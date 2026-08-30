"""百度千帆智能 PPT 工具 API 封装（D4 主方案，2026-08-15 实证）。

接口：POST {BAIDU_PPT_BASE_URL}/v2/tools/ai_ppt/generate_ppt_by_outline
鉴权：Authorization: Bearer <bce-v3 密钥>（settings.BAIDU_PPT_API_KEY）
响应：SSE 流式事件，status 流转：PPT生成中×N → PPT生成结束 → PPT导出中 → PPT导出结束
     （PPT导出结束 的 data.pptx_url 为最终文件下载地址）

实测要点（scripts/test_ppt_api.py 验证）：
- resource_url/custom_tpl_url 传空字符串会被拒绝（"resource_url not valid"），不传即可；
- query_id/chat_id 自编号即可（文档称与大纲生成接口有依赖，实测 1/1 不影响效果）；
- 一次调用 13 页约 20s；产物 11.7MB（含设计配图）。

设计（P04 模板映射）：百度 style_id/tpl_id 为数值模板 ID，项目内 template 名
（business/简约/科技）映射到百度模板 ID，见 TEMPLATE_MAP。
"""
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)

# 百度模板 ID 映射（P04：商务/简约/科技 → style_id/tpl_id）
# 102322 为官方示例商务模板（已实测）；其余为预留，待模板接口确认后补
TEMPLATE_MAP: dict[str, dict] = {
    "business": {"style_id": 0, "tpl_id": 102322, "label": "商务"},
    "simple": {"style_id": 0, "tpl_id": 102322, "label": "简约"},  # TODO: 待模板接口确认
    "tech": {"style_id": 0, "tpl_id": 102322, "label": "科技"},    # TODO: 待模板接口确认
}
DEFAULT_TEMPLATE = "business"
PPT_GEN_TIMEOUT = 120  # 生成+导出全流程（实测 20s，留足余量）


class PptBaiduError(RuntimeError):
    """百度 PPT API 调用异常（业务错误/网络失败统一封装）。"""


def template_to_ids(template: str) -> dict:
    """模板名 → 百度 (style_id, tpl_id)。未知模板回退商务。"""
    return TEMPLATE_MAP.get(template, TEMPLATE_MAP[DEFAULT_TEMPLATE])


def outline_to_baidu(outline: dict) -> str:
    """项目大纲 JSON → 百度 Markdown 大纲格式。

    百度格式约束（官方文档）：
    - 一级标题：`# 空格`（对应 PPT 封面后的章节页）
    - 二级标题：`* 空格`（章节内小节）
    - 三级标题：`两个空格 + * + 空格`（小节要点）
    至少一个一级标题；格式小问题百度会尝试自动修复。

    项目大纲结构（prompts.yml ppt_outline）：
        {"title": "...", "subtitle": "...", "sections": [{"name": "...", "points": ["..."]}]}
    """
    title = (outline.get("title") or "").strip()
    sections = outline.get("sections") or []
    if not title and not sections:
        raise PptBaiduError("大纲为空：缺少 title/sections")

    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
    for sec in sections:
        name = (sec.get("name") or "").strip()
        if not name:
            continue
        lines.append(f"* {name}")
        for pt in (sec.get("points") or [])[:5]:  # 每页要点 ≤5（任务书 2.8）
            pt = (pt or "").strip()
            if pt:
                lines.append(f"  * {pt}")
    if len(lines) < 2:
        raise PptBaiduError("大纲内容不足：至少需要一个章节")
    return "\n".join(lines)


def generate_ppt(outline: dict, topic: str, template: str = DEFAULT_TEMPLATE) -> tuple[str, int]:
    """调用百度生成 PPT，返回 (pptx 下载 URL, 页数)。

    Args:
        outline: 项目大纲 JSON（title/sections）。
        topic: PPT 标题（query/title 字段）。
        template: 模板名（business/simple/tech），映射百度模板 ID。

    Returns:
        (pptx_url, page_count)。

    Raises:
        PptBaiduError: 配置缺失 / 网络失败 / API 业务错误 / 导出未完成。
    """
    if not settings.BAIDU_PPT_API_KEY or not settings.BAIDU_PPT_BASE_URL:
        raise PptBaiduError(
            "百度 PPT API 未配置：请在 .env 填写 BAIDU_PPT_API_KEY / BAIDU_PPT_BASE_URL"
        )

    url = settings.BAIDU_PPT_BASE_URL.rstrip("/") + "/v2/tools/ai_ppt/generate_ppt_by_outline"
    tpl = template_to_ids(template)
    body = {
        "query_id": 1,
        "chat_id": 1,
        "outline": outline_to_baidu(outline),
        "query": topic,
        "title": topic,
        "style_id": tpl["style_id"],
        "tpl_id": tpl["tpl_id"],
        "gen_mode": 1,   # 1=智能润色（P05 内容填充由百度完成）
        "ai_info": False,
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + settings.BAIDU_PPT_API_KEY)
    req.add_header("Content-Type", "application/json")

    logger.info("百度 PPT 生成: topic=%s template=%s tpl_id=%s", topic, template, tpl["tpl_id"])
    try:
        # 禁用代理：本机 7897 代理会劫持 HTTPS 请求导致 SSL EOF（实测 2026-08-15），
        # 百度是国内服务应直连（同政务站采集 trust_env=False 的经验）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        resp = opener.open(req, timeout=PPT_GEN_TIMEOUT)
        raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise PptBaiduError(f"百度 API HTTP {exc.code}: {detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise PptBaiduError(f"百度 API 请求失败: {exc}") from exc

    pages: list[str] = []
    pptx_url: str | None = None
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            d = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        status = d.get("status", "")
        if status == "PPT生成中" and d.get("bd_ppt_json"):
            pages.append(d["bd_ppt_json"])
        elif status == "PPT导出结束":
            data_obj = d.get("data") or {}
            pptx_url = data_obj.get("pptx_url")
        elif d.get("errno") not in (None, 0):
            raise PptBaiduError(
                f"百度 API 错误 errno={d.get('errno')}: {d.get('show_msg') or d.get('error')}"
            )

    if not pages:
        raise PptBaiduError("百度 API 未返回任何 PPT 页面数据")
    if not pptx_url:
        raise PptBaiduError("百度 API 导出未完成（缺少 pptx_url）")
    return pptx_url, len(pages)


def download_pptx(url: str, dest: Path) -> Path:
    """下载 pptx 文件到 dest，返回 dest（校验 zip 结构）。

    Raises:
        PptBaiduError: 下载失败或文件不是合法 pptx（zip）。
    """
    import zipfile

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 同 generate_ppt：禁代理直连
        with opener.open(req, timeout=120) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as exc:  # noqa: BLE001
        raise PptBaiduError(f"pptx 下载失败: {exc}") from exc

    # 完整性校验：合法 pptx 必须是 zip 且含 slide XML
    try:
        with zipfile.ZipFile(dest) as z:
            slides = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        if not slides:
            raise PptBaiduError("下载文件不是合法 pptx（无幻灯片）")
    except zipfile.BadZipFile as exc:
        raise PptBaiduError(f"下载文件损坏（非 zip）: {exc}") from exc
    return dest
