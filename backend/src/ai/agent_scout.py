"""缺口驱动的 Agent 网搜采集（v2.0 大点 04 · 裁定 10）。

触发：回答落库后**异步**执行（不阻塞 SSE、不影响 01 的时延口径）。
信号：本轮"没有检索到任何资料"或"检索降级"——与会话缓存不足区分开。

流程：
1. `plan()`：读方法论 + 缺口问题 → LLM 给出 1~2 条检索词（`agent_plan` 提示词）；
2. `collect()`：Playwright 打开搜索页（`KB_SCOUT_SEARCH_URL` 模板）→ 收集候选链接 →
   逐个打开抽取正文（限站点/页数/字节/时长）→ 全程记录 URL 与结果；
3. `summarize()`：LLM 把材料总结成 md（`agent_summarize` 提示词，≥1000 字、带来源）；
4. `submit()`：以 `knowledge_scout` 身份提交 → 进人工审核（**不自动入库**）；
5. 全程写 `agent_runs`，并把"成功/失败教训"追加进 `cases.md`（方法论的进化来源）。

安全边界（裁定 10）：只允许 http/https；禁 `file://`、localhost、内网段与保留 IP；
`KB_FETCH_DENY_HOSTS` 可加黑名单；超限即停。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

from src.core.config import settings

logger = logging.getLogger(__name__)

_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}
#: 搜索引擎模板（可配）；百度对中文安全类查询命中率明显高于 Bing
#: 相关性判定：缺口关键词在正文里的最少命中数（权威站点放宽到 1）
_MIN_KEYWORD_HITS = 2
#: 条款级证据（有条款号/标准号/条文字样）——低权威站点必须命中
_CLAUSE_RE = None  # 延迟编译
_MAX_TEXT_PER_PAGE = 12000
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 软黑名单（质量优先，不是安全边界）：百科/问答/文库/门户/社交一律不采
_SOFT_DENY_HOSTS = (
    "baike.baidu.com", "zhidao.baidu.com", "wenku.baidu.com", "baidu.com", "zhihu.com",
    "docin.com", "doc88.com", "book118.com", "renrendoc.com", "csdn.net", "cnblogs.com",
    "sohu.com", "163.com", "sina.com.cn", "weibo.com", "douban.com", "bilibili.com",
    "wikipedia.org", "youtube.com", "facebook.com", "twitter.com", "x.com", "tiktok.com",
    # 文库聚合（正文是摘要/预览，无法核对原件）与搜索引擎自身页面
    "wenku.so.com", "wenku.baidu.com", "image.so.com", "ai.so.com", "so.com",
    "sogou.com", "bing.com", "baidu.com", "sm.cn", "toutiao.com",
)
# 权威来源加分（排序用）
_AUTHORITY_SUFFIX = (".gov.cn", ".org.cn", ".edu.cn", "mem.gov.cn", "mohurd.gov.cn")

_lock = threading.Lock()
_running: set[str] = set()


# --------------------------------------------------------------------------- #
# 安全边界
# --------------------------------------------------------------------------- #
def url_allowed(url: str) -> tuple[bool, str]:
    """硬边界：scheme / 主机名 / 解析后的 IP 三重校验（防 SSRF）。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:  # noqa: BLE001
        return False, "URL 解析失败"
    if parsed.scheme not in ("http", "https"):
        return False, f"协议不允许：{parsed.scheme or '空'}"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "缺少主机名"
    if host in _LOCAL_NAMES:
        return False, "禁止访问本机"
    extra = {h.strip().lower() for h in settings.KB_FETCH_DENY_HOSTS.split(",") if h.strip()}
    if host in extra or any(host.endswith("." + d) for d in extra):
        return False, f"黑名单主机：{host}"
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                return False, f"解析到内网/保留地址：{ip}"
    except socket.gaierror:
        return False, f"域名解析失败：{host}"
    except Exception as exc:  # noqa: BLE001
        return False, f"地址校验失败：{type(exc).__name__}"
    return True, ""


# --------------------------------------------------------------------------- #
# 触发判断
# --------------------------------------------------------------------------- #
def _keywords(text: str, limit: int = 8) -> list[str]:
    """缺口问题 → 关键词（jieba 可用就分词，否则按非中文切分）。"""
    import re as _re

    raw = (text or "").strip()
    if not raw:
        return []
    tokens: list[str] = []
    try:
        import jieba  # lazy

        tokens = [w.strip() for w in jieba.cut(raw) if len(w.strip()) >= 2]
    except Exception:  # noqa: BLE001
        tokens = [t for t in _re.split(r"[^\u4e00-\u9fa5A-Za-z0-9]+", raw) if len(t) >= 2]
    stop = {"要求", "哪些", "什么", "需要", "如何", "怎么", "是否", "作业", "配备", "进行", "相关", "知识库"}
    out: list[str] = []
    for t in tokens:
        if t in stop or t in out:
            continue
        out.append(t)
        if len(out) >= limit:
            break
    return out


def _keyword_hits(text: str, keywords: list[str]) -> int:
    body = text or ""
    return sum(1 for k in keywords if k and k in body)


def _has_clause(text: str) -> bool:
    """是否出现"条款级"结构：第X条 / GB(T) 编号 / X号文 / 章-节 编号。"""
    import re as _re

    return bool(
        _re.search(r"第[\d一二三四五六七八九十百千．.\s]{1,8}条", text)
        or _re.search(r"(GB|AQ|JGJ|DB\d{2}|SL|DL)\s*/?\s*T?\s*\d{3,}", text)
        or _re.search(r"[〔\[（(]\d{4}[〕\]）)]\s*\d+\s*号", text)
    )


def _is_relevant(url: str, text: str, keywords: list[str]) -> tuple[bool, str]:
    """相关 + 有条款级证据：低权威站点必须"既相关又有条款"，官方站点只需相关。"""
    hits = _keyword_hits(text, keywords)
    q = _host_quality(url)
    if q < 0:
        return False, "软黑名单来源"
    if q >= 2:
        return (hits >= 1, f"权威站点，关键词命中 {hits}")
    has_clause = _has_clause(text)
    if hits < _MIN_KEYWORD_HITS:
        # 命中 1 个关键词也放行，但必须"长文 + 有条款结构"（避免题库/泛述页）
        if not (hits >= 1 and has_clause and len(text) >= 1200):
            return False, f"关键词命中 {hits} < {_MIN_KEYWORD_HITS} 且不够长/无条款"
    if not has_clause:
        return False, "无条款级证据（疑似泛述/文库页）"
    return True, f"关键词命中 {hits} 且有条款结构"


def _is_search_host(host: str) -> bool:
    """是不是搜索引擎自家的域名（跳转链接本身可以打开，但不能当来源）。"""
    host = (host or "").lower()
    for _name, tpl, _sel in _engines():
        eh = (urllib.parse.urlparse(tpl.split("{")[0]).hostname or "").lower()
        eh = eh[4:] if eh.startswith("www.") else eh
        if eh and (host == eh or host.endswith("." + eh) or host.startswith(eh + ".")):
            return True
    return False


def _host_quality(url: str) -> int:
    """来源质量分：gov/org/edu 优先；软黑名单直接 -1（不采）。"""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in _SOFT_DENY_HOSTS):
        return -1
    if any(host.endswith(suffix) for suffix in _AUTHORITY_SUFFIX):
        return 2
    return 0


#: 答案里出现这些说法 = 模型自己承认"知识库没覆盖"（比 sources==0 更准的缺口信号）
GAP_MARKERS = (
    "依据现有资料无法确认", "资料中没有", "资料中未", "参考资料中没有", "没有针对",
    "未覆盖", "没有相关", "资料不足", "无法确认", "未提及", "缺少相关",
)


def answer_declares_gap(answer: str) -> bool:
    """答案是否自述"知识库覆盖不足"。"""
    text = (answer or "").strip()
    if len(text) < 10:
        return False
    return any(m in text for m in GAP_MARKERS)


def should_scout(prepared: dict, source_count: int, answer: str = "") -> bool:
    """对"资料确实缺"的轮次触发（闲聊不触发）。

    三种缺口信号任一命中即触发：
    1. 检索降级（向量库/BM25 失败）；
    2. 一条资料都没检索到；
    3. 答案自述"资料中没有/无法确认"（知识库覆盖面不足，但检索本身成功）。
    """
    if not settings.KB_AGENT_SCOUT_ENABLED:
        return False
    if prepared.get("mode") == "chat":
        return False
    if prepared.get("retrieval_degraded"):
        return True
    if source_count == 0:
        return True
    return answer_declares_gap(answer)


def kick_off(conversation_id: int, question: str) -> bool:
    """起一个后台线程跑采集（幂等：同一会话+问题短时间内只跑一次）。"""
    key = f"{conversation_id}:{hash(question) & 0xFFFFFFFF}"
    with _lock:
        if key in _running:
            return False
        _running.add(key)

    def _run():
        try:
            run(conversation_id, question)
        except Exception:  # noqa: BLE001 —— 后台任务失败不影响用户
            logger.exception("Agent 采集失败 conv=%s", conversation_id)
        finally:
            with _lock:
                _running.discard(key)

    threading.Thread(target=_run, name=f"scout-{conversation_id}", daemon=True).start()
    return True


# --------------------------------------------------------------------------- #
# 采集流程
# --------------------------------------------------------------------------- #
@dataclass
class Page:
    url: str
    title: str = ""
    text: str = ""


@dataclass
class CollectResult:
    pages: list[Page] = field(default_factory=list)
    visited: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for p in self.pages)


def _plan_queries(gap_question: str, methodology: str) -> list[str]:
    """LLM 依据方法论给出检索词（最多 2 条）。失败退回原问题。"""
    try:
        from src.ai.llm_client import llm_client
        from src.ai.prompts import render

        prompt = render("agent_plan", gap=gap_question, methodology=methodology[-6000:] or "（无）")
        raw = llm_client.chat([{"role": "system", "content": prompt}], temperature=0.2)
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(raw[start : end + 1])
            qs = [str(q).strip() for q in (obj.get("queries") or []) if str(q).strip()]
            if qs:
                return qs[:2]
    except Exception as exc:  # noqa: BLE001
        logger.warning("采集检索词生成失败，用原问题：%s", exc)
    return [gap_question[:80]]


def _engines() -> list[tuple[str, str, str]]:
    """解析 `KB_SCOUT_ENGINES`：名称|URL模板|结果选择器。"""
    out: list[tuple[str, str, str]] = []
    for part in (settings.KB_SCOUT_ENGINES or "").split(";"):
        fields = [f.strip() for f in part.split("|")]
        if len(fields) == 3 and fields[1]:
            out.append((fields[0], fields[1], fields[2]))
    return out


def _search_links(page, query: str) -> list[str]:
    """按引擎列表取**结果链接**（用各引擎的结果选择器，不抓全站导航）。"""
    for name, tpl, selector in _engines():
        url = tpl.format(query=urllib.parse.quote(query))
        ok, _why = url_allowed(url)
        if not ok:
            continue
        try:
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(1400)
            links = page.eval_on_selector_all(
                selector,
                "els => els.map(e => e.href).filter(h => !!h).slice(0, 20)",
            )
        except Exception:  # noqa: BLE001
            continue
        links = [ln for ln in links if ln and not ln.startswith("javascript")]
        if links:
            logger.info("搜索引擎 %s 命中 %d 条候选", name, len(links))
            return links
        logger.info("搜索引擎 %s 无结果链接，回退下一个", name)
    return []


def _fetch_pdf(url: str, keywords: list[str]) -> Page | None:
    """下载页内 PDF/附件并用 kb_convert 转 md（一次任务最多 1 个）。"""
    import tempfile
    import urllib.request

    from src.services import kb_convert

    ok, why = url_allowed(url)
    if not ok:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(settings.KB_FETCH_MAX_BYTES + 1)
        if len(data) > settings.KB_FETCH_MAX_BYTES:
            return None
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(data)
            tmp = Path(fh.name)
        try:
            conv = kb_convert.to_markdown(tmp, "laws")
        finally:
            tmp.unlink(missing_ok=True)
        text = conv["md"]
        if _keyword_hits(text, keywords) < 1:
            return None
        return Page(url=url, title=text.splitlines()[0].lstrip("# ")[:120], text=text[:_MAX_TEXT_PER_PAGE])
    except Exception as exc:  # noqa: BLE001
        logger.info("附件下载/转换失败 %s: %s", url[:80], type(exc).__name__)
        return None


def _collect(queries: list[str], gap_question: str = "") -> CollectResult:
    """搜索 → 相关性过滤 → 抽正文 → （必要时）跟进 PDF。全部限额。"""
    from playwright.sync_api import sync_playwright

    result = CollectResult()
    keywords = _keywords(gap_question or " ".join(queries))
    max_pages = settings.KB_FETCH_MAX_PAGES
    deadline = time.time() + settings.KB_FETCH_TIMEOUT_S
    seen_hosts: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=_USER_AGENT, locale="zh-CN")
        page = ctx.new_page()

        candidates: list[str] = []
        for q in queries:
            if time.time() > deadline or len(candidates) >= max_pages:
                break
            links = _search_links(page, q)
            if not links:
                result.errors.append(f"搜索无结果（{q[:20]}）")
                continue
            for link in sorted(links, key=_host_quality, reverse=True):
                if link in candidates:
                    continue
                host = urllib.parse.urlparse(link).hostname or ""
                if _host_quality(link) < 0 and not _is_search_host(host):
                    continue  # 软黑名单直接跳过（搜索跳转链接除外，落地后再判）
                if host in seen_hosts and len(seen_hosts) >= settings.KB_FETCH_MAX_SITES:
                    continue
                seen_hosts.add(host)
                candidates.append(link)
                if len(candidates) >= max_pages or len(seen_hosts) >= settings.KB_FETCH_MAX_SITES:
                    break

        pdf_done = False
        for url in candidates:
            if time.time() > deadline or len(result.pages) >= 4:
                break
            try:
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(600)
                final_url = page.url or url          # 百度跳转后的真实地址
                title = (page.title() or "").strip()[:120]
                text = page.inner_text("body")[: _MAX_TEXT_PER_PAGE]
                pdf_links = page.eval_on_selector_all(
                    "a[href$='.pdf'], a[href*='.pdf']", "els => els.map(e => e.href).slice(0, 3)"
                )
            except Exception as exc:  # noqa: BLE001
                result.visited.append({"url": url, "ok": False, "why": type(exc).__name__})
                continue

            text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
            final_host = (urllib.parse.urlparse(final_url).hostname or "").lower()
            if _is_search_host(final_host):
                result.visited.append({"url": final_url, "ok": False, "why": "搜索页，不是来源"})
                continue
            ok_final, why_final = url_allowed(final_url)
            if not ok_final:
                result.visited.append({"url": final_url, "ok": False, "why": f"落地地址被拒：{why_final}"})
                continue
            if len(text) < 200:
                # 正文可能是附件：跟一个 PDF
                if pdf_links and not pdf_done:
                    pdf_done = True
                    pdf_page = _fetch_pdf(pdf_links[0], keywords)
                    if pdf_page:
                        result.pages.append(pdf_page)
                        result.visited.append({"url": pdf_page.url, "ok": True, "kind": "pdf",
                                               "chars": len(pdf_page.text)})
                        continue
                result.visited.append({"url": final_url, "ok": False, "why": "正文过短（可能是导航页）"})
                continue

            relevant, why = _is_relevant(final_url, text, keywords)
            if not relevant:
                result.visited.append({"url": final_url, "ok": False, "why": why})
                # 相关但缺条款时，尝试页内 PDF 作为"条款级证据"
                if pdf_links and not pdf_done and _keyword_hits(text, keywords) >= 1:
                    pdf_done = True
                    pdf_page = _fetch_pdf(pdf_links[0], keywords)
                    if pdf_page:
                        result.pages.append(pdf_page)
                        result.visited.append({"url": pdf_page.url, "ok": True, "kind": "pdf",
                                               "chars": len(pdf_page.text)})
                continue

            result.pages.append(Page(url=final_url, title=title, text=text))
            result.visited.append({"url": final_url, "ok": True, "chars": len(text), "why": why})
        browser.close()
    return result


def _broaden(gap_question: str, queries: list[str]) -> list[str]:
    """放宽检索词：去掉 site: 限定，取缺口关键词前 4 个 + "规程/标准 全文"。"""
    kws = _keywords(gap_question)[:4]
    base = " ".join(kws) if kws else gap_question[:40]
    out = [f"{base} 规程 全文", f"{base} 标准 要求"]
    return [q for q in out if q not in queries][:2]


def _collect_merged(queries: list[str], gap_question: str) -> CollectResult:
    """先按计划词采；0 命中或材料偏少时放宽再采一轮，按 URL 去重合并。"""
    first = _collect(queries, gap_question)
    if len(first.pages) >= 2 and first.total_chars >= 3000:
        return first
    extra_q = _broaden(gap_question, queries)
    if not extra_q:
        return first
    logger.info("材料偏少（%d 页/%d 字），放宽检索词再采一轮：%s", len(first.pages), first.total_chars, extra_q)
    second = _collect(extra_q, gap_question)
    seen = {pg.url for pg in first.pages}
    for pg in second.pages:
        if pg.url not in seen:
            first.pages.append(pg)
            seen.add(pg.url)
    first.visited.extend(second.visited)
    first.errors.extend(second.errors)
    return first


def _summarize(gap_question: str, pages: list[Page]) -> str:
    """LLM 只做"抄要求"；材料不够就返回 `INSUFFICIENT`（由上层决定不提交）。"""
    from src.ai.llm_client import llm_client
    from src.ai.prompts import render

    materials = "\n\n".join(
        f"[来源 {p.url}]\n标题：{p.title}\n{p.text[:6000]}" for p in pages[:4]
    )
    prompt = render("agent_summarize", gap=gap_question, materials=materials[:20000])
    out = ""
    for _attempt in range(2):
        out = (llm_client.chat([{"role": "system", "content": prompt}], temperature=0.1) or "").strip()
        if out:
            break
        time.sleep(1.5)
    if not out:
        logger.warning("总结模型连续返回空，按材料不足处理")
        return "INSUFFICIENT"
    if "INSUFFICIENT" in out[:80].upper():
        return "INSUFFICIENT"
    if not out.startswith("#"):
        out = f"# {gap_question[:40]}\n\n{out}"
    return out


_URL_RE = None


def _strip_links(md: str, max_sources: int = 3) -> str:
    """把正文里的链接全部清掉，只在文末保留 ≤3 条来源。

    - 去掉 `[来源: ...]`、`（来源：...）` 这类内联标注；
    - 去掉正文里的裸 URL（http/https/www.开头）；
    - 末尾「来源」节重建为最多 3 条纯 URL。
    """
    import re as _re

    text = md or ""
    urls = _re.findall(r"https?://[^\s\)\]<>，。；、]+", text)
    seen: list[str] = []
    for u in urls:
        u = u.rstrip(".,;)")
        if u not in seen:
            seen.append(u)

    # 拆出末尾「来源」节
    body, _, _tail = text.partition("\n## 来源")
    body = _re.sub(r"\[来源[:：][^\]]*\]", "", body)          # [来源: ...]
    body = _re.sub(r"[（(]来源[:：][^）)]*[）)]", "", body)      # （来源：...）
    body = _re.sub(r"https?://\S+", "", body)                  # 裸链接
    body = _re.sub(r"\bwww\.\S+", "", body)
    body = _re.sub(r"[ \t]{2,}", " ", body)
    body = _re.sub(r"\n{3,}", "\n\n", body).strip()

    if seen:
        tail = "\n\n## 来源\n" + "\n".join(f"- {u}" for u in seen[:max_sources])
        return body + tail + "\n"
    return body + "\n"


def _link_stats(md: str) -> tuple[int, float]:
    """正文（末尾来源节之前）的 URL 个数与字符占比。"""
    import re as _re

    body = (md or "").partition("\n## 来源")[0]
    urls = _re.findall(r"https?://\S+", body)
    chars = max(1, len(body))
    return len(urls), sum(len(u) for u in urls) / chars


def _knowledge_quality(md: str, min_chars: int) -> tuple[bool, str]:
    """知识含量闸门：有条款/条目、有引用、长度够，才允许提交。"""
    import re as _re

    body = _re.sub(r"^>.*$", "", md, flags=_re.M)
    chars = len(body.strip())
    bullets = len(_re.findall(r"^\s*(?:[-*]|\d+[.、])\s+\S", body, flags=_re.M))
    banned = ("缺口说明", "待补录", "本次结论：未取得", "后续需要补充核实", "知识库维护操作步骤")
    if md.strip() == "INSUFFICIENT":
        return False, "模型判定材料不足以成文"
    if any(b in md for b in banned):
        return False, "文档是缺口/待补录叙事，不是知识条目"
    if chars < min_chars:
        return False, f"正文 {chars} 字 < {min_chars}"
    if bullets < 5:
        return False, f"条目仅 {bullets} 条，疑似空泛文档"
    body_urls, ratio = _link_stats(md)
    total_urls = len(_re.findall(r"https?://\S+", md))
    if body_urls > 0 or "[来源" in body:
        return False, f"正文仍含 {body_urls} 个链接/内联标注，属索引文档"
    if total_urls > 3:
        return False, f"来源 URL {total_urls} 条 > 3（污染语料）"
    if ratio > 0.02:
        return False, f"链接字符占比 {ratio:.1%} 过高"
    if not _has_clause(md) and bullets < 8:
        return False, "正文既无条款号也无足够条目"
    return True, f"{chars} 字 / {bullets} 条 / 来源 {total_urls} 条"


def _record_start(conversation_id: int, question: str) -> int | None:
    """采集一开始就建一条 `running` 任务（方案：建任务 → 可回放）。"""
    from src.core.db import SessionLocal
    from src.models.agent import AgentRun

    db = SessionLocal()
    try:
        run = AgentRun(
            conversation_id=conversation_id,
            gap_question=question[:2000],
            model=settings.DEEPSEEK_MODEL,
            methodology_version=_methodology_version(),
            status="running",
            note="采集开始",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    except Exception:  # noqa: BLE001 —— 记录失败不影响采集
        logger.exception("agent_runs 建任务失败")
        return None
    finally:
        db.close()


def _record_finish(run_id: int | None, conversation_id: int, question: str, *,
                   status: str, urls: list[dict] | None = None, contrib_id: int | None = None,
                   note: str = "", md_path: str | None = None) -> None:
    """结束时回填同一行（没有 run_id 时兜底新建一条，保证有痕迹）。"""
    from src.core.db import SessionLocal
    from src.models.agent import AgentRun

    db = SessionLocal()
    try:
        row = db.get(AgentRun, run_id) if run_id else None
        if row is None:
            row = AgentRun(
                conversation_id=conversation_id,
                gap_question=question[:2000],
                model=settings.DEEPSEEK_MODEL,
                methodology_version=_methodology_version(),
            )
            db.add(row)
        row.status = status
        row.urls = json.dumps(urls or [], ensure_ascii=False)
        row.contrib_id = contrib_id
        row.note = note[:2000]
        row.result_md_path = md_path
        db.commit()
    finally:
        db.close()


def _notify_asker(conversation_id: int, gap_question: str, contrib_id: int) -> None:
    """告诉提问者：你的问题触发了资料补充，已提交管理员审核。"""
    try:
        from sqlalchemy import select

        from src.core.db import SessionLocal
        from src.models.conversation import Conversation
        from src.services.notification_service import TYPE_KB_CONTRIB, NotificationService

        db = SessionLocal()
        try:
            conv = db.get(Conversation, conversation_id)
            if not conv:
                return
            NotificationService.push(
                conv.user_id,
                f"【资料补充待审】已针对「{gap_question[:30]}」自动采集公开资料并提交（贡献 #{contrib_id}），"
                f"管理员审核通过后即可在问答中检索到。",
                msg_type=TYPE_KB_CONTRIB,
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001 —— 通知失败不影响采集结果
        logger.debug("通知提问者失败", exc_info=True)


def _methodology_version() -> str:
    try:
        from src.services import agent_docs

        return agent_docs.version()
    except Exception:  # noqa: BLE001
        return "none"


def _with_provenance(md: str, pages: list[Page]) -> str:
    """在标题后插入来源小字（裁定 1/10）：不破坏 `_first_title()`（第 1 行仍是 `# 标题`）。"""
    lines = (md or "").strip().splitlines()
    if not lines:
        return md
    stamp = time.strftime("%Y-%m-%d")
    note = f"> Agent 贡献（{settings.DEEPSEEK_MODEL}） · 来源 {len(pages)} 篇 · 抓取 {stamp} · 待人工审核"
    return "\n".join([lines[0], "", note, ""] + lines[1:])


def run(conversation_id: int, gap_question: str) -> dict:
    """完整跑一次采集 + 提交审核。返回摘要（供测试/日志）。"""
    from sqlalchemy import select

    from src.core.db import SessionLocal
    from src.models.agent import AgentClient
    from src.services import agent_contrib_service, agent_docs

    run_id = _record_start(conversation_id, gap_question)
    retried = False
    methodology = agent_docs.read(agent_docs.METHODOLOGY)["content"]
    queries = _plan_queries(gap_question, methodology)
    logger.info("Agent 采集开始 conv=%s run=%s queries=%s", conversation_id, run_id, queries)

    collected = _collect_merged(queries, gap_question)
    if not collected.pages:
        _record_finish(run_id, conversation_id, gap_question, status="failed",
                       urls=collected.visited,
                       note="；".join(collected.errors)[:500] or "没有抓到可用正文")
        return {"ok": False, "reason": "no_pages", "visited": collected.visited}

    md = _summarize(gap_question, collected.pages)
    if md.strip() == "INSUFFICIENT":
        _record_finish(run_id, conversation_id, gap_question, status="insufficient",
                       urls=collected.visited, note="材料相关但不足以成文（模型判定）")
        try:
            agent_docs.append_case(
                f"{time.strftime('%Y-%m-%d')} | {collected.pages[0].url} | 缺口「{gap_question[:40]}」"
                f" | 材料不足 | 换更权威来源（标准/规程原文）"
            )
        except Exception:  # noqa: BLE001
            logger.debug("案例回写失败", exc_info=True)
        return {"ok": False, "reason": "insufficient", "note": "model_insufficient"}

    md = _strip_links(md)
    md = _with_provenance(md, collected.pages)
    title = (md.splitlines()[0] or "").lstrip("# ").strip()[:120] or gap_question[:60]

    ok_doc, why_doc = _knowledge_quality(md, settings.KB_CONTRIB_MIN_CHARS)
    if not ok_doc and not retried:
        # 第二轮抢救：再采一批，重新总结一次（只做一次，避免无限拉长）
        retried = True
        more = _collect(_broaden(gap_question, queries + ["__retry__"]), gap_question)
        fresh = [pg for pg in more.pages if pg.url not in {x.url for x in collected.pages}]
        if fresh:
            logger.info("质量闸门未过（%s），补采 %d 页后重试总结", why_doc, len(fresh))
            collected.pages.extend(fresh)
            collected.visited.extend(more.visited)
            md = _summarize(gap_question, collected.pages)
            if md.strip() != "INSUFFICIENT":
                md = _with_provenance(_strip_links(md), collected.pages)
                title = (md.splitlines()[0] or "").lstrip("# ").strip()[:120] or gap_question[:60]
                ok_doc, why_doc = _knowledge_quality(md, settings.KB_CONTRIB_MIN_CHARS)
    if not ok_doc:
        _record_finish(run_id, conversation_id, gap_question, status="insufficient",
                       urls=collected.visited, note=f"未通过知识含量闸门：{why_doc}")
        try:
            agent_docs.append_case(
                f"{time.strftime('%Y-%m-%d')} | {collected.pages[0].url} | 缺口「{gap_question[:40]}」"
                f" | 未通过闸门（{why_doc}） | 换更权威来源"
            )
        except Exception:  # noqa: BLE001
            logger.debug("案例回写失败", exc_info=True)
        return {"ok": False, "reason": "low_quality", "note": why_doc}

    body_chars = len(md)
    db = SessionLocal()
    try:
        agent_row = db.scalar(select(AgentClient).where(AgentClient.name == "knowledge_scout"))
        agent = {"id": agent_row.id if agent_row else None, "name": "knowledge_scout",
                 "model": settings.DEEPSEEK_MODEL}
        try:
            contrib = agent_contrib_service.submit_agent(
                db,
                title=title,
                doc_type="procedures",
                intro=f"Agent 针对「{gap_question[:60]}」自动采集并总结，来源 {len(collected.pages)} 篇。",
                md=md,
                agent=agent,
                origin_urls=[p.url for p in collected.pages],
            )
        except Exception as exc:  # noqa: BLE001
            _record_finish(run_id, conversation_id, gap_question, status="failed",
                           urls=collected.visited, note=f"提交失败：{exc}")
            raise
    finally:
        db.close()

    _record_finish(run_id, conversation_id, gap_question, status="submitted",
                   urls=collected.visited, contrib_id=contrib.id, md_path=contrib.file_path,
                   note=f"{body_chars} 字 / {len(collected.pages)} 篇")
    try:
        agent_docs.append_case(
            f"{time.strftime('%Y-%m-%d')} | {collected.pages[0].url} | 缺口「{gap_question[:40]}」"
            f" | 成功（{len(collected.pages)} 篇，{body_chars} 字） | 已提交待审 #{contrib.id}"
        )
    except Exception:  # noqa: BLE001
        logger.debug("案例回写失败", exc_info=True)

    return {"ok": True, "contrib_id": contrib.id, "chars": body_chars, "pages": len(collected.pages)}
