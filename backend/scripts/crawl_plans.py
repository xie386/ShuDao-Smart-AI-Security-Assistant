#!/usr/bin/env python
"""③ plans 类采集：应急预案 / 应急管理体系（安全管理网 Emergency 频道 + 应急管理部）。

对应知识库 `knowledge_src/plans/`（应急预案类）。

数据源：
1. 安全管理网 Emergency 频道（预案编制/应急体系/应急处置卡/应急案例）——主力上量；
2. 应急管理部 www.mem.gov.cn 应急预案栏目（权威补充，D3 已验证可直连）。

用法：
    PYTHONPATH= C:\\python\\python.exe scripts/crawl_plans.py --quota 250
"""
from kb_crawl_common import build_argparser, count_md, crawl

S = "https://www.safehoo.com"
MEM = "https://www.mem.gov.cn"

COLUMNS = {
    # —— 预案主力（安全管理网）——
    "应急预案编制": f"{S}/Emergency/Weave/",
    "应急管理体系": f"{S}/Emergency/System/",
    "应急处置卡": f"{S}/Emergency/yjczk/",
    "应急管理理论": f"{S}/Emergency/Theory/",
    "应急救援案例": f"{S}/Emergency/Case/",
    # —— 权威补充（应急管理部，栏目页含 t<日期>_<id>.shtml 详情）——
    "应急管理部-应急预案": f"{MEM}/fw/yjya/",
    "应急管理部-预案管理": f"{MEM}/fw/yjya/yjyagl/",
    "应急管理部-国家专项预案": f"{MEM}/fw/yjya/gjzxyjya/",
}

if __name__ == "__main__":
    args = build_argparser(__doc__, default_quota=250).parse_args()
    crawl(
        doc_type="plans",
        columns=COLUMNS,
        prefix="shd-plan",
        quota=args.quota,
        per_column=args.per_column,
        max_pages=args.max_pages,
        delay=args.delay,
        dry_run=args.dry_run,
    )
    print("当前四类 .md 统计：", count_md())
