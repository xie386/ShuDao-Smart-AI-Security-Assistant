"""PPT Agent 的"审图"视觉通道（v2.0 大点 02）。

定位：**不接主模型**。主模型（`llm_client`）是纯文本，看图要走专门的多模态段
`PPT_VISION_*`（默认 AGNES，`PPT_VISION_PROVIDER` 预留给以后接别的视觉模型）。

实测要记住的坑（2026-10-23，research 话题 6.2）：
- AGNES 能正确读图（测试图里的编号与颜色都答对）；
- 但它在 `message.content` 之前先输出 `reasoning_content`（180~215 字符），
  `max_tokens=40` 时会被思考内容吃光 → `finish_reason=length` 且 `content` 为空。
  所以这里固定放宽 `PPT_VISION_MAX_TOKENS`（默认 800），并且只取 `content`。

对外接口：
    from src.ai.vision_agent import review_page, available
    verdict = review_page(png_path, page_spec)
    # -> {"ok": bool, "problems": [...], "fix_hints": [...], "skipped": 原因或 None}
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)

_REVIEW_PROMPT = """你是资深 PPT 版式审查员。看这一页幻灯片的渲染图，判断它能否直接交付。

本页应当表达的内容（供你核对，不要评价内容本身写得对不对）：
{page_desc}

只检查这四类**版式问题**：
1. overflow：文字超出文本框或被裁切、行尾挤到边缘；
2. overlap：图片与文字互相遮挡，或元素压住标题栏；
3. contrast：前景色与背景色对比过低，字看不清；
4. missing：本页应有的要点/图片缺失（与上面「应当表达的内容」对照，数量明显不符）。

输出要求：**只输出 JSON**，不要解释：
{{"ok": true/false,
  "problems": [{{"type": "overflow|overlap|contrast|missing", "detail": "一句话"}}],
  "fix_hints": ["可执行的修法，例如：把要点从 6 条减到 4 条；字号从 20 降到 18"]}}
没有问题就返回 {{"ok": true, "problems": [], "fix_hints": []}}。"""


def _cfg() -> tuple[str, str, str]:
    """(base_url, api_key, model) —— PPT 段优先，未配 key 时回退到 AGNES 段。"""
    base = (settings.PPT_VISION_BASE_URL or settings.AGNES_BASE_URL or "").rstrip("/")
    key = settings.PPT_VISION_API_KEY or settings.AGNES_API_KEY
    model = settings.PPT_VISION_MODEL or settings.AGNES_MODEL
    return base, key, model


def available() -> bool:
    base, key, model = _cfg()
    return bool(base and key and model)


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _describe(page_spec: dict) -> str:
    lines = [
        f"- 页面类型：{page_spec.get('type', 'content')}",
        f"- 标题：{page_spec.get('title', '')}",
    ]
    bullets = page_spec.get("bullets") or []
    if bullets:
        lines.append(f"- 应有关点 {len(bullets)} 条：" + "；".join(str(b)[:40] for b in bullets[:8]))
    if page_spec.get("image"):
        lines.append("- 本页应有一张配图")
    return "\n".join(lines)


def review_page(png_path: str | Path, page_spec: dict) -> dict:
    """看图判定一页版式；任何异常都返回 ok=True + skipped，绝不阻断生成。"""
    png = Path(png_path)
    if not settings.PPT_VISION_REVIEW:
        return {"ok": True, "problems": [], "fix_hints": [], "skipped": "vision review disabled"}
    if not available():
        return {"ok": True, "problems": [], "fix_hints": [], "skipped": "vision model not configured"}
    if not png.is_file():
        return {"ok": True, "problems": [], "fix_hints": [], "skipped": "screenshot missing"}

    try:
        from openai import OpenAI  # lazy

        base, key, model = _cfg()
        client = OpenAI(base_url=base, api_key=key, timeout=settings.PPT_VISION_TIMEOUT, max_retries=0)
        b64 = base64.b64encode(png.read_bytes()).decode()
        resp = client.chat.completions.create(
            model=model,
            max_tokens=settings.PPT_VISION_MAX_TOKENS,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _REVIEW_PROMPT.format(page_desc=_describe(page_spec))},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                    ],
                }
            ],
        )
        msg = resp.choices[0].message
        content = (getattr(msg, "content", "") or "").strip()
        data = _parse_json(content)
        if not data:
            return {
                "ok": True,
                "problems": [],
                "fix_hints": [],
                "skipped": f"unparsable vision output: {content[:80]}",
            }
        problems = data.get("problems") if isinstance(data.get("problems"), list) else []
        hints = data.get("fix_hints") if isinstance(data.get("fix_hints"), list) else []
        return {
            "ok": bool(data.get("ok")) and not problems,
            "problems": problems[:6],
            "fix_hints": [str(h)[:120] for h in hints][:4],
            "skipped": None,
        }
    except Exception as exc:  # noqa: BLE001 —— 审图失败不能拖垮生成
        logger.warning("PPT 审图失败(%s): %s", png.name, exc)
        return {"ok": True, "problems": [], "fix_hints": [], "skipped": f"{type(exc).__name__}: {exc}"[:120]}
