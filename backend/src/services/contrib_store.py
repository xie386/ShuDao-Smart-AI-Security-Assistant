"""贡献落盘与入库（v2.0 大点 04 · 裁定 1/2/6）。

职责：把"审核通过的贡献"写成 `knowledge_src/{doc_type}/{文件名}.md` 并入库（doc=文件名 stem、
source=相对路径），保证 **三种失败都不留脏数据**：

1. 查重（同名文件 / 同标题）→ 抛 `ConflictError`，不写盘；
2. 写盘用临时名 `.tmp_{uuid}.md`，入库成功才 `replace` 成最终名；
3. 入库中途失败 → 删临时文件 + 删该 doc 已写入的块（防止半份知识留在库里）。

对外只有一个入口：`store_contribution(db, contrib, md)`。
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ai.knowledge import upsert_file
from src.core.config import settings
from src.models.knowledge_contrib import KnowledgeContribution

logger = logging.getLogger(__name__)

SUBDIRS = ("laws", "procedures", "plans", "cases")
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class ConflictError(Exception):
    """文件名或标题冲突（审核端展示为"标题重复"）。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


# --------------------------------------------------------------------------- #
# 路径与命名
# --------------------------------------------------------------------------- #
def kb_root() -> Path:
    return Path(settings.KNOWLEDGE_SRC_DIR)


def normalize_stem(raw: str, limit: int = 60) -> str:
    """原始文件名 → md 文件名（去扩展名、去非法字符、压缩空白、截断）。"""
    cleaned = (raw or "").replace("\\", "/")
    cleaned = cleaned.split("/")[-1]          # 去掉可能的目录前缀，再取文件名
    name = Path(cleaned.strip()).stem
    name = _BAD_CHARS.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:limit] or "未命名文档"


def target_path(doc_type: str, stem: str) -> Path:
    if doc_type not in SUBDIRS:
        raise ValueError(f"未知 doc_type: {doc_type}")
    return kb_root() / doc_type / f"{stem}.md"


def rel_source(path: Path) -> str:
    """Chroma 里的 source：相对 backend/ 的 `knowledge_src/{type}/{file}`（正斜杠）。"""
    try:
        rel = path.resolve().relative_to(kb_root().resolve().parent)
        return str(rel).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


# --------------------------------------------------------------------------- #
# 查重（裁定 2）
# --------------------------------------------------------------------------- #
def _first_heading(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                s = line.strip()
                if s.startswith("#"):
                    return s.lstrip("#").strip()
                if s:
                    return s[:80]
    except OSError:
        pass
    return ""


def dir_titles(doc_type: str) -> dict[str, str]:
    """目标目录内 标题 → 文件名（用于查重）。"""
    out: dict[str, str] = {}
    d = kb_root() / doc_type
    if not d.is_dir():
        return out
    for f in sorted(d.iterdir()):
        if f.is_file() and f.suffix.lower() in {".md", ".txt"}:
            title = _first_heading(f)
            if title:
                out[title] = f.name
    return out


def db_titles(db: Session, doc_type: str) -> dict[str, str]:
    """DB 里已入库贡献的 标题 → md 文件名。"""
    rows = db.scalars(
        select(KnowledgeContribution).where(
            KnowledgeContribution.status == "已入库",
            KnowledgeContribution.doc_type == doc_type,
        )
    ).all()
    out: dict[str, str] = {}
    for r in rows:
        if r.title:
            out[r.title.strip()] = (r.md_path or "")
    return out


def find_conflict(db: Session, doc_type: str, title: str, stem: str) -> dict | None:
    """返回 None 表示无冲突；否则 {kind: file|title, detail}。"""
    dst = target_path(doc_type, stem)
    if dst.exists():
        return {"kind": "file", "detail": f"同名文件已存在：{dst.name}"}
    t = (title or "").strip()
    if not t:
        return None
    existing = dir_titles(doc_type)
    if t in existing:
        return {"kind": "title", "detail": f"标题重复：{existing[t]}（{t}）"}
    from_db = db_titles(db, doc_type)
    if t in from_db:
        return {"kind": "title", "detail": f"标题重复：已入库贡献 #{from_db[t]}（{t}）"}
    return None


# --------------------------------------------------------------------------- #
# 落盘 + 入库
# --------------------------------------------------------------------------- #
def remove_chunks(doc: str) -> int:
    """删掉某个 doc 命名空间的全部块（回滚/清理用），返回删除前的块数。"""
    from src.ai.knowledge import get_collection

    coll = get_collection()
    try:
        hit = coll.get(where={"doc": doc}, limit=10000)
        n = len(hit.get("ids") or [])
    except Exception:  # noqa: BLE001
        n = 0
    if n:
        coll.delete(where={"doc": doc})
    return n


def store_contribution(
    db: Session,
    contrib: KnowledgeContribution,
    md: str,
    *,
    origin: str = "user",
    source_agent_id: int | None = None,
    origin_urls: list[str] | None = None,
    convert_by: str = "builtin",
    stem: str | None = None,
) -> dict:
    """写盘 + 入库 + 更新贡献记录字段（不 commit，由调用方决定事务边界）。

    返回 {"path": 相对路径, "absolute": str, "chunks": n, "stem": stem}
    失败时抛异常，磁盘与向量库不留残留。
    """
    doc_type = contrib.doc_type
    stem = stem or normalize_stem(contrib.origin_filename or contrib.title)
    final = target_path(doc_type, stem)

    conflict = find_conflict(db, doc_type, contrib.title, stem)
    if conflict:
        raise ConflictError(conflict["kind"], conflict["detail"])

    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f".tmp_{uuid.uuid4().hex}.md")
    tmp.write_text(md, encoding="utf-8")

    meta = {"contrib_id": contrib.id, "origin": origin}
    if source_agent_id:
        meta["source_agent_id"] = source_agent_id
    chunks = 0
    try:
        chunks = upsert_file(tmp, doc_type, stem=stem, source=rel_source(final), extra_meta=meta)
        tmp.replace(final)  # 入库成功才改名（同目录 rename 为原子操作）
    except Exception:
        logger.exception("贡献落盘/入库失败 contrib=%s stem=%s", contrib.id, stem)
        tmp.unlink(missing_ok=True)
        if chunks:
            try:
                remove_chunks(stem)
            except Exception:  # noqa: BLE001 —— 回滚尽力而为
                logger.exception("回滚块失败 doc=%s", stem)
        raise

    contrib.md_path = rel_source(final)
    contrib.chunk_count = chunks
    contrib.convert_by = convert_by
    contrib.source_kind = origin  # type: ignore[assignment]
    contrib.source_agent_id = source_agent_id
    if origin_urls:
        import json

        contrib.origin_urls = json.dumps(origin_urls, ensure_ascii=False)
    db.add(contrib)
    logger.info(
        "贡献已入库 contrib=%s → %s（%d 块，convert=%s，origin=%s）",
        contrib.id, contrib.md_path, chunks, convert_by, origin,
    )
    return {"path": contrib.md_path, "absolute": str(final), "chunks": chunks, "stem": stem}
