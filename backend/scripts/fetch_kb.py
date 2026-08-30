#!/usr/bin/env python
"""知识库源文档采集：按 kb_manifest.jsonl 下载到 knowledge_src/raw/<doc_type>/。

用法（在 backend/ 目录下，使用全局 Python）：
    PYTHONPATH= C:\\python\\python.exe scripts/fetch_kb.py                # 全量抓取
    PYTHONPATH= C:\\python\\python.exe scripts/fetch_kb.py --limit 10    # 只抓前 10 条
    PYTHONPATH= C:\\python\\python.exe scripts/fetch_kb.py --only <source_id>  # 抓单条

特性：
- 断点续传：raw/ 下已存在同 source_id 文件则跳过（可反复重跑）；
- 归类存储：laws / procedures / plans / cases 四类子目录（D3 提取管线直接扫描）；
- 失败不中断：单条失败记日志继续，最后汇总。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

BASE = Path(__file__).resolve().parent.parent / "knowledge_src"
MANIFEST = BASE / "kb_manifest.jsonl"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
EXT = {
    "text/html": ".htm",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
}


def fetch_attachments(client: httpx.Client) -> None:
    """解析已下载 HTML 内的附件链接（.pdf/.doc/.docx）并下载。

    政务站常见"通知页+附件正文"模式：网页只有通知头，正文在附件里。
    附件保存为 <source_id>-attachment-N.<ext>，与源 htm 同目录。
    """
    from urllib.parse import urljoin

    recs = [json.loads(l) for l in MANIFEST.open(encoding="utf-8") if l.strip()]
    url_map = {r["source_id"]: r["url"] for r in recs if r.get("url")}

    ok = skip = fail = 0
    for dt in ["laws", "cases", "plans", "procedures"]:
        d = BASE / dt
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.htm")):
            page_url = url_map.get(f.stem)
            if not page_url:
                continue
            raw = f.read_bytes().decode("utf-8", errors="ignore")
            links = re.findall(r'href="([^"]+\.(?:pdf|docx?|wps|ofd))"', raw, re.I)
            seen = set()
            for i, rel in enumerate(links, 1):
                if rel in seen:
                    continue
                seen.add(rel)
                target = BASE / dt / f"{f.stem}-attachment-{i}{Path(rel).suffix.lower()}"
                if target.exists():
                    print(f"[跳过] {target.name} 已存在")
                    skip += 1
                    continue
                try:
                    resp = client.get(urljoin(page_url, rel))
                    resp.raise_for_status()
                    target.write_bytes(resp.content)
                    print(
                        f"[OK] {f.stem} 附件{i} -> {target.name} ({len(resp.content) // 1024}KB)"
                    )
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"[FAIL] {f.stem} 附件{i}: {type(exc).__name__} {str(exc)[:60]}")
                    fail += 1
    print(f"\n附件补抓完成: 成功 {ok} / 跳过 {skip} / 失败 {fail}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 条（0=全部）")
    ap.add_argument("--only", default="", help="只抓指定 source_id")
    ap.add_argument("--attachments", action="store_true", help="解析 HTML 内附件链接并下载")
    args = ap.parse_args()

    recs = [json.loads(l) for l in MANIFEST.open(encoding="utf-8") if l.strip()]

    # 附件模式：不走主清单循环
    if args.attachments:
        with httpx.Client(
            trust_env=False, timeout=30, headers={"User-Agent": UA}, follow_redirects=True
        ) as client:
            fetch_attachments(client)
        return

    if args.only:
        recs = [r for r in recs if r["source_id"] == args.only]
    if args.limit:
        recs = recs[: args.limit]

    ok = skip = fail = 0
    # 注意：不走系统代理（trust_env=False）。政务网站国内直连即可；
    # 本机代理转发 http 政务站点曾出现 502（实测 hcq.gov.cn 直连 200）。
    with httpx.Client(
        trust_env=False, timeout=30, headers={"User-Agent": UA}, follow_redirects=True
    ) as client:
        for r in recs:
            if r["status"] == "local-missing" or not r["url"]:
                print(f"[跳过] {r['source_id']}: 本地文件缺失（{r.get('local_hint','')}，需组员提供）")
                skip += 1
                continue
            # 直接归档到 knowledge_src/<doc_type>/ 四类子目录（D3 提取管线直接扫描）
            target = BASE / r["doc_type"] / (
                r["source_id"] + EXT.get(r["media_type"], ".bin")
            )
            if target.exists():
                print(f"[跳过] {r['source_id']}: 已存在 {target.relative_to(BASE)}")
                skip += 1
                continue
            try:
                resp = client.get(r["url"])
                resp.raise_for_status()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(resp.content)
                print(
                    f"[OK] {r['source_id']} -> {target.relative_to(BASE)} "
                    f"({len(resp.content) // 1024}KB)"
                )
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[FAIL] {r['source_id']}: {type(exc).__name__} {str(exc)[:80]}")
                fail += 1

    print(f"\n完成: 成功 {ok} / 跳过 {skip} / 失败 {fail}（共 {len(recs)}）")


if __name__ == "__main__":
    main()
