#!/usr/bin/env python
"""④ cases 类采集：事故案例 / 事故调查报告（安全管理网 Case 频道 + 应急管理部）。

对应知识库 `knowledge_src/cases/`（事故案例类，现有 64 份）。

栏目选取原则（对齐蜀道隐患等级与工地高频事故类型）：
- 坍塌 / 高处坠落 / 起重伤害 / 物体打击 / 机械伤害 / 触电 / 车辆伤害
  —— 正是公路桥隧工地「五大伤害」，与隐患模块的分类语义一致，优先级最高；
- 其余（火灾、爆炸、中毒、淹溺、粉尘等）作为扩量与横向借鉴。

数据源附注：安全管理网事故案例正文常分页（`_2.shtml`），
公共库 `fetch_detail()` 会自动跟随合并，避免正文被截断。

用法：
    PYTHONPATH= C:\\python\\python.exe scripts/crawl_cases.py --quota 400
"""
from kb_crawl_common import build_argparser, count_md, crawl

S = "https://www.safehoo.com"
MEM = "https://www.mem.gov.cn"

COLUMNS = {
    # —— 工地「五大伤害」优先 ——
    "坍塌事故": f"{S}/Case/Case/Collapse/",
    "高处坠落": f"{S}/Case/Case/Drop/",
    "起重伤害": f"{S}/Case/Case/Crane/",
    "物体打击": f"{S}/Case/Case/Hit/",
    "机械伤害": f"{S}/Case/Case/Machine/",
    "触电事故": f"{S}/Case/Case/Electric/",
    "车辆伤害": f"{S}/Case/Case/Vehicle/",
    "道路交通事故": f"{S}/Case/Case/Roadvehicle/",
    # —— 其他类型扩量 ——
    "火灾事故": f"{S}/Case/Case/Blaze/",
    "爆炸事故": f"{S}/Case/Case/Blow/",
    "中毒窒息": f"{S}/Case/Case/Poison/",
    "淹溺事故": f"{S}/Case/Case/Drowned/",
    "灼烫事故": f"{S}/Case/Case/Scorch/",
    "粉尘爆炸": f"{S}/Case/Case/dustexplosion/",
    "容器爆炸": f"{S}/Case/Case/Container/",
    "受限空间": f"{S}/Case/Case/krqtbz/",
    "易燃液体": f"{S}/Case/Case/Flammableliquid/",
    "矿山事故": f"{S}/Case/Case/Mine/",
    "其他事故": f"{S}/Case/Case/Other/",
    # —— 权威事故调查报告（应急管理部；按年份归档，D3 已验证）——
    "应急部-特别重大事故调查报告": f"{MEM}/gk/sgcc/tbzdsgdcbg/",
    "应急部-事故挂牌督办": f"{MEM}/gk/sgcc/sggpdbqk/",
}

if __name__ == "__main__":
    args = build_argparser(__doc__, default_quota=400).parse_args()
    crawl(
        doc_type="cases",
        columns=COLUMNS,
        prefix="shd-case",
        quota=args.quota,
        per_column=args.per_column,
        max_pages=args.max_pages,
        delay=args.delay,
        dry_run=args.dry_run,
    )
    print("当前四类 .md 统计：", count_md())
