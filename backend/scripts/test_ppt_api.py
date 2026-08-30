#!/usr/bin/env python
"""百度千帆智能 PPT 工具 API 验证脚本（D4 主方案实证）。

调用 POST /v2/tools/ai_ppt/generate_ppt_by_outline（SSE 流式），
解析完整事件流（PPT生成中×N → 生成结束 → 导出中 → 导出结束），
下载最终 .pptx 并校验完整性。

用法（在 backend/ 目录下，全局 Python）：
    PYTHONPATH= C:\\python\\python.exe scripts/test_ppt_api.py
    PYTHONPATH= C:\\python\\python.exe scripts/test_ppt_api.py "临时用电安全"   # 自定义主题

依赖 .env 的 BAIDU_PPT_API_KEY / BAIDU_PPT_BASE_URL（bce-v3 密钥，每日 10 次免费）。
输出：docs/ppt_demo/AI生成_<主题>_百度API.pptx（与自研 gen_ppt.py 产物同目录便于对比）
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.request  # noqa: E402

from src.core.config import settings  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "ppt_demo"
DEFAULT_TOPIC = "施工现场高处作业安全"

# 模板 ID（从百度官方示例文档获取：style_id=0 通用样式，tpl_id=102322 商务模板）
STYLE_ID = 0
TPL_ID = 102322


def build_outline(topic: str) -> str:
    """按百度大纲格式约束构造 outline（# 一级 / * 二级 / 两个空格+* 三级）。

    格式要求（官方文档）：
    - 一级标题：`# 空格`；二级：`* 空格`；三级：`两个空格 + * + 空格`
    - 至少包含一个一级标题；系统会尝试自动修复小格式问题
    """
    return (
        f"# {topic}\n"
        f"* 安全风险概述\n"
        f"  * 主要危险源辨识\n"
        f"  * 事故后果分析\n"
        f"* 安全防护措施\n"
        f"  * 个人防护装备要求\n"
        f"  * 现场作业安全规范\n"
        f"  * 应急处理流程\n"
        f"* 管理要求与建议\n"
        f"  * 安全培训与交底\n"
        f"  * 监督检查机制\n"
    )


def call_generate(topic: str) -> dict:
    """调用生成接口，返回 {'pages': [bd_ppt_json...], 'pptx_url': str}。"""
    url = settings.BAIDU_PPT_BASE_URL + "/v2/tools/ai_ppt/generate_ppt_by_outline"
    if not settings.BAIDU_PPT_API_KEY or not settings.BAIDU_PPT_BASE_URL:
        raise RuntimeError("BAIDU_PPT_API_KEY / BAIDU_PPT_BASE_URL 未配置（.env）")

    body = {
        "query_id": 1,
        "chat_id": 1,
        "outline": build_outline(topic),
        "query": topic,
        "title": topic,
        "style_id": STYLE_ID,
        "tpl_id": TPL_ID,
        # 注意：resource_url/custom_tpl_url 传空字符串会被服务端拒绝（实测 InternalError），
        # 不传这两个字段即可。
        "gen_mode": 1,   # 1=智能润色（对大纲内容做润色），2=严格依从
        "ai_info": False,
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + settings.BAIDU_PPT_API_KEY)
    req.add_header("Content-Type", "application/json")

    print(f"POST {url}")
    print(f"主题: {topic} ｜ style_id={STYLE_ID} tpl_id={TPL_ID} gen_mode=1（智能润色）")
    resp = urllib.request.urlopen(req, timeout=120)
    raw = resp.read().decode("utf-8", errors="replace")
    print(f"HTTP {resp.status} | 响应 {len(raw) / 1024:.0f} KB\n")

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
            print(f"  [生成中] 第 {len(pages)} 页数据（bd_ppt_json {len(d['bd_ppt_json']) // 1024} KB）")
        elif status == "PPT生成结束":
            print("  [生成结束] 全部页面数据已就绪")
        elif status == "PPT导出中":
            print("  [导出中] 正在渲染 pptx...")
        elif status == "PPT导出结束":
            data_obj = d.get("data") or {}
            pptx_url = data_obj.get("pptx_url")
            print(f"  [导出结束] pptx_url 已返回")
        elif d.get("errno") not in (None, 0):
            raise RuntimeError(f"API 错误 errno={d.get('errno')}: {d.get('show_msg') or d.get('error')}")

    if not pages:
        raise RuntimeError("未收到任何 PPT 页面数据（bd_ppt_json 为空）")
    if not pptx_url:
        raise RuntimeError("未收到 pptx_url（导出未完成）")
    return {"pages": pages, "pptx_url": pptx_url}


def download_pptx(url: str, topic: str) -> Path:
    """下载最终 pptx 文件，返回本地路径。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", topic)
    out = OUT_DIR / f"AI生成_{safe}_百度API.pptx"
    print(f"\n下载 pptx: {url[:80]}...")
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as resp, open(out, "wb") as f:
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    print(f"已保存: {out}（{total / 1024:.0f} KB）")
    return out


def verify_pptx(path: Path) -> None:
    """校验 pptx 完整性：zip 结构 + 页数。"""
    import zipfile

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        slide_files = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        print(f"完整性校验: zip 条目 {len(names)} 个 | 幻灯片 {len(slide_files)} 页 ✅")
        if not slide_files:
            raise RuntimeError("pptx 内无幻灯片文件，文件损坏")


def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TOPIC
    result = call_generate(topic)
    print(f"\n=== 生成完成: {len(result['pages'])} 页数据 ===")
    out = download_pptx(result["pptx_url"], topic)
    verify_pptx(out)
    print(f"\n✅ 验证通过，产物: {out}")
    print("（可用 python-pptx 或 PowerPoint 打开查看效果）")


if __name__ == "__main__":
    main()
