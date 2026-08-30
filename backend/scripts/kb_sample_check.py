import random
from pathlib import Path

random.seed(7)
B = Path("knowledge_src")
for dt in ["laws", "procedures", "plans", "cases"]:
    fs = sorted(B.joinpath(dt).glob("*.md"))
    print(f"===== {dt} ({len(fs)} 份) 抽样 3 =====")
    for f in random.sample(fs, min(3, len(fs))):
        t = f.read_text(encoding="utf-8", errors="ignore")
        lines = t.split("\n")
        title = lines[0].lstrip("# ")
        body = " ".join(lines[1:]).strip()[:110]
        print(f"  [{len(t):6}字] {title[:46]}")
        print(f"          {body}")
    print()

print("=== 交通/公路主题覆盖度（含关键词的文档数）===")
kws = ["公路", "桥梁", "隧道", "交通", "施工", "隐患", "高处坠落", "起重", "安全生产"]
allmd = []
for dt in ["laws", "procedures", "plans", "cases"]:
    allmd += list(B.joinpath(dt).glob("*.md"))
texts = {f: f.read_text(encoding="utf-8", errors="ignore") for f in allmd}
for kw in kws:
    n = sum(1 for t in texts.values() if kw in t)
    print(f"  {kw:10} {n:5} 份")
