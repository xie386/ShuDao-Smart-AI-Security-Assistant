#!/usr/bin/env python
"""HTML 知识源治理：正文抽取 + 清洗 → 同目录同名 .md。

用法（在 backend/ 目录下，使用全局 Python）：
    PYTHONPATH= C:\\python\\python.exe scripts/govern_html.py                 # 全量治理四类目录
    PYTHONPATH= C:\\python\\python.exe scripts/govern_html.py --doc-type laws # 只治理 laws
    PYTHONPATH= C:\\python\\python.exe scripts/govern_html.py --dry-run       # 只统计不写文件

策略：
- 正文容器优先级：id=UCAP-CONTENT > class=pages_content > TRS_UEDITOR > trs_editor_view
  （政务站 TRS/拓尔思 CMS 通用容器；实测 gov.cn / hcq.gov.cn 均命中）
- 清洗：去 script/style/导航（首页/收藏/打印/字号/无障碍/面包屑）；标题去站点后缀
- 结构保留：h1-h3 → #/##/###，p → 段落，li → 列表（利于 D3 结构感知分块）
- 输出：<source_id>.md 与源 .htm 同目录；kb_manifest.jsonl 回填 title/提取状态/字数
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent.parent / "knowledge_src"
MANIFEST = BASE / "kb_manifest.jsonl"
DOC_TYPES = ["laws", "cases", "plans", "procedures"]

# 导航/噪音词（命中则剔除该片段）
# 注意：单字词（简/繁/EN 等）只精确匹配整段，防止误杀正文里的"简化/简明"等正常词（实测踩坑）
NOISE_WORDS = [
    "首页", "收藏", "打印", "字号", "无障碍", "长者助手", "个人中心", "退出",
    "网站无障碍", "默认", "超大", "简", "繁", "EN", "登录", "注册", "邮箱",
    "中国政府网", "国务院部门文件", "政策文件库", "当前位置", "您当前的位置",
    "网站支持IPV6", "走进惠城", "政务公开", "政务服务", "政民互动", "部门镇街频道",
    "网站地图", "联系我们", "设为首页", "加入收藏", "分享到",
    "粤公网安备", "网站标识码", "ICP备", "备案号", "邮编", "主办单位", "版权所有",
    # —— 安全管理网（safehoo.com，D6 批量采集源）站点噪声 ——
    "安全管理网", "会员中心", "加入VIP", "客服微信", "网站公众号", "收藏本页",
    "上一篇", "下一篇", "相关文章", "推荐资料", "热门排行", "本文来源",
    "扫一扫", "手机版", "投稿", "广告合作", "免责声明", "转载请注明",
    "评论：", "点击数", "更新时间", "文档大小", "资料下载",
]
# 单字/极短噪声词：仅整段精确匹配时剔除（防误杀正文）
_EXACT_NOISE = {"简", "繁", "EN", "邮箱", "默认", "超大", "登录", "注册"}

# —— 安全管理网侧栏/顶栏菜单项（整段精确匹配才剔除，防误杀正文同名词）——
# 背景（D6 实测）：部分详情页没有 pagecontent 容器，治理走"全页 p/h/li 兜底"，
# 会把频道导航（教育/规程/预案/评价…）和「推荐/热门」列表整段带进正文。
_NAV_ITEMS = {
    "教育", "规程", "预案", "评价", "工伤", "职业卫生", "环保", "健康", "体系",
    "文档", "论文", "常识", "工程师", "文艺", "视频", "标准", "法规", "案例",
    "管理", "技术", "培训", "考试", "试题", "课件", "资料", "图库", "专题",
    "首页", "更多", "下载", "在线阅读", "全部", "推荐", "热门", "最新", "排行",
}

# 列表项噪声模式：导航/推荐条目的形态特征（非正文语句）
_LIST_NOISE_RE = re.compile(
    r"^(?:"
    r"\d{1,2}-\d{1,2}\s"          # "11-27 2025年度中央安全生产…"（日期前缀推荐位）
    r"|\d{4,}\s"                   # "154418 安全生产检查内容"（点击数前缀热门位）
    r"|\d{4}-\d{1,2}-\d{1,2}\s*$"  # 纯日期行
    r")"
)


def is_noise(text: str) -> bool:
    """噪声判定：长词子串匹配；单字/短词与导航菜单项整段精确匹配。"""
    t = text.strip()
    if t in _EXACT_NOISE or t in _NAV_ITEMS:
        return True
    if _LIST_NOISE_RE.match(t):
        return True
    # 以省略号结尾的短条目 = 被截断的推荐标题（正文句子不会这样结尾）
    if len(t) <= 30 and t.endswith(("…", "...", "···")):
        return True
    for n in NOISE_WORDS:
        if n in _EXACT_NOISE:
            if t == n:
                return True
        elif n in t:
            return True
    return False

# 正文容器选择器（按优先级）
CONTENT_SELECTORS = [
    {"id": "UCAP-CONTENT"},
    {"class": "pages_content"},
    {"class": "TRS_UEDITOR"},
    {"class": "trs_editor_view"},
    # —— 安全管理网 safehoo.com（D6 批量采集源）：正文在 id=pagecontent / class=con_zw ——
    # 命中后可直接跳过页头会员中心/客服微信等导航区（比噪声词过滤更彻底）
    {"id": "pagecontent"},
    {"class": "con_zw"},
]

# 「文档下载页」正文预览容器（D6 实测关键发现）：
# 部分资料页（规程/检查表/制度模板）本体是 WORD 附件下载页，网页正文区只有
# 「点击数/更新时间/文件大小/下载点数」等元信息，但站点会把**全文预览**
# 放在 <div class="con_pl"><textarea>…</textarea></div> 里（实测 4558 字完整正文）。
# 这类页若只看 p/li 会被判成空壳丢弃，从 textarea 取回可救回大量有效文档。
PREVIEW_SELECTORS = [
    {"class": "con_pl"},
]


def pick_container(soup):
    """返回正文容器节点或 None（单容器，兼容旧调用）。"""
    for sel in CONTENT_SELECTORS:
        node = soup.find(**sel) if "id" in sel else soup.find(class_=sel["class"])
        if node:
            return node
    return None


def pick_containers(soup) -> list:
    """返回**同一优先级下的全部**正文容器节点（列表，可能为空）。

    为什么要多容器：D6 批量采集的详情页正文可能分页（`_2.shtml`、`_3.shtml`…），
    `kb_crawl_common.fetch_detail()` 把各页 HTML 拼接成一个文件，
    因此同一文件里会出现**多个** `id=pagecontent` 容器。
    只 `find()` 第一个会丢掉后续所有分页正文（实测长报告被截断），
    故按第一个命中的选择器把该选择器的所有节点全部取出、按文档顺序拼接。
    """
    for sel in CONTENT_SELECTORS:
        if "id" in sel:
            # id 理论上唯一，但分页拼接后会重复出现 → 用 find_all 兜住
            nodes = soup.find_all(id=sel["id"])
        else:
            nodes = soup.find_all(class_=sel["class"])
        if nodes:
            return nodes
    return []


def clean_title(raw_title: str) -> str:
    """清洗 <title>：去掉站点后缀（_xxx_yyy 部分），保留主标题。"""
    t = re.split(r"[_|｜\-]", raw_title)[0].strip()
    return t or "未命名文档"


def node_to_md(node) -> list[str]:
    """把容器节点转换为 md 行：标题保留层级、段落/列表成行。"""
    lines: list[str] = []
    for el in node.descendants:  # noqa: PLC0206
        if el.name in ("h1", "h2", "h3", "h4"):
            text = el.get_text(" ", strip=True)
            if text:
                lines.append(f"{'#' * int(el.name[1])} {text}")
        elif el.name == "p":
            text = el.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            if text and not is_noise(text):
                lines.append(text)
        elif el.name == "li":
            text = el.get_text(" ", strip=True)
            if text and not is_noise(text):
                lines.append(f"- {text}")
    return lines


def extract_preview_md(soup) -> list[str]:
    """从「文档下载页」的 textarea 全文预览中取正文行（见 PREVIEW_SELECTORS 说明）。

    textarea 里是纯文本（WORD 转出的预览），按空行切段即可；
    首行常是「仅提取页面文字内容，供快速阅读使用。」的提示语，需剔除。
    """
    for sel in PREVIEW_SELECTORS:
        holder = soup.find(class_=sel["class"])
        if not holder:
            continue
        ta = holder.find("textarea")
        if not ta:
            continue
        raw_text = ta.get_text("\n", strip=True)
        lines: list[str] = []
        for seg in re.split(r"\n+", raw_text):
            seg = re.sub(r"\s+", " ", seg).strip()
            if not seg:
                continue
            if "仅提取页面文字内容" in seg or "供快速阅读使用" in seg:
                continue
            if is_noise(seg):
                continue
            lines.append(seg)
        if lines:
            return lines
    return []


def extract_md(path: Path) -> tuple[str, str, int]:
    """返回 (标题, md内容, 正文字数)。"""
    raw = path.read_bytes().decode("utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "html.parser")
    title = clean_title(soup.title.string if soup.title and soup.title.string else "")

    # 多容器优先（分页拼接的文件有多个 pagecontent）；都没命中则全页兜底
    containers = pick_containers(soup)
    if containers:
        lines: list[str] = []
        for node in containers:
            lines.extend(node_to_md(node))
    else:
        lines = node_to_md(soup)

    # 去空行 + 去相邻重复行（分页页头会重复文章标题）
    body: list[str] = []
    for ln in lines:
        if not ln.strip():
            continue
        if body and ln == body[-1]:
            continue
        body.append(ln)
    # 去掉与标题重复的一级标题行（正文首行常再写一遍标题）
    body = [ln for ln in body if ln.lstrip("# ").strip() != title.strip()]

    chars = sum(len(re.sub(r"[#\- ]", "", ln)) for ln in body)

    # 正文过短 → 尝试「文档下载页」的 textarea 全文预览（D6：救回附件型资料页）
    if chars < 500:
        preview = extract_preview_md(soup)
        p_chars = sum(len(re.sub(r"[#\- ]", "", ln)) for ln in preview)
        if p_chars > chars:
            body, chars = preview, p_chars

    text = "\n\n".join(body)
    return title, f"# {title}\n\n{text}\n", chars


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-type", default="", help="只治理指定目录（laws/cases/plans/procedures）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = ap.parse_args()

    types = [args.doc_type] if args.doc_type else DOC_TYPES
    manifest = {r["source_id"]: r for r in
                (json.loads(l) for l in MANIFEST.open(encoding="utf-8") if l.strip())}

    total = converted = failed = 0
    for dt in types:
        d = BASE / dt
        if not d.is_dir():
            print(f"[跳过] 目录不存在: {dt}")
            continue
        for f in sorted(d.glob("*.htm")):
            total += 1
            try:
                title, md, chars = extract_md(f)
                # 阈值 2000 字：政务站"通知头+附件正文"页面常见（网页正文不足时，正文在
                # 附件 docx/pdf 中，需人工确认附件是否已抓取/转换）
                if chars < 2000:
                    print(f"[可疑] {f.name}: 正文字数仅 {chars}（可能正文在附件或结构特殊，仍需人工确认）")
                if not args.dry_run:
                    f.with_suffix(".md").write_text(md, encoding="utf-8")
                if f.stem in manifest:
                    manifest[f.stem].update(
                        title=title,
                        extract_status="ok" if chars >= 2000 else "suspect",
                        extract_chars=chars,
                        md_path=f"{dt}/{f.stem}.md",
                    )
                print(f"[{'DRY' if args.dry_run else 'OK '}] {dt}/{f.name}: {chars} 字 | {title[:30]}")
                converted += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[FAIL] {dt}/{f.name}: {type(exc).__name__} {str(exc)[:80]}")
                failed += 1

    if not args.dry_run:
        with MANIFEST.open("w", encoding="utf-8") as fh:
            for r in manifest.values():
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n已回填 manifest（{len(manifest)} 条）")
    print(f"\n治理统计: 处理 {total} / 转换 {converted} / 失败 {failed}")


if __name__ == "__main__":
    main()
