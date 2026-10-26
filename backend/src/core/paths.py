"""账号沙箱路径的唯一事实源（v2.0 大点 02，仿 deep_search_pro/utils/paths.py 的归口模式）。

为什么要有这个模块
------------------
PPT Agent 会产出/下载很多文件：模板副本、检索到的图片、逐页截图、中间稿、成品 pptx、结构化日志。
如果每个模块各自拼路径，历史上出现过"写进 A、读的是 B"、清理账号时漏项的事故
（deep_search_pro 的 `utils/paths.py` 头注释记录过同类教训）。所以本模块是**唯一入口**：

    要 PPT 相关路径，来这里拿；不要在别处再拼一次。

目录树
------
    user_data/ppt/u{user_id}/templates/                       该账号的模板副本（跨任务复用）
    user_data/ppt/u{user_id}/tasks/t{task_id}/images/         本次下载的图片
    user_data/ppt/u{user_id}/tasks/t{task_id}/images/sources.json   图片授权与署名
    user_data/ppt/u{user_id}/tasks/t{task_id}/shots/          逐页截图 PNG
    user_data/ppt/u{user_id}/tasks/t{task_id}/drafts/         spec.json / outline.json / pages.json
    user_data/ppt/u{user_id}/tasks/t{task_id}/logs/           {task_id}.jsonl 结构化工作日志
    user_data/ppt/u{user_id}/tasks/t{task_id}/out/            成品 pptx

设计约束（写死在这里，别在调用点重复实现）
------------------------------------------
1. **本模块只给路径、不建目录、不写文件**；要落盘显式调用 `ensure_dir()`。
2. 账号/任务目录名**只由整数 id 拼**（`u17` / `t23`），天然免疫 `..`、`/`、盘符注入。
3. 任何来自用户或模型的**文件名**一律先过 `safe_name()`（只留 basename、拒绝 `..`），
   再用 `safe_join()` 校验解析后的真实路径仍在根目录内。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

#: 项目根（team/）：backend/src/core/paths.py → parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


from src.core.config import settings

#: 沙箱根（相对 backend/ 或绝对路径，由 settings.PPT_SANDBOX_ROOT 决定）
SANDBOX_ROOT = Path(settings.PPT_SANDBOX_ROOT)


def ensure_dir(p: str | os.PathLike) -> Path:
    """确保目录存在并返回它（路径函数保持"纯"，落盘必须显式调用本函数）。"""
    path = Path(p)
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_root(user_id: int | str) -> Path:
    """`user_data/ppt/u{user_id}` —— 账号级根目录。"""
    return SANDBOX_ROOT / f"u{int(user_id)}"


def templates_dir(user_id: int | str) -> Path:
    """账号级模板副本目录（跨任务复用）。"""
    return user_root(user_id) / "templates"


def task_root(user_id: int | str, task_id: int | str) -> Path:
    """`user_data/ppt/u{uid}/tasks/t{tid}` —— 单个任务的工作区。"""
    return user_root(user_id) / "tasks" / f"t{int(task_id)}"


def task_dir(user_id: int | str, task_id: int | str, kind: str) -> Path:
    """任务内的分类目录：images / shots / drafts / logs / out。"""
    if kind not in TASK_KINDS:
        raise ValueError(f"未知的任务目录类型: {kind}")
    return task_root(user_id, task_id) / kind


TASK_KINDS = ("images", "shots", "drafts", "logs", "out")

#: 清理端点允许删除的分类（`out` 单独由参数控制，默认保留成品）
CLEANABLE_KINDS = ("images", "shots", "drafts", "logs")


def out_path(user_id: int | str, task_id: int | str, topic: str) -> Path:
    """成品 pptx 路径：`out/u{uid}_t{tid}_{安全主题}.pptx`（带归属，防同名覆盖）。"""
    stem = safe_name(topic)[:60] or "ppt"
    return task_dir(user_id, task_id, "out") / f"u{int(user_id)}_t{int(task_id)}_{stem}.pptx"


def log_path(user_id: int | str, task_id: int | str) -> Path:
    """结构化工作日志（JSONL，一行一个事件）。"""
    return task_dir(user_id, task_id, "logs") / f"{int(task_id)}.jsonl"


# --------------------------------------------------------------------------- #
# 安全工具
# --------------------------------------------------------------------------- #
_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(name: str, *, fallback: str = "") -> str:
    """把任意字符串压成"可安全当文件名"的形式。

    - 只取最后一段（basename），去掉路径分隔符与 Windows 非法字符；
    - 拒绝 `.` / `..` / 空串（返回 fallback）；
    - 保留中文与空格（Office/Windows 都支持，用户可读性更好）。
    """
    raw = str(name or "").replace("\\", "/").split("/")[-1]
    cleaned = _UNSAFE.sub("_", raw).strip().strip(".")
    if not cleaned or cleaned in (".", ".."):
        return fallback
    return cleaned


def safe_join(root: str | os.PathLike, name: str) -> Path:
    """拼出 root 下的安全路径。

    两步防护：① `safe_name()` 只留 basename（`../x` 变 `x`，绝对路径也只剩文件名）；
    ② 解析后的真实路径必须仍在 root 内，否则抛 ValueError（正常情况走不到这一步，
    是给符号链接/异常路径留的兜底）。空名字直接拒绝，避免把 root 自己当文件返回。
    """
    root_path = Path(root).resolve()
    clean = safe_name(name)
    if not clean:
        raise ValueError(f"非法文件名: {name!r}")
    candidate = (root_path / clean).resolve()
    if candidate != root_path and root_path not in candidate.parents:
        raise ValueError(f"路径越界: {name}")
    return candidate


def is_within(path: str | os.PathLike, root: str | os.PathLike) -> bool:
    """path 解析后是否落在 root 内（给下载/静态资源做二次校验）。"""
    p, r = Path(path).resolve(), Path(root).resolve()
    return p == r or r in p.parents


# --------------------------------------------------------------------------- #
# 清理（用户自清理，裁定 10）
# --------------------------------------------------------------------------- #
def cleanup_task(
    user_id: int | str, task_id: int | str, keep_out: bool = True
) -> dict:
    """清空该任务的中间产物，默认保留成品目录。

    返回 {"removed": [目录名], "kept": [目录名], "files": 删除文件数}
    """
    removed: list[str] = []
    kept: list[str] = []
    count = 0
    for kind in TASK_KINDS:
        d = task_dir(user_id, task_id, kind)
        if kind == "out" and keep_out:
            kept.append(kind)
            continue
        if not d.exists():
            continue
        count += sum(1 for p in d.rglob("*") if p.is_file())
        shutil.rmtree(d, ignore_errors=True)
        removed.append(kind)
    return {"removed": removed, "kept": kept, "files": count}
