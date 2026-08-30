"""图片隐患识别（视觉子智能体 qwen3.7-plus，LangChain ChatOpenAI 多模态）。

设计（AI_SOLUTION §1.1）：
- 复用 prompt/prompts.yml 的 hazard_vision 场景（四段式，强制 JSON，含 location 坐标字段）；
- LangChain ChatOpenAI 多模态调用（OpenAI 兼容，content 含 text + image_url data URI）；
- qwen 系实测具备目标检测/坐标输出能力（公众号实测 + 领导小组实测），
  无法精确定位时模型输出 null（prompt 约束防编造）；
- QWEN_API_KEY/QWEN_BASE_URL 未配置时给出清晰报错（503）。

坐标错位修复（2026-08-17 用户反馈）：
- 根因：视觉模型内部会把图片缩放到固定尺寸（如长边 1024）再推理，
  返回坐标相对「模型内部看到的缩放尺寸」，直接画在原图上必然错位；
- 修复：识别前主动等比缩放到长边 MAX_SIDE（1024），prompt 明确告知缩放后尺寸，
  模型返回坐标即相对缩放图；画框时按 scale_x/scale_y 还原回原图坐标。

用法：
    from src.ai.vision import vision_client
    result = vision_client.analyze_hazard(image_bytes, scene_description="")
    # -> {"detected": bool, "hazards": [...], "summary": "...", "_scale": (sx, sy)}
"""
import base64
import io
import json
import logging

from langchain_openai import ChatOpenAI

from src.ai.prompts import render
from src.core.config import settings

logger = logging.getLogger(__name__)

MAX_SIDE = 1024  # 模型内部固定 1024 长边系（校准实测：输入 2048 被内部缩回 1024，坐标反而错位 +0.45；1024 最优）


class VisionClient:
    def __init__(self) -> None:
        self._llm: ChatOpenAI | None = None

    def _ensure_configured(self) -> ChatOpenAI:
        if not settings.QWEN_API_KEY or not settings.QWEN_BASE_URL:
            raise RuntimeError(
                "qwen3.7-plus 视觉未配置：请在 .env 填写 QWEN_API_KEY / QWEN_BASE_URL"
            )
        if self._llm is None:
            self._llm = ChatOpenAI(
                model=settings.QWEN_MODEL,
                api_key=settings.QWEN_API_KEY,
                base_url=settings.QWEN_BASE_URL,
                timeout=180,  # MaaS 专属实例冷启动慢（实测 ~180s），不能沿用通用 LLM_TIMEOUT=60
                max_retries=1,  # 冷启动重试意义不大，一次失败即报错让前端提示
                temperature=0.2,
                # 注意：qwen3.7-plus 是推理模型，思考(reasoning)会先消耗 tokens；
                # max_tokens 太小会全被思考吃掉导致 content 为空（实测 finish_reason=length）
                max_tokens=4096,
            )
        return self._llm

    @staticmethod
    def _resize_for_model(image_bytes: bytes) -> tuple[bytes, int, int, float, float]:
        """等比缩放到长边 ≤MAX_SIDE，返回 (缩放图字节, 缩放宽, 缩放高, scale_x, scale_y)。

        scale_x = 原图宽/缩放宽（画框时坐标 x × scale_x 还原原图坐标）。
        """
        from PIL import Image  # lazy

        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        w0, h0 = img.size
        # 统一长边=MAX_SIDE（小图也放大）：校准实测(2026-08-17)模型内部按 1024 长边系推理——
        # 小图(<1024)若不放大直接送，模型内部放大后返回 1024 系坐标，按原尺寸理解必然错位(B图偏差+0.18)
        ratio = MAX_SIDE / max(w0, h0)
        new_w, new_h = round(w0 * ratio), round(h0 * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        scale_x, scale_y = w0 / new_w, h0 / new_h
        logger.info(
            "视觉图缩放: %dx%d → %dx%d (scale=%.3f, %.3f)", w0, h0, new_w, new_h, scale_x, scale_y
        )
        return buf.getvalue(), new_w, new_h, scale_x, scale_y

    def analyze_hazard(
        self, image_bytes: bytes, scene_description: str = ""
    ) -> dict:
        """分析隐患图片，返回结构化 JSON（detected/hazards/summary）。

        hazards 元素：{category, description, level, suggestion, location}
        location 坐标为**缩放后图片**的像素坐标；响应附带 `_scale`=(scale_x, scale_y)
        供画框方还原到原图坐标（见 src/routers/vision.py / src/utils/boxes.py）。
        """
        llm = self._ensure_configured()

        # 坐标错位修复：先等比缩放再送模型，prompt 告知缩放后尺寸
        resized, rw, rh, sx, sy = self._resize_for_model(image_bytes)
        prompt = render(
            "hazard_vision",
            scene_description=scene_description or "（无）",
            image_width=rw,
            image_height=rh,
        )

        image_b64 = base64.b64encode(resized).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                ],
            }
        ]
        resp = llm.invoke(messages)
        content = resp.content if hasattr(resp, "content") else str(resp)
        result = self._parse_json(content or "")
        result["_scale"] = (sx, sy)
        return result

    @staticmethod
    def _parse_json(content: str) -> dict:
        """宽容解析：剥离 ```json 围栏与前后杂质。"""
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise RuntimeError(f"视觉模型未返回 JSON: {content[:200]}")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"视觉模型 JSON 解析失败: {exc}") from exc


vision_client = VisionClient()
