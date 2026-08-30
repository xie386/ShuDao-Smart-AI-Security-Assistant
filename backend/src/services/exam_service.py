"""考试工坊业务服务：试卷任务状态机 + 出卷 + 导出。

流程（计划书 v2）：
    创建任务（status=生成中）→ exam_agent.generate_paper（主模型+RAG 依据）
    → 成功写 exam_content（status=已完成）/ 失败写 error（status=失败）
    → 导出：exam_export.export_paper 渲染 md/docx/pdf 落盘 uploads/exam/。
"""
import logging
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.ai.exam_agent import exam_agent
from src.models.exam_paper import ExamPaper
from src.schemas.exam import ExamPaperCreate
from src.services import exam_export

logger = logging.getLogger(__name__)

STATUS_DRAFT = "草稿"
STATUS_GENERATING = "生成中"
STATUS_DONE = "已完成"
STATUS_FAILED = "失败"


class ExamService:
    """考试工坊核心业务（状态机：草稿→生成中→已完成/失败）。"""

    # ---- 模板 ----

    def list_templates(self) -> list[dict]:
        """试卷模板列表（prompts.yml exam_templates.*）。"""
        return exam_agent.list_templates()

    # ---- 生成 ----

    def create_paper(self, db: Session, user_id: int, data: ExamPaperCreate) -> ExamPaper:
        """创建试卷任务并同步生成（LLM 调用约 30-60s，失败落 failed 保留记录）。"""
        paper = ExamPaper(
            paper_id=f"paper_{uuid.uuid4().hex[:12]}",
            title=data.title,
            template=data.template,
            input_type=data.input_type,
            input_text=data.input_text,
            input_file=data.input_file,
            status=STATUS_GENERATING,
            created_by=user_id,
        )
        db.add(paper)
        db.commit()
        db.refresh(paper)

        try:
            content = exam_agent.generate_paper(
                title=data.title,
                template_key=data.template,
                input_text=data.input_text,
                use_rag=data.use_rag,
            )
            paper.exam_content = _dump_json(content)
            paper.status = STATUS_DONE
            logger.info("试卷生成成功: %s（%d 题）", paper.paper_id, len(content.get("questions", [])))
        except Exception as exc:  # noqa: BLE001 —— 失败落库保留记录
            paper.status = STATUS_FAILED
            paper.error = str(exc)[:255]
            logger.warning("试卷生成失败: %s -> %s", paper.paper_id, exc)
        db.commit()
        db.refresh(paper)
        return paper

    # ---- 查询 ----

    def list_papers(self, db: Session, user_id: int, page: int = 1, page_size: int = 10):
        """我的试卷列表（按创建时间倒序）。"""
        base = select(ExamPaper).where(ExamPaper.created_by == user_id)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        items = db.scalars(
            base.order_by(ExamPaper.id.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return items, total

    def get_paper(self, db: Session, user_id: int, paper_id: str) -> ExamPaper:
        """详情（本人可见，404 处理不存在/他人）。"""
        paper = db.scalar(select(ExamPaper).where(ExamPaper.paper_id == paper_id))
        if paper is None or paper.created_by != user_id:
            raise HTTPException(status_code=404, detail="试卷不存在")
        return paper

    # ---- 导出 ----

    def export_paper(self, db: Session, user_id: int, paper_id: str, fmt: str) -> dict:
        """按格式导出试卷文件，更新 export_format/file_path，返回下载信息。"""
        paper = self.get_paper(db, user_id, paper_id)
        if paper.status != STATUS_DONE or not paper.exam_content:
            raise HTTPException(status_code=400, detail=f"试卷未生成成功（当前状态: {paper.status}），无法导出")
        content = _load_json(paper.exam_content)
        if not content:
            raise HTTPException(status_code=500, detail="试卷内容解析失败")

        try:
            rel = exam_export.export_paper(content, fmt, paper.paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        paper.export_format = fmt
        paper.file_path = rel
        db.commit()
        return {"paper_id": paper.paper_id, "format": fmt, "file_url": f"/exam-files/{paper.paper_id}.{fmt}"}


# 模块级单例（对齐 PptService 用法）
exam_service = ExamService()


def _dump_json(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def _load_json(text: str) -> Optional[dict]:
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
