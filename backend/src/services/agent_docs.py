"""Agent 方法论文档读写（v2.0 大点 04 · 裁定 9）。

目录：`KB_AGENT_DOCS_DIR`（默认 `Methodology`，相对项目根 `team/`）。
文件：`Methodology.md`（方法论，注入网搜工具）、`cases.md`（案例库，一行一条）。
版本控制：仓库只带 `.example`，实体文件已 gitignore（运行期数据）。

「AI 写方法论」：读 `cases.md` → 一次 LLM（`prompts.yml::methodology_writer`）→ 覆盖 `Methodology.md`，
旧内容留 `.bak`；版本号 = 内容 sha1 前 8 位，供 `agent_runs` 记录追溯。
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path

from src.core.config import settings
from src.core.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

METHODOLOGY = "Methodology.md"
CASES = "cases.md"
NAMES = (METHODOLOGY, CASES)
_MAX_CASES_CHARS = 20000  # 喂给 LLM 的案例上限（超出取最近部分）


def docs_dir() -> Path:
    d = Path(settings.KB_AGENT_DOCS_DIR)
    return d if d.is_absolute() else PROJECT_ROOT / d


def path_of(name: str) -> Path:
    if name not in NAMES:
        raise ValueError(f"未知方法论文档: {name}（可选 {NAMES}）")
    return docs_dir() / name


def version() -> str:
    """方法论版本 = 内容 sha1 前 8 位（文件不存在时 `none`）。"""
    p = path_of(METHODOLOGY)
    if not p.is_file():
        return "none"
    return hashlib.sha1(p.read_bytes()).hexdigest()[:8]


def read(name: str = METHODOLOGY) -> dict:
    p = path_of(name)
    if not p.is_file():
        example = p.with_suffix(p.suffix + ".example")
        content = example.read_text(encoding="utf-8") if example.is_file() else ""
        return {"name": name, "content": content, "exists": False, "version": "none",
                "updated_at": None, "from_example": example.is_file()}
    return {
        "name": name,
        "content": p.read_text(encoding="utf-8"),
        "exists": True,
        "version": version() if name == METHODOLOGY else hashlib.sha1(p.read_bytes()).hexdigest()[:8],
        "updated_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
        "from_example": False,
    }


def write(name: str, content: str, *, keep_backup: bool = True) -> dict:
    """写文档（保留 .bak，便于回滚）。返回 {name, version, chars}。"""
    p = path_of(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if keep_backup and p.is_file():
        shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
    p.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    ver = version() if name == METHODOLOGY else hashlib.sha1(p.read_bytes()).hexdigest()[:8]
    logger.info("方法论更新 %s → %s（%d 字）", name, ver, len(content))
    return {"name": name, "version": ver, "chars": len(content)}


def append_case(line: str) -> dict:
    """追加一条案例（一行一条；重复行跳过）。"""
    line = " ".join((line or "").split())
    if not line:
        raise ValueError("案例内容为空")
    p = path_of(CASES)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = p.read_text(encoding="utf-8") if p.is_file() else ""
    if line in existing:
        return {"appended": False, "chars": len(existing)}
    with p.open("a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(line + "\n")
    return {"appended": True, "chars": len(existing) + len(line) + 1}


def update_case(contrib_id: int, verdict: str, reason: str = "", *,
                url: str = "", gap: str = "") -> dict:
    """把案例库里 `#<id>` 那条的结论从「已提交待审」改成实际审核结果。

    贡献经管理员处理后，案例库要能反映"这条到底行不行、为什么"，
    否则方法论自进化读到全是"待审核"，学不到东西。
    """
    p = path_of(CASES)
    if not p.is_file():
        return {"updated": False, "reason": "案例库文件不存在"}
    label = {"已入库": "审核通过", "已驳回": "审核驳回"}.get(verdict, verdict)
    tail = f"{label} #{contrib_id}"
    if reason:
        tail += "：" + " ".join(reason.split())[:120]

    lines = p.read_text(encoding="utf-8").splitlines()
    hit = 0
    marker = f"#{contrib_id}"
    for i, ln in enumerate(lines):
        if not ln.startswith("20") or marker not in ln:
            continue
        head, sep, _old = ln.rpartition("|")
        if not sep or marker not in _old:
            continue
        lines[i] = f"{head.rstrip()} | {tail}"
        hit += 1
    if hit:
        p.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
        return {"updated": True, "lines": hit, "tail": tail}

    # 案例库里没有这条（提交时没写案例 / 案例被清理）→ 补一条完整记录，别让结论丢失
    import time as _time

    line = " | ".join([
        _time.strftime("%Y-%m-%d"),
        (url or "（来源未记录）")[:160],
        f"缺口「{(gap or '（未记录）')[:40]}」",
        "补记",
        tail,
    ])
    append_case(line)
    return {"updated": False, "lines": 0, "tail": tail, "appended": True}


def recent_cases(limit_chars: int = _MAX_CASES_CHARS) -> str:
    """取最近的部分案例（文件过大时保留末尾）。"""
    p = path_of(CASES)
    if not p.is_file():
        return ""
    text = p.read_text(encoding="utf-8")
    return text[-limit_chars:]


def regenerate() -> dict:
    """「AI 写方法论」：案例库 → 方法论。失败抛异常（由路由转 502）。"""
    cases = recent_cases()
    if not cases.strip():
        raise ValueError("案例库为空，先积累案例再生成方法论")
    from src.ai.llm_client import llm_client
    from src.ai.prompts import render

    current = read(METHODOLOGY)["content"]
    prompt = render("methodology_writer", cases=cases, current=current[-6000:] or "（无）")
    out = llm_client.chat([{"role": "system", "content": prompt}], temperature=0.2)
    text = (out or "").strip()
    if len(text) < 200:
        raise RuntimeError(f"生成内容过短（{len(text)} 字），已放弃写入")
    if not text.startswith("#"):
        text = "# Agent 知识采集方法论\n\n" + text
    saved = write(METHODOLOGY, text)
    saved["cases_used"] = len(cases)
    return saved
