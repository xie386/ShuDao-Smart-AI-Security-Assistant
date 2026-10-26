"""知识贡献业务（K01–K06）：提交 / 列表 / 详情 / 审核 / 审核预筛。

v2.0 大点 04 起（裁定 1–6）：
- 支持 `.html/.htm`；审核通过时先用 `kb_convert` 把原文件转成 markdown（MinerU 优先，失败兜底）；
- 清洗后 **≥`KB_CONTRIB_MIN_CHARS`（默认 1000）字**才允许入库；
- 入库前 `contrib_store` 做**双重查重**（同名文件/同标题），命中报「标题重复」；
- 落盘 `knowledge_src/{doc_type}/{文件名}.md` 并入库（doc=stem、source=相对路径），
  失败自动回滚（删临时文件 + 删已写入块），不留半份知识。

核心设计（对齐 hazard_service）：
- **K01 为文件上传**（multipart）：校验扩展名/大小 → 存盘 uploads/kb_contrib/ →
  解析文本（md/txt/docx/pdf）→ content=文件文本、file_intro=上传者编写的简介；
- **状态机由 service 强制流转**：待审核 → 已入库 / 已驳回（非法流转 400，权限不足 403）；
- **审核通过才入向量库**：调 knowledge.upsert_chunks（稳定 ID 增量 upsert，**不重建**）；
  doc=contrib_{id} 命名空间隔离，避免与官方源（laws/plans/... 文件名）冲突；
- 每一步写 knowledge_contrib_timeline（K03 进度），关键流转联动 H08 消息
  （Redis 队列，优雅降级；上传者收进度，admin 收待审通知）；
- 审核预筛（K05）调 knowledge_review_agent，**只辅助不替代**管理员决策。
"""
import logging
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, status, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.knowledge_contrib import KnowledgeContribTimeline, KnowledgeContribution
from src.models.user import User
from src.schemas.knowledge_contrib import (
    KnowledgeContribListOut,
    KnowledgeContribOut,
    ReviewRequest,
)
from src.services import contrib_store, kb_convert, kb_govern
from src.services.notification_service import (
    TYPE_KB_CONTRIB,
    NotificationService,
)

logger = logging.getLogger(__name__)

# 内容质量门槛：标题 2-128 字；文件内容 ≥50 字（过短视为碎片/无价值）；简介必填 2-500 字
_CONTENT_MIN = 50
_INTRO_MIN, _INTRO_MAX = 2, 500
_ALLOWED_EXTS = {e.strip().lower() for e in settings.KB_CONTRIB_EXTS.split(",") if e.strip()}
_MAX_FILE_BYTES = settings.KB_CONTRIB_MAX_MB * 1024 * 1024

VALID_DOC_TYPES = {"laws", "procedures", "plans", "cases"}


def _extract_file_text(path: Path) -> str:
    """按扩展名解析文件文本（md/txt 双编码兜底 / docx / pdf）。"""
    ext = path.suffix.lower()
    if ext in (".md", ".markdown", ".txt"):
        for enc in ("utf-8", "gbk"):
            try:
                return path.read_text(encoding=enc)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext == ".docx":
        from docx import Document  # lazy（全局已装 python-docx）

        return "\n".join(p.text for p in Document(str(path)).paragraphs)
    if ext in (".html", ".htm"):
        from bs4 import BeautifulSoup  # lazy

        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text("\n").strip()
    if ext == ".pdf":
        import pymupdf  # lazy（全局已装 PyMuPDF 1.28+，fitz 别名已弃用）

        doc = pymupdf.open(str(path))
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    return ""


class KnowledgeContribService:
    # ---------- K01 提交（文件上传） ----------

    @staticmethod
    def submit(
        db: Session,
        title: str,
        doc_type: str,
        intro: str,
        file: UploadFile,
        user_id: int,
    ) -> KnowledgeContribution:
        title = (title or "").strip()
        intro = (intro or "").strip()
        if not title:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="标题不能为空")
        if not (_INTRO_MIN <= len(intro) <= _INTRO_MAX):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"文件简介必填（{_INTRO_MIN}-{_INTRO_MAX} 字，当前 {len(intro)} 字）",
            )
        filename = (file.filename or "").strip()
        ext = Path(filename).suffix.lower()
        if ext not in _ALLOWED_EXTS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"不支持的文件格式：{ext or '无扩展名'}（支持 {'/'.join(sorted(_ALLOWED_EXTS))}）",
            )
        # 大小校验（读流截断到上限+1 检测超限）
        data = file.file.read(_MAX_FILE_BYTES + 1)
        if len(data) > _MAX_FILE_BYTES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文件超过 10MB 上限")
        if not data.strip():
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文件内容为空")

        # 存盘 uploads/kb_contrib/{uuid}{ext}（保留原扩展名便于解析与审核员下载查看）
        dest_dir = Path(settings.UPLOAD_DIR) / "kb_contrib"
        dest_dir.mkdir(parents=True, exist_ok=True)
        rel = f"kb_contrib/{uuid4().hex}{ext}"
        (dest_dir / Path(rel).name).write_bytes(data)

        # 解析文件文本（入库与分块用全文，不是简介）
        content = _extract_file_text(Path(settings.UPLOAD_DIR) / rel)
        if len(content.strip()) < _CONTENT_MIN:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"文件解析出的有效文本过短（至少 {_CONTENT_MIN} 字，当前 {len(content.strip())} 字）",
            )

        contrib = KnowledgeContribution(
            title=title[:128],
            content=content,
            file_intro=intro,
            file_path=rel,
            doc_type=doc_type if doc_type in VALID_DOC_TYPES else "laws",
            contributor_id=user_id,
            status="待审核",
            origin_filename=filename[:255],
            source_kind="user",
        )
        db.add(contrib)
        db.flush()
        KnowledgeContribService._timeline(db, contrib.id, "提交", user_id, note=f"上传文件 {filename}，等待管理员审核")
        db.commit()
        db.refresh(contrib)
        # H08：通知全部 admin（有待审贡献）
        admins = db.execute(select(User.id).where(User.role == "admin")).scalars().all()
        for admin_id in admins:
            NotificationService.push(
                admin_id,
                f"【知识待审】用户 #{user_id} 贡献知识：{contrib.title}（贡献 #{contrib.id}），请审核",
                msg_type=TYPE_KB_CONTRIB,
            )
        return contrib

    # ---------- K02 列表 ----------

    @staticmethod
    def list_for(
        db: Session,
        user_id: int,
        role: str,
        page: int = 1,
        page_size: int = 10,
        status_filter: Optional[str] = None,
        only_mine: bool = False,
    ) -> KnowledgeContribListOut:
        q = select(KnowledgeContribution)
        if role == "admin":
            # admin 看全部（可筛状态）
            if status_filter:
                q = q.where(KnowledgeContribution.status == status_filter)
        else:
            # 非 admin 只看自己（隐含待审核状态也可见，可跟踪进度）
            q = q.where(KnowledgeContribution.contributor_id == user_id)
            # 普通员工也支持按状态筛选（个人主页「只显示通过的」需 status_filter=已入库）
            if status_filter:
                q = q.where(KnowledgeContribution.status == status_filter)
        if only_mine:
            q = q.where(KnowledgeContribution.contributor_id == user_id)

        total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
        items = db.scalars(
            q.order_by(KnowledgeContribution.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return KnowledgeContribListOut(
            total=total,
            page=page,
            page_size=page_size,
            items=[KnowledgeContribOut.model_validate(c) for c in items],
        )

    # ---------- K03 详情 ----------

    @staticmethod
    def detail(db: Session, contrib_id: int, user_id: int, role: str) -> KnowledgeContribution:
        contrib = db.get(KnowledgeContribution, contrib_id)
        if not contrib:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="贡献记录不存在")
        if role != "admin" and contrib.contributor_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅贡献者本人或管理员可查看")
        return contrib

    @staticmethod
    def timeline(db: Session, contrib_id: int) -> list[KnowledgeContribTimeline]:
        return list(
            db.scalars(
                select(KnowledgeContribTimeline)
                .where(KnowledgeContribTimeline.contrib_id == contrib_id)
                .order_by(KnowledgeContribTimeline.created_at.asc())
            )
        )

    # ---------- K06 审核 ----------

    @staticmethod
    def review(db: Session, contrib_id: int, data: ReviewRequest, operator: dict) -> KnowledgeContribution:
        contrib = db.get(KnowledgeContribution, contrib_id)
        if not contrib:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="贡献记录不存在")
        if contrib.status != "待审核":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前状态[{contrib.status}]不允许审核（仅待审核可审核）",
            )

        if data.approved:
            # ① 原文件 → markdown（MinerU 优先，失败走内置兜底；裁定 4）
            raw_path = Path(settings.UPLOAD_DIR) / (contrib.file_path or "")
            if not contrib.file_path or not raw_path.is_file():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="原始文件不存在，无法转换（请让贡献者重新上传）",
                )
            try:
                conv = kb_convert.to_markdown(
                    raw_path, doc_type=contrib.doc_type, title=contrib.title
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"文件转换失败：{exc}",
                ) from exc

            # ② 清洗治理 + 质量校验（v2.0 大点 04：门槛提升到 KB_CONTRIB_MIN_CHARS）
            clean_text = kb_govern.clean_text(conv["md"])
            min_chars = settings.KB_CONTRIB_MIN_CHARS
            qc = kb_govern.quality_check(clean_text, min_chars=min_chars)
            if not qc["ok"]:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="文件未通过清洗治理：" + "；".join(qc["reasons"]),
                )

            # ③ 落盘 + 入库（先查重；失败自动回滚，裁定 2/6）
            try:
                stored = contrib_store.store_contribution(
                    db,
                    contrib,
                    clean_text,
                    origin=getattr(contrib, "source_kind", "user") or "user",
                    source_agent_id=contrib.source_agent_id,
                    convert_by=conv["converted_by"],
                )
                n = stored["chunks"]
            except contrib_store.ConflictError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"标题重复：{exc.detail}",
                ) from exc
            except Exception as exc:  # noqa: BLE001
                logger.exception("知识贡献入库失败 contrib=%s", contrib_id)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"入库失败：{exc}",
                ) from exc

            contrib.status = "已入库"
            contrib.review_by = operator["id"]
            contrib.review_note = data.note
            govern_note = f"清洗治理后 {qc['chars']} 字（{conv['converted_by']} 转换）"
            action = "审核通过"
            note = f"已入库（{n} 块，{govern_note}）" + (f"：{data.note}" if data.note else "")
            if conv["warnings"]:
                note += "｜提醒：" + "；".join(conv["warnings"])
        else:
            contrib.status = "已驳回"
            contrib.review_by = operator["id"]
            contrib.review_note = data.note
            action, note = "审核驳回", data.note or "不符合入库标准"

        KnowledgeContribService._timeline(db, contrib.id, action, operator["id"], note=note)
        db.commit()
        db.refresh(contrib)

        # H08：通知上传者（进度可见）
        NotificationService.push(
            contrib.contributor_id,
            f"【知识{'已入库' if data.approved else '被驳回'}】您的贡献 {contrib.title}（贡献 #{contrib.id}）"
            + (f"：{data.note}" if data.note else ""),
            msg_type=TYPE_KB_CONTRIB,
        )
        return contrib

    # ---------- K05 审核预筛（子智能体辅助，不写库） ----------

    @staticmethod
    def review_preview(db: Session, contrib_id: int) -> dict:
        contrib = db.get(KnowledgeContribution, contrib_id)
        if not contrib:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="贡献记录不存在")
        try:
            from src.ai.knowledge_review_agent import review_agent  # lazy

            return review_agent.review(contrib.title, contrib.content, contrib.doc_type)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI 预筛失败：{exc}",
            ) from exc

    # ---------- 内部工具 ----------

    @staticmethod
    def _timeline(
        db: Session,
        contrib_id: int,
        action: str,
        operator_id: int | None,
        note: Optional[str] = None,
        actor_kind: str = "human",
    ) -> None:
        db.add(
            KnowledgeContribTimeline(
                contrib_id=contrib_id,
                action=action,
                operator_id=operator_id,
                actor_kind=actor_kind,
                note=note,
            )
        )
