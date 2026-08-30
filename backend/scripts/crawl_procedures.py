#!/usr/bin/env python
"""② procedures 类采集：安全操作规程 / 规章制度 / 管理制度（安全管理网 Manage 频道）。

对应知识库 `knowledge_src/procedures/`（操作规程·管理制度类，当前仅 1 份，缺口最大）。

栏目选取原则：
- 分行业安全管理（Stew/*）覆盖交通、建筑、机械、电气、特种设备等；
- 规章制度（System）、隐患排查（hazard/Check）、风险管控（Control）与项目
  隐患模块（H01–H08）业务语义最贴合，优先级最高。

用法：
    PYTHONPATH= C:\\python\\python.exe scripts/crawl_procedures.py --quota 300
"""
from kb_crawl_common import build_argparser, count_md, crawl

S = "https://www.safehoo.com"

COLUMNS = {
    # —— 与隐患排查/风险管控业务直接相关（优先）——
    "规章制度": f"{S}/Manage/System/",
    "隐患排查治理": f"{S}/Manage/hazard/",
    "安全检查": f"{S}/Manage/Check/",
    "风险管控": f"{S}/Manage/Control/",
    # —— 交通/建筑行业安全管理 ——
    "交通运输安全管理": f"{S}/Manage/Stew/Traffic/",
    "建筑施工安全管理": f"{S}/Manage/Stew/Build/",
    "特种设备安全管理": f"{S}/Manage/Stew/Particular/",
    "机械行业安全管理": f"{S}/Manage/Stew/Machine/",
    "电力安全管理": f"{S}/Manage/Stew/Electric/",
    "消防安全管理": f"{S}/Manage/Stew/Fire/",
    # —— 管理通用 ——
    "安全教育培训": f"{S}/Manage/Edu/",
    "事故处理": f"{S}/Manage/Dispose/",
    "政府安全监管": f"{S}/Manage/Gov/",
    "集团安全管理": f"{S}/Manage/Group/",
    # —— 其他行业兜底 ——
    "化工安全管理": f"{S}/Manage/Stew/Chemical/",
    "冶金安全管理": f"{S}/Manage/Stew/Metallurgy/",
    "爆破安全管理": f"{S}/Manage/Stew/Burst/",
    "其他行业管理": f"{S}/Manage/Stew/Else/",
    "其他管理内容": f"{S}/Manage/Else/",
}

if __name__ == "__main__":
    args = build_argparser(__doc__, default_quota=300).parse_args()
    crawl(
        doc_type="procedures",
        columns=COLUMNS,
        prefix="shd-proc",
        quota=args.quota,
        per_column=args.per_column,
        max_pages=args.max_pages,
        delay=args.delay,
        dry_run=args.dry_run,
    )
    print("当前四类 .md 统计：", count_md())
