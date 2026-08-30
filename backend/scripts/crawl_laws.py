#!/usr/bin/env python
"""① laws 类采集：法律 / 法规 / 规章 / 规范性文件全文（安全管理网 Laws 频道）。

对应知识库 `knowledge_src/laws/`（法规·标准·规范类）。

⚠️ 选源踩坑记录（D6 实测，勿再改回）：
最初用 `Standard` 频道（技术标准 GB/AQ/JTG…），实测 **254/315 份是"附件下载页"**
——网页正文只有标题 + 一个 .doc/.pdf 下载链接，治理后 md 仅十几个字，
入库纯属噪声。改用 `Laws` 频道后抽样命中率 ~90%（正文 1000~70000 字），
因为法规类页面把条文全文直接排在正文容器里。

栏目说明（均已抽样验证有正文）：
- Law        法律全文（安全生产法、建筑法、公路法…）
- Statute    行政法规 / 条例
- Pact       部门规章 / 规程规范（含大部头，最长 7 万字）
- Notice     通知 / 通告类规范性文件
- Views      意见 / 办法 / 指导文件

用法（backend/ 目录，全局 Python）：
    PYTHONPATH= C:\\python\\python.exe scripts/crawl_laws.py --quota 300
    PYTHONPATH= C:\\python\\python.exe scripts/crawl_laws.py --dry-run --quota 20
"""
from kb_crawl_common import build_argparser, count_md, crawl

S = "https://www.safehoo.com"

COLUMNS = {
    # —— 法规全文（抽样 5/5 有效）——
    "法律全文": f"{S}/Laws/Law/",
    "行政法规条例": f"{S}/Laws/Statute/",
    "部门规章规程": f"{S}/Laws/Pact/",
    # —— 规范性文件（抽样 3~4/5 有效）——
    "通知通告": f"{S}/Laws/Notice/",
    "意见办法": f"{S}/Laws/Views/",
    "法规解读": f"{S}/Laws/Interpretation/",
}

if __name__ == "__main__":
    args = build_argparser(__doc__, default_quota=300).parse_args()
    crawl(
        doc_type="laws",
        columns=COLUMNS,
        prefix="shd-law",
        quota=args.quota,
        per_column=args.per_column,
        max_pages=args.max_pages,
        delay=args.delay,
        dry_run=args.dry_run,
    )
    print("当前四类 .md 统计：", count_md())
