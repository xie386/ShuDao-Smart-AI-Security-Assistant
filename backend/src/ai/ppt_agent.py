"""PPT 生成 Agent（v2.0 大点 02，默认链路）。

编排形态（裁定 3，混合式）：
    **代码决定走哪些阶段**（S0–S8，进度条与日志都对齐它），
    **Agent 决定每一页写什么**（标题/要点/配图意图/版式类型）。

阶段与进度权重（与 `v2.0规划文档.md` §02 §3.2 一致）：

    S0 预处理      3%    S3 缺口补检  10%    S6 逐页制作+自检 40%
    S1 知识库检索  7%    S4 大纲策划  15%    S7 收尾           5%
    S2 充分性判定  5%    S5 素材采集  10%    S8 落盘           5%

对外只有一个入口：

    from src.ai.ppt_agent import run
    for event in run(spec, user_id=17, task_id=23):
        ...  # 事件见 `_emit()` 的字段说明

约定：
- 事件是**纯 dict**，SSE 层直接 `json.dumps` 下发；日志文件与 SSE 同源（同一批事件）；
- 任何单页失败都不中断整单：图片拿不到 → 无图版式；审图超时 → 跳过判定；
- 唯一写者：目标 pptx 只由 OfficeCLI 写（裁定 8）。
"""
from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from pathlib import Path

from src.ai import ppt_layout, ppt_tools, vision_agent
from src.core.config import settings
from src.core.paths import ensure_dir, log_path, out_path, task_dir

logger = logging.getLogger(__name__)

STAGES = {
    "S0": ("问题预处理", 3),
    "S1": ("知识库检索", 7),
    "S2": ("充分性判定", 5),
    "S3": ("缺口补检", 10),
    "S4": ("大纲策划", 15),
    "S5": ("素材采集", 10),
    "S6": ("逐页制作与自检", 40),
    "S7": ("收尾", 5),
    "S8": ("落盘", 5),
}
_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]


def _pct(stage: str, within: float = 0.0) -> int:
    """阶段基线的累计百分比 + 阶段内完成度（within: 0~1）。"""
    total = 0
    for key in _ORDER:
        if key == stage:
            return min(99, int(total + STAGES[key][1] * max(0.0, min(1.0, within))))
        total += STAGES[key][1]
    return 99


PLAN_PROMPT = """你是企业安全培训课件策划。根据主题、用户要求与资料，规划一份中文 PPT。

硬性要求：
- 章节 sections 数量 = {n_sections} 章，每章 1~3 个内容页；内容页合计必须 = {n_content} 页；
- 每个内容页 4~6 条要点，每条 ≤ 40 字，一句话说清一件事；
- **优先使用资料中的事实、要求与做法**；资料没有的不要编造具体数字、条款号、日期；
- 需要配图的页面给 `image_query`（英文 2~4 个词，例如 "tunnel construction worker"），不需要就不给；
- 页面标题 ≤ 14 字。

只输出 JSON，不要解释、不要代码块：
{{"title": "≤20字的主标题", "subtitle": "副标题（可空）",
  "sections": [
    {{"name": "章节名（≤12字）",
      "pages": [{{"title": "页面标题", "bullets": ["要点1", "要点2", "要点3"], "image_query": ""}}]}}
  ]}}

主题：{topic}
用户要求：{requirements}
资料（可能不完整）：
{material}
"""


# --------------------------------------------------------------------------- #
# 主题色（用户模板）
# --------------------------------------------------------------------------- #
def read_template_theme(pptx: str | Path) -> dict | None:
    """从用户模板的 `ppt/theme/theme1.xml` 里读主色/强调色（读失败返回 None）。"""
    path = Path(pptx)
    if not path.is_file():
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if re.match(r"ppt/theme/theme\d+\.xml$", n)]
            if not names:
                return None
            xml = zf.read(sorted(names)[0]).decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取模板主题失败(%s): %s", path.name, exc)
        return None

    def _color(tag: str) -> str | None:
        m = re.search(rf"<a:{tag}>(.*?)</a:{tag}>", xml, flags=re.S)
        if not m:
            return None
        inner = m.group(1)
        srgb = re.search(r'srgbClr val="([0-9A-Fa-f]{6})"', inner)
        if srgb:
            return "#" + srgb.group(1).upper()
        sys = re.search(r'lastClr="([0-9A-Fa-f]{6})"', inner)
        if sys:
            return "#" + sys.group(1).upper()
        return None

    primary = _color("dk2") or _color("accent1")
    accent = _color("accent1")
    if not primary:
        return None
    return {
        "primary": primary,
        "accent": accent or ppt_layout.DEFAULT_THEME["accent"],
        "text": _color("dk1") or ppt_layout.DEFAULT_THEME["text"],
        "font": ppt_layout.DEFAULT_THEME["font"],
    }


# --------------------------------------------------------------------------- #
# 规划
# --------------------------------------------------------------------------- #
def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    if "```" in t:
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        obj = json.loads(t[i : j + 1])
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _fallback_plan(spec: dict, material: str) -> dict:
    """LLM 规划失败时的确定性兜底：把资料按段落切成章节与页面。"""
    topic = (spec.get("topic") or "安全培训").strip()
    paras = [p.strip() for p in re.split(r"\n{2,}", material or "") if p.strip()]
    sentences: list[str] = []
    for p in paras[:40]:
        for s in re.split(r"(?<=[。；!?])", p):
            s = s.strip(" 　-·•\t")
            if 6 <= len(s) <= 60:
                sentences.append(s)
    if not sentences:
        sentences = [f"{topic}的要求与做法需结合现场实际落实。"]
    per_page = 4
    chunks = [sentences[i : i + per_page] for i in range(0, min(len(sentences), per_page * 6), per_page)]
    names = ["风险特点", "法规要求", "现场做法", "应急处置", "检查要点", "常见问题"]
    sections = []
    for i, chunk in enumerate(chunks):
        sections.append({
            "name": names[i % len(names)],
            "pages": [{"title": names[i % len(names)], "bullets": chunk, "image_query": ""}],
        })
    return {"title": topic[:20], "subtitle": "安全培训课件", "sections": sections or []}


def _alloc(target: int, has_refs: bool) -> tuple[int, int]:
    """页数预算：目标页数 → (章节数, 内容页数)。

    页数 = 封面 + 目录 + 结尾 (+ 图片来源页) + 章节页×N + 内容页×M。
    约束：**每章至少 1 个内容页**（否则章节页后面没有内容，实测 LLM 会干脆只给 1 章 1 页）。
    """
    fixed = 3 + (1 if has_refs else 0)
    available = max(1, target - fixed)          # 分给 章节页 + 内容页 的总预算
    if target <= 7:
        # 小页数不排章节过渡页：否则 6 页里只有 1 页内容（实测拆页会拆出 2 条要点的空洞页）
        return 0, max(1, available)
    sections = max(1, min(5, available // 2))
    content = max(1, available - sections)
    return sections, content


def _ensure_sections(plan: dict, want_sections: int) -> dict:
    """章节数不足时把最大的章节对半拆（不编造内容，只换分章）。"""
    while len(plan.get("sections") or []) < want_sections:
        sections = plan.get("sections") or []
        if not sections:
            return plan
        biggest = max(sections, key=lambda s: len(s.get("pages") or []))
        pages = biggest.get("pages") or []
        if len(pages) < 2:
            break
        half = (len(pages) + 1) // 2
        new_sec = {
            "name": str((pages[half].get("title") or "要点"))[:12],
            "pages": pages[half:],
        }
        biggest["pages"] = pages[:half]
        sections.insert(sections.index(biggest) + 1, new_sec)
        plan["sections"] = sections
    return plan


def _rebalance(plan: dict, want_content: int) -> dict:
    """把规划的内容页数调整到预算：多了截断，少了按要点拆页（不编造内容）。"""
    sections = plan.get("sections") or []
    pages = [p for s in sections for p in (s.get("pages") or [])]
    if len(pages) == want_content:
        return plan

    if len(pages) > want_content:
        # 从最后一章往前删，章节空了就移除
        excess = len(pages) - want_content
        for s in reversed(sections):
            while excess > 0 and s.get("pages"):
                s["pages"].pop()
                excess -= 1
            if excess <= 0:
                break
        plan["sections"] = [s for s in sections if s.get("pages")]
        return plan

    # 少了：把要点最多的页对半拆（同一主题的续页）
    while True:
        pages = [p for s in plan["sections"] for p in (s.get("pages") or [])]
        if len(pages) >= want_content:
            break
        target_page, target_sec = None, None
        for s in plan["sections"]:
            for p in s.get("pages") or []:
                bullets = [b for b in (p.get("bullets") or []) if str(b).strip()]
                if len(bullets) >= 6 and not p.get("_split"):
                    if target_page is None or len(bullets) > len(target_page.get("bullets") or []):
                        target_page, target_sec = p, s
        if target_page is None:
            break
        bullets = [str(b).strip() for b in target_page["bullets"]]
        half = (len(bullets) + 1) // 2
        first = dict(target_page, bullets=bullets[:half], _split=True)
        second = dict(target_page, title=f"{str(target_page.get('title') or '')[:18]}（续）",
                      bullets=bullets[half:], image_query="", _split=True)
        idx = target_sec["pages"].index(target_page)
        target_sec["pages"][idx:idx + 1] = [first, second]
    return plan


def plan_outline(spec: dict, material: str) -> dict:
    """一次结构化输出：章节 + 内容页。失败自动退回确定性兜底。"""
    topic = (spec.get("topic") or "").strip()
    target = int(spec.get("pages") or 10)
    n_sections, n_content = _alloc(target, has_refs=settings.PPT_IMAGES_ENABLED)
    try:
        from src.ai.llm_client import llm_client  # lazy

        prompt = PLAN_PROMPT.format(
            n_sections=n_sections,
            n_content=n_content,
            topic=topic,
            requirements=(spec.get("requirements") or "无")[:1500],
            material=(material or "（无资料，按通用安全培训常识撰写）")[:9000],
        )
        content = llm_client.chat([{"role": "system", "content": prompt}], temperature=0.4)
        data = _parse_json(content)
        sections = data.get("sections")
        if not isinstance(sections, list) or not sections:
            raise ValueError("规划结果缺少 sections")
        clean = []
        for s in sections[:8]:
            pages = [p for p in (s.get("pages") or []) if isinstance(p, dict) and p.get("bullets")]
            if not pages:
                continue
            clean.append({"name": str(s.get("name") or "")[:14], "pages": pages})
        if not clean:
            raise ValueError("规划结果没有可用页面")
        plan = {
            "title": str(data.get("title") or topic)[:24],
            "subtitle": str(data.get("subtitle") or "安全培训课件")[:20],
            "sections": clean,
            "source": "llm",
        }
        return _ensure_sections(_rebalance(plan, n_content), n_sections)
    except Exception as exc:  # noqa: BLE001
        logger.warning("大纲策划失败，走确定性兜底: %s", exc)
        fb = _fallback_plan(spec, material)
        fb["source"] = f"fallback:{type(exc).__name__}"
        if not (fb.get("sections") or []):
            fb["sections"] = [{"name": "要点", "pages": [
                {"title": topic[:18] or "要点", "bullets": [f"{topic}的要求需结合现场实际落实。"],
                 "image_query": ""}]}]
        return _ensure_sections(_rebalance(fb, n_content), n_sections)


def build_pages(spec: dict, plan: dict, has_refs: bool) -> list[dict]:
    """把"章节-页面"渲染成最终的页面序列（含封面/目录/章节页/来源页/结尾页）。"""
    pages: list[dict] = [{"type": "cover", "title": plan.get("title"), "subtitle": plan.get("subtitle")}]
    pages.append({"type": "toc", "sections": plan.get("sections") or []})

    # 小页数（≤7）不插章节过渡页，把页数留给内容
    with_sections = int(spec.get("pages") or 0) > 7
    for i, sec in enumerate(plan.get("sections") or [], 1):
        if with_sections:
            pages.append({"type": "section", "title": sec.get("name"), "section_no": i})
        for pg in sec.get("pages") or []:
            bullets = [str(b).strip() for b in (pg.get("bullets") or []) if str(b).strip()]
            if not bullets:
                continue
            pages.append({
                "type": "content",
                "title": str(pg.get("title") or sec.get("name"))[:20],
                "bullets": bullets,
                "image_query": str(pg.get("image_query") or "").strip(),
                "section_no": i,
            })

    if has_refs:
        pages.append({"type": "refs", "sources": []})   # sources 在 S5 后回填
    pages.append({"type": "end", "title": plan.get("title")})
    return pages


# --------------------------------------------------------------------------- #
# 事件与日志
# --------------------------------------------------------------------------- #
class _Emitter:
    """把事件同时送进：① 生成器 yield（SSE）② JSONL 日志（事后取证）。"""

    def __init__(self, user_id: int, task_id: int):
        self.user_id = user_id
        self.task_id = task_id
        self.path = log_path(user_id, task_id)
        ensure_dir(self.path.parent)
        self.events: list[dict] = []

    def emit(self, ev: dict) -> dict:
        ev = dict(ev)
        ev.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        self.events.append(ev)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except OSError as exc:  # 日志不能拖垮生成
            logger.debug("写 PPT 日志失败: %s", exc)
        line = f"[ppt_agent] task={self.task_id} {ev.get('stage', '-')} " \
               f"{ev.get('progress', '-')}% {ev.get('msg', '')}"
        if ev.get("level") == "warn":
            logger.warning(line)
        else:
            logger.info(line)
        return ev


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run(spec: dict, *, user_id: int, task_id: int):
    """生成器：逐阶段 yield 事件，结束时 yield 一条 `done`。"""
    started = time.time()
    deadline = started + float(settings.PPT_AGENT_TOTAL_TIMEOUT_S)
    em = _Emitter(user_id, task_id)

    for kind in ("images", "shots", "drafts", "logs", "out"):
        ensure_dir(task_dir(user_id, task_id, kind))

    topic = (spec.get("topic") or "").strip()
    page_target = max(3, min(int(spec.get("pages") or 10), settings.PPT_AGENT_PAGE_LIMIT))

    yield em.emit({"stage": "S0", "progress": _pct("S0", 0), "msg": f"开始生成：{topic}（目标 {page_target} 页）"})

    # ---------------- S0 预处理 ----------------
    from src.ai.query_preprocess import preprocess  # lazy

    query = preprocess(f"制作一份关于{topic}的安全培训课件", history="", covered=[])
    retrieval_query = query.get("declarative") or topic
    yield em.emit({
        "stage": "S0", "progress": _pct("S0", 1),
        "msg": f"检索式：{retrieval_query}",
        "detail": {"subqueries": query.get("subqueries"), "reason": query.get("reason")},
    })

    # ---------------- S1 检索 ----------------
    yield em.emit({"stage": "S1", "progress": _pct("S1", 0), "msg": "检索企业知识库…"})
    material = ""
    blocks: list[dict] = []
    try:
        material = ppt_tools.kb_search(retrieval_query)
        from src.ai.rag import retrieve_queries  # 复用同一批函数，拿到结构化块给判定用

        blocks = retrieve_queries([retrieval_query], k=6)
    except Exception as exc:  # noqa: BLE001
        yield em.emit({"stage": "S1", "progress": _pct("S1", 1), "level": "warn",
                       "msg": f"知识库检索失败，转到通用写法：{type(exc).__name__}"})
    yield em.emit({
        "stage": "S1", "progress": _pct("S1", 1),
        "msg": f"资料 {len(blocks)} 段 / {len(material)} 字符",
        "detail": {"chars": len(material)},
    })

    # ---------------- S2 充分性判定 ----------------
    from src.ai.sufficiency import judge  # lazy

    verdict = judge(retrieval_query, blocks=blocks)
    yield em.emit({
        "stage": "S2", "progress": _pct("S2", 1),
        "msg": ("材料充分" if verdict.get("sufficient") else
                f"材料不足，补检：{'、'.join(verdict.get('queries') or [])[:60]}"),
        "detail": {"sufficient": verdict.get("sufficient"), "reason": verdict.get("reason")},
    })

    # ---------------- S3 缺口补检（最多 1 次） ----------------
    if not verdict.get("sufficient"):
        yield em.emit({"stage": "S3", "progress": _pct("S3", 0), "msg": "按缺口补检…"})
        try:
            extra = ppt_tools.kb_search("；".join(verdict.get("queries") or [retrieval_query]))
            if extra:
                material = (material + "\n\n" + extra)[:12000]
        except Exception as exc:  # noqa: BLE001
            yield em.emit({"stage": "S3", "progress": _pct("S3", 1), "level": "warn",
                           "msg": f"补检失败（不影响继续）：{type(exc).__name__}"})
    yield em.emit({"stage": "S3", "progress": _pct("S3", 1), "msg": "资料准备完成"})

    # ---------------- S4 大纲策划 ----------------
    yield em.emit({"stage": "S4", "progress": _pct("S4", 0), "msg": "策划大纲…"})
    plan = plan_outline({**spec, "pages": page_target}, material)
    drafts = task_dir(user_id, task_id, "drafts")
    (drafts / "outline.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pages = build_pages({**spec, "pages": page_target}, plan,
                        has_refs=settings.PPT_IMAGES_ENABLED)
    yield em.emit({
        "stage": "S4", "progress": _pct("S4", 1),
        "msg": f"大纲就绪：{len(plan.get('sections') or [])} 章 / {len(pages)} 页（来源：{plan.get('source')}）",
        "detail": {"sections": [s.get("name") for s in plan.get("sections") or []]},
    })

    # ---------------- S5 素材采集 ----------------
    images_dir = task_dir(user_id, task_id, "images")
    images: dict[str, dict] = {}
    used_sources: list[dict] = []
    queries = [p["image_query"] for p in pages if p.get("type") == "content" and p.get("image_query")]
    if queries and settings.PPT_IMAGES_ENABLED:
        yield em.emit({"stage": "S5", "progress": _pct("S5", 0), "msg": f"检索配图（{len(set(queries))} 个关键词）…"})
        total = 0
        for q in dict.fromkeys(queries):
            if total >= settings.PPT_IMAGE_MAX_TOTAL or time.time() > deadline:
                break
            found = ppt_tools.search_images(q, 1, images_dir)
            if found:
                images[q] = found[0]
                used_sources.append(found[0])
                total += 1
        ppt_tools.write_sources(images_dir, used_sources)
        yield em.emit({
            "stage": "S5", "progress": _pct("S5", 1),
            "msg": (f"配图 {len(used_sources)} 张" if used_sources else "没有拿到可用配图，改用无图版式"),
            "level": "info" if used_sources else "warn",
            "detail": {"images": [it["file"] for it in used_sources]},
        })
    else:
        yield em.emit({"stage": "S5", "progress": _pct("S5", 1), "msg": "跳过配图"})

    # 回填来源页；没有配图就把"图片来源"页去掉（不留一页空白声明）
    if not used_sources:
        pages = [p for p in pages if p.get("type") != "refs"]
        # 少了一页：把要点最多的内容页对半拆（不编造内容，只为贴住用户设定的页数）
        while len(pages) < page_target:
            cand = None
            for p in pages:
                if (p.get("type") == "content" and not p.get("_split")
                        and len(p.get("bullets") or []) >= 6):
                    if cand is None or len(p["bullets"]) > len(cand["bullets"]):
                        cand = p
            if cand is None:
                break
            i = pages.index(cand)
            half = (len(cand["bullets"]) + 1) // 2
            pages[i:i + 1] = [
                dict(cand, bullets=cand["bullets"][:half], _split=True),
                dict(cand, title=f"{str(cand.get('title') or '')[:18]}（续）",
                     bullets=cand["bullets"][half:], image_query="", _split=True),
            ]
    for p in pages:
        if p.get("type") == "refs":
            p["sources"] = used_sources
    (drafts / "pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------------- S6 逐页制作 + 自检 ----------------
    theme = ppt_layout.DEFAULT_THEME
    if spec.get("template_path"):
        tpl = Path(spec["template_path"])
        if tpl.is_file():
            theme = read_template_theme(tpl) or theme
            yield em.emit({"stage": "S6", "progress": _pct("S6", 0),
                           "msg": f"使用模板配色：{theme.get('primary')} / {theme.get('accent')}"})
        else:
            yield em.emit({"stage": "S6", "progress": _pct("S6", 0), "level": "warn",
                           "msg": "模板文件不存在，使用内置配色"})

    deck = out_path(user_id, task_id, spec.get("topic") or "安全培训")
    ensure_dir(deck.parent)
    if not ppt_tools.officecli_available():
        # 兜底（验证项 7）：没有 OfficeCLI 就用 python-pptx 渲染器出片，保证"能生成"
        yield em.emit({"stage": "S6", "progress": _pct("S6", 0), "level": "warn",
                       "msg": "OfficeCLI 不可用，改用本地兜底渲染器（无配图/无逐页自检）"})
        from src.ai.ppt_local import generate_local_ppt

        outline = _pages_to_outline(pages, spec.get("topic") or "")
        count = generate_local_ppt(outline, spec.get("topic") or "安全培训", deck,
                                   template_path=spec.get("template_path"))
        shot = None
        yield em.emit({"stage": "S6", "progress": _pct("S6", 1),
                       "msg": f"兜底渲染完成：{count} 页", "page": count, "page_total": count,
                       "verdict": "fallback"})
        yield em.emit({"stage": "S7", "progress": 95, "msg": "兜底渲染不含自检，跳过 validate"})
        yield em.emit({
            "stage": "S8", "progress": 100, "done": True, "file": str(deck),
            "pages": count, "page_target": page_target, "sources": 0,
            "unfixed_pages": 0, "size_kb": deck.stat().st_size // 1024,
            "duration_s": round(time.time() - started, 1),
            "msg": f"完成（兜底渲染）：{count} 页 / {deck.stat().st_size // 1024} KB",
            "fallback": True,
        })
        return
    ppt_tools.create_deck(deck)
    ppt_tools.open_doc(deck)

    shots_dir = task_dir(user_id, task_id, "shots")
    max_fix = int(settings.PPT_AGENT_MAX_FIX_PER_PAGE)
    fixed_pages = 0
    try:
        for idx, page in enumerate(pages, 1):
            if time.time() > deadline:
                yield em.emit({"stage": "S6", "progress": _pct("S6", idx / len(pages)),
                               "level": "warn", "msg": f"整单超时，停在第 {idx} 页（已产页保留）"})
                break
            image = images.get(page.get("image_query")) if page.get("type") == "content" else None
            fix: dict = {}
            verdict = {"ok": True, "problems": [], "fix_hints": [], "skipped": "not reviewed"}
            shot: Path | None = None
            for round_no in range(max_fix + 1):
                page["_fix"] = fix
                cmds = ppt_layout.build(idx, page, idx, image=image, theme=theme)
                if round_no > 0:                      # 重建本页：先删掉最后一张幻灯片
                    try:
                        ppt_tools._oci("remove", str(deck), f"/slide[{idx}]")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("重建第 %s 页时删除失败: %s", idx, exc)
                receipts = ppt_tools.apply_commands(deck, cmds)
                # 合规（Unsplash）：署名必须可点击 —— 从 batch 回执拿到元素真实路径再挂 link
                by_index = {r["index"]: r["path"] for r in receipts
                            if r.get("ok") and r.get("path")}
                pairs = [
                    (by_index.get(i), c.get("_link"), c.get("_tooltip", ""))
                    for i, c in enumerate(cmds) if c.get("_link")
                ]
                pairs = [pp for pp in pairs if pp[0] and pp[1]]
                if pairs:
                    try:
                        ppt_tools.apply_hyperlinks(deck, pairs)
                    except Exception as exc:  # noqa: BLE001 —— 链接失败不阻断出片
                        logger.warning("附加超链接失败(第 %s 页): %s", idx, exc)
                shot = ppt_tools.render_page(deck, idx, shots_dir / f"page-{idx:02d}.png")
                verdict = (vision_agent.review_page(shot, page)
                           if page.get("type") == "content" else
                           {"ok": True, "problems": [], "fix_hints": [], "skipped": "layout page"})
                if verdict.get("ok") or round_no >= max_fix:
                    break
                new_fix = _next_fix(fix, verdict)
                if new_fix == fix:                    # 没有更狠的修法，止损
                    break
                fix = new_fix
                yield em.emit({
                    "stage": "S6", "progress": _pct("S6", (idx - 0.5) / len(pages)),
                    "level": "warn", "page": idx, "page_total": len(pages),
                    "msg": f"第 {idx} 页自检未通过（{_problem_summary(verdict)}），修补中（{round_no + 1}/{max_fix}）",
                })
            if not verdict.get("ok"):
                fixed_pages += 1
            yield em.emit({
                "stage": "S6", "progress": _pct("S6", idx / len(pages)),
                "page": idx, "page_total": len(pages),
                "shot": str(shot) if shot else None,
                "verdict": "ok" if verdict.get("ok") else "unfixed",
                "msg": f"第 {idx}/{len(pages)} 页：{page.get('title') or page.get('type')}"
                       + ("" if verdict.get("ok") else "（自检仍有问题，见日志）"),
            })
    finally:
        ppt_tools.save_doc(deck)
        ppt_tools.close_doc(deck)

    # ---------------- S7 收尾 ----------------
    yield em.emit({"stage": "S7", "progress": _pct("S7", 0), "msg": "校验文件…"})
    try:
        report = ppt_tools.validate(deck)
    except Exception as exc:  # noqa: BLE001
        report = f"validate 失败: {type(exc).__name__}: {exc}"
    issues = ppt_tools.inspect_issues(deck)
    yield em.emit({
        "stage": "S7", "progress": _pct("S7", 1),
        "msg": f"校验：{report[:120]}（问题 {len(issues)} 条）",
        "level": "warn" if issues else "info",
    })

    # ---------------- S8 落盘 ----------------
    size_kb = deck.stat().st_size // 1024 if deck.is_file() else 0
    elapsed = round(time.time() - started, 1)
    result = {
        "stage": "S8", "progress": 100, "done": True,
        "file": str(deck), "pages": len(pages), "page_target": page_target,
        "sources": len(used_sources), "unfixed_pages": fixed_pages,
        "size_kb": size_kb, "duration_s": elapsed,
        "msg": f"完成：{len(pages)} 页 / {size_kb} KB / {elapsed}s",
    }
    yield em.emit(result)


def _pages_to_outline(pages: list[dict], topic: str) -> dict:
    """把 Agent 的页面序列压回旧渲染器认的 outline（兜底链路用）。"""
    sections: list[dict] = []
    current = None
    for p in pages:
        if p.get("type") == "section":
            current = {"name": p.get("title") or "要点", "points": []}
            sections.append(current)
        elif p.get("type") == "content":
            if current is None:
                current = {"name": p.get("title") or "要点", "points": []}
                sections.append(current)
            current["points"].extend([str(b) for b in (p.get("bullets") or [])])
    title = next((p.get("title") for p in pages if p.get("type") == "cover"), topic)
    subtitle = next((p.get("subtitle") for p in pages if p.get("type") == "cover"), "安全培训课件")
    if not sections:
        sections = [{"name": topic or "要点", "points": ["按培训要求组织内容。"]}]
    return {"title": title or topic, "subtitle": subtitle, "sections": sections}


def _next_fix(fix: dict, verdict: dict) -> dict:
    """根据审图问题给出更狠的修法（幂等：返回与入参相同表示"没得更狠"）。

    第 1 轮只缩字号 —— 因为 6 条以上会走双栏，缩字号通常就够；
    第 2 轮才砍要点/去配图（砍内容是最伤信息的操作，放到最后）。
    """
    problems = {str(p.get("type") or "") for p in verdict.get("problems") or [] if isinstance(p, dict)}
    out = {
        "font_delta": int(fix.get("font_delta") or 0) - 2,
        "max_bullets": int(fix.get("max_bullets") or ppt_layout.MAX_BULLETS),
        "drop_image": bool(fix.get("drop_image")),
    }
    if out["font_delta"] <= -4:           # 已经缩过一轮仍溢出 → 才减要点
        out["max_bullets"] = min(out["max_bullets"], 4)
    if "overlap" in problems or "missing" in problems:
        out["drop_image"] = True
    return out


def _problem_summary(verdict: dict) -> str:
    parts = []
    for p in verdict.get("problems") or []:
        if isinstance(p, dict):
            parts.append(f"{p.get('type')}:{str(p.get('detail') or '')[:24]}")
    return "；".join(parts)[:80] or "未知问题"
