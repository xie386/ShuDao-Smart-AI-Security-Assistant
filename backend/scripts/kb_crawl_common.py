#!/usr/bin/env python
"""知识库批量采集公共库（D6 扩容：90 余份 → 1000+ 份）。

四个采集脚本（crawl_laws / crawl_procedures / crawl_plans / crawl_cases）共用本模块。

与 D3 的 `fetch_kb.py` 的分工：
- `fetch_kb.py`：**清单驱动**（人工维护 kb_manifest.jsonl 的 92 条精选权威源），保留不动；
- 本模块：**列表页驱动**（自动翻页发现详情页 URL），用于把知识库从 ~90 份扩到 1000+ 份。

核心能力：
1. **列表页翻页发现**：安全管理网（safehoo.com）栏目页规律 `List_<N>.shtml`，
   首页为栏目根目录；逐页提取详情链接直到 404 / 空页 / 达到配额；
2. **详情页分页合并**：详情页正文可能分页（`<id>_2.shtml`、`_3.shtml`…），
   自动跟随「下一页」把多页正文 HTML 片段合并为一个文件（否则正文被截断）；
3. **断点续传**：目标文件已存在则跳过，脚本可反复重跑；
4. **去重**：同一 doc_type 内按 source_id（站点+栏目+文章 ID）去重；
5. **manifest 追加**：写入 `kb_manifest.jsonl`，与 D3 既有记录同构
   （source_id/url/media_type/doc_type/status/extract_status），
   供 `govern_html.py` 治理后回填 title/字数，`collect_kb.py` 入库时可溯源；
6. **礼貌抓取**：串行 + 每请求间隔（默认 0.3s），失败重试 2 次，不并发压站。

代理注意：`trust_env=False` 直连（本机代理转发部分站点会 502，与 curl --noproxy 同理）。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

BASE = Path(__file__).resolve().parent.parent / "knowledge_src"
MANIFEST = BASE / "kb_manifest.jsonl"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# 详情页链接：/<频道>/<栏目>/<YYYYMM>/<文章ID>.shtml（安全管理网通用规律）
DETAIL_RE = re.compile(r'href="([^"]*?/\d{6}/\d+\.shtml)"', re.I)
# 分页链接：<文章ID>_2.shtml
PAGE_RE = re.compile(r'href="([^"]*?/\d{6}/\d+_(\d+)\.shtml)"', re.I)


def new_client(timeout: int = 25) -> httpx.Client:
    """直连 HTTP 客户端（不走系统代理，政务/行业站点国内直连更稳）。"""
    return httpx.Client(
        trust_env=False,
        timeout=timeout,
        headers={"User-Agent": UA},
        follow_redirects=True,
    )


def _get(client: httpx.Client, url: str, retry: int = 2) -> httpx.Response | None:
    """GET + 重试；返回 None 表示最终失败。"""
    for attempt in range(retry + 1):
        try:
            r = client.get(url)
            if r.status_code == 200:
                r.encoding = "utf-8"  # safehoo 声明 utf-8，httpx 猜测偶有偏差
                return r
            if r.status_code in (404, 403):
                return None  # 翻页到尽头，无需重试
        except Exception:  # noqa: BLE001
            if attempt == retry:
                return None
            time.sleep(1.0 * (attempt + 1))
    return None


def list_page_url(col_url: str, page: int) -> str:
    """栏目第 N 页 URL：第 1 页为栏目根，后续为 List_<N>.shtml。"""
    if page <= 1:
        return col_url
    return urljoin(col_url if col_url.endswith("/") else col_url + "/", f"List_{page}.shtml")


def discover(
    client: httpx.Client,
    col_url: str,
    max_pages: int = 30,
    delay: float = 0.3,
    quota: int = 0,
) -> list[str]:
    """翻页发现栏目下的详情页 URL（绝对地址，去重保序）。

    终止条件：达到 max_pages / 页面 404 / 该页无详情链接 / 达到 quota。
    """
    found: list[str] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        r = _get(client, list_page_url(col_url, page))
        if r is None:
            break
        hits = [urljoin(str(r.url), h) for h in DETAIL_RE.findall(r.text)]
        # 只保留与栏目同频道的详情页（列表页含推荐位、跨栏目链接）
        col_path = urlparse(col_url).path.rstrip("/")
        hits = [h for h in hits if urlparse(h).path.startswith(col_path)]
        fresh = [h for h in hits if h not in seen]
        if not fresh:
            break
        for h in fresh:
            seen.add(h)
            found.append(h)
            if quota and len(found) >= quota:
                return found[:quota]
        time.sleep(delay)
    return found


def source_id_of(url: str, prefix: str) -> str:
    """URL → 稳定 source_id：<prefix>-<栏目>-<文章ID>（同栏目同文唯一）。"""
    p = urlparse(url).path
    m = re.search(r"/([A-Za-z_]+)/(\d{6})/(\d+)\.shtml", p)
    if m:
        col, ym, aid = m.group(1), m.group(2), m.group(3)
        return f"{prefix}-{col.lower()}-{ym}-{aid}"
    return f"{prefix}-{re.sub(r'[^a-zA-Z0-9]+', '-', p).strip('-')[:60]}"


def fetch_detail(
    client: httpx.Client, url: str, max_sub_pages: int = 8, delay: float = 0.25
) -> str | None:
    """抓详情页 HTML；若正文分页（_2/_3…）则把各页 HTML 拼接返回。

    拼接策略：保留首页完整 HTML（含 <title>，治理脚本据此取标题），
    后续页只追加其正文容器片段，用注释分隔，交给 govern_html.py 统一抽取。
    """
    r = _get(client, url)
    if r is None:
        return None
    html = r.text

    # 找同文分页（_2.shtml…），按页码升序补抓
    subs: dict[int, str] = {}
    for href, num in PAGE_RE.findall(html):
        try:
            n = int(num)
        except ValueError:
            continue
        if 2 <= n <= max_sub_pages:
            subs[n] = urljoin(str(r.url), href)

    for n in sorted(subs):
        time.sleep(delay)
        sub = _get(client, subs[n])
        if sub is None:
            continue
        html += f"\n<!-- KB-SUBPAGE {n} -->\n" + sub.text
        # 后续页里可能出现更靠后的分页链接（_4、_5…），动态补充
        for href, num in PAGE_RE.findall(sub.text):
            try:
                k = int(num)
            except ValueError:
                continue
            if 2 <= k <= max_sub_pages and k not in subs:
                subs[k] = urljoin(str(sub.url), href)
    return html


def load_manifest_ids() -> set[str]:
    """已有 manifest 的 source_id 集合（跨脚本去重）。"""
    if not MANIFEST.exists():
        return set()
    ids = set()
    with MANIFEST.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["source_id"])
            except Exception:  # noqa: BLE001
                continue
    return ids


def append_manifest(records: list[dict]) -> None:
    """追加 manifest 记录（与 D3 字段同构，治理脚本会回填 title/字数）。"""
    if not records:
        return
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def crawl(
    doc_type: str,
    columns: dict[str, str],
    prefix: str,
    quota: int,
    max_pages: int = 30,
    delay: float = 0.3,
    dry_run: bool = False,
    per_column: int = 0,
) -> dict:
    """通用采集主流程：发现 → 抓取 → 落盘 → 写 manifest。

    Args:
        doc_type: laws / procedures / plans / cases（决定落盘子目录）
        columns:  {栏目名: 栏目URL}
        prefix:   source_id 前缀（如 shd-case）
        quota:    本次目标新增文件数（0=不限）
        per_column: 每栏目最多取多少条（0=平均分配 quota）
        max_pages: 每栏目最多翻几页
        dry_run:  只发现不下载

    Returns: 统计字典
    """
    out_dir = BASE / doc_type
    out_dir.mkdir(parents=True, exist_ok=True)
    exist_ids = load_manifest_ids()
    # 目录内已存在的 .htm（断点续传的第二道保险）
    exist_files = {f.stem for f in out_dir.glob("*.htm")}

    if per_column <= 0 and quota:
        # 平均分配 + 20% 余量（部分栏目可能提前枯竭）
        per_column = max(1, int(quota / max(1, len(columns)) * 1.2))

    stats = {"discovered": 0, "saved": 0, "skipped": 0, "failed": 0}
    new_records: list[dict] = []

    with new_client() as client:
        for col_name, col_url in columns.items():
            if quota and stats["saved"] >= quota:
                break
            print(f"\n--- [{doc_type}] 栏目：{col_name} ---")
            urls = discover(
                client, col_url, max_pages=max_pages, delay=delay,
                quota=per_column if per_column else 0,
            )
            stats["discovered"] += len(urls)
            print(f"  发现 {len(urls)} 条详情页")

            for u in urls:
                if quota and stats["saved"] >= quota:
                    break
                sid = source_id_of(u, prefix)
                target = out_dir / f"{sid}.htm"
                if sid in exist_ids or sid in exist_files or target.exists():
                    stats["skipped"] += 1
                    continue
                if dry_run:
                    print(f"  [DRY] {sid} <- {u}")
                    stats["saved"] += 1
                    continue
                html = fetch_detail(client, u, delay=delay)
                if html is None:
                    print(f"  [FAIL] {sid}")
                    stats["failed"] += 1
                    continue
                target.write_bytes(html.encode("utf-8", errors="ignore"))
                new_records.append(
                    {
                        "source_id": sid,
                        "url": u,
                        "local_hint": "",
                        "media_type": "text/html",
                        "doc_type": doc_type,
                        "status": "fetched",
                        "extract_status": "pending",
                        "column": col_name,
                    }
                )
                exist_ids.add(sid)
                stats["saved"] += 1
                if stats["saved"] % 20 == 0:
                    print(f"  …已保存 {stats['saved']} 份")
                time.sleep(delay)

    if not dry_run:
        append_manifest(new_records)
    print(
        f"\n[{doc_type}] 完成：发现 {stats['discovered']} / 新增 {stats['saved']} / "
        f"跳过 {stats['skipped']} / 失败 {stats['failed']}"
    )
    return stats


def count_md() -> dict:
    """统计四类目录当前 .md 数量（治理产物 = 知识库真实条数）。"""
    out = {}
    for dt in ["laws", "procedures", "plans", "cases"]:
        d = BASE / dt
        out[dt] = len(list(d.glob("*.md"))) if d.is_dir() else 0
    out["total"] = sum(v for k, v in out.items() if k != "total")
    return out


def build_argparser(desc: str, default_quota: int):
    """四个脚本共用的命令行参数。"""
    import argparse

    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--quota", type=int, default=default_quota, help="本次目标新增份数")
    ap.add_argument("--per-column", type=int, default=0, help="每栏目上限（0=按配额均分）")
    ap.add_argument("--max-pages", type=int, default=30, help="每栏目最多翻页数")
    ap.add_argument("--delay", type=float, default=0.3, help="请求间隔秒")
    ap.add_argument("--dry-run", action="store_true", help="只发现不下载")
    return ap
