"""试卷生成记录模型：对齐 shudao_safety.exam_papers 表（考试工坊模块）。

业务（考试工坊实现计划书 v2）：提示词/文档输入 → 出卷子智能体生成试卷+参考答案
（exam_content 存结构化 JSON）→ 导出 md/docx/pdf（file_path 落盘）。
模式对齐 ppt_tasks：状态机（草稿→生成中→已完成/失败）+ file_path。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class ExamPaper(Base):
    __tablename__ = "exam_papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(String(32), unique=True)  # paper_+uuid12，导出文件名/详情用
    title: Mapped[str] = mapped_column(String(128))
    template: Mapped[str] = mapped_column(String(32), default="general")  # prompts.yml 模板 key
    input_type: Mapped[str] = mapped_column(Enum("prompt", "document"), default="prompt")
    input_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 提示词或文档提取文本
    input_file: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # 上传文档相对路径
    exam_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 试卷结构 JSON（题目数组+答案）
    export_format: Mapped[Optional[str]] = mapped_column(Enum("md", "docx", "pdf"), nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # 最近导出文件路径
    status: Mapped[str] = mapped_column(
        Enum("草稿", "生成中", "已完成", "失败"), default="草稿"
    )
    error: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ExamPaper id={self.id} title={self.title!r} status={self.status!r}>"
