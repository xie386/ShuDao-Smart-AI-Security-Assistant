"""考试工坊模块契约（计划书 v2）。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 试卷模板 key（prompts.yml exam_templates.*）
EXAM_TEMPLATES = ("general", "law", "case")
# 导出格式
EXPORT_FORMATS = ("md", "docx", "pdf")


class ExamPaperCreate(BaseModel):
    """创建并生成试卷：提示词（或文档文本）→ 出卷子智能体 → 试卷 JSON。

    input_type=document 时，前端负责读取 md/txt 文件内容并传入 input_text
    （后端不接收 multipart，保持单 JSON 端点；input_file 仅存原始文件名展示）。
    """

    title: str = Field(min_length=2, max_length=128, description="试卷名称")
    template: str = Field(default="general", description=f"试卷模板：{'/'.join(EXAM_TEMPLATES)}")
    input_type: str = Field(default="prompt", pattern="^(prompt|document)$")
    input_text: str = Field(min_length=10, max_length=5000, description="出题提示词或文档文本（10~5000 字）")
    input_file: Optional[str] = Field(default=None, max_length=128, description="原始文件名（仅展示）")
    use_rag: bool = Field(default=True, description="是否引用知识库依据（hybrid_retrieve 只读）")
    export_format: str = Field(default="md", description=f"导出格式：{'/'.join(EXPORT_FORMATS)}")

    @field_validator("template")
    @classmethod
    def _check_template(cls, v: str) -> str:
        if v not in EXAM_TEMPLATES:
            raise ValueError(f"模板必须是 {'/'.join(EXAM_TEMPLATES)} 之一")
        return v

    @field_validator("export_format")
    @classmethod
    def _check_format(cls, v: str) -> str:
        v = v.lower()
        if v not in EXPORT_FORMATS:
            raise ValueError(f"导出格式必须是 {'/'.join(EXPORT_FORMATS)}")
        return v


class ExamExportRequest(BaseModel):
    """按格式导出试卷文件。"""

    format: str = Field(default="md", description=f"导出格式：{'/'.join(EXPORT_FORMATS)}")

    @field_validator("format")
    @classmethod
    def _check_format(cls, v: str) -> str:
        v = v.lower()
        if v not in EXPORT_FORMATS:
            raise ValueError(f"导出格式必须是 {'/'.join(EXPORT_FORMATS)}")
        return v


class ExamPaperOut(BaseModel):
    """试卷任务响应（列表/详情共用）。"""

    model_config = ConfigDict(from_attributes=True)

    paper_id: str
    title: str
    template: str
    input_type: str
    exam_content: Optional[str] = None  # 试卷 JSON 字符串（前端自行 parse）
    export_format: Optional[str] = None
    file_path: Optional[str] = None  # 相对路径（前端拼 /exam-files/）
    status: str
    error: Optional[str] = None
    created_at: datetime


class ExamPaperListOut(BaseModel):
    """我的试卷列表（分页）。"""

    items: list[ExamPaperOut]
    total: int
    page: int
    page_size: int


class ExamTemplateOut(BaseModel):
    """试卷模板信息（前端选择卡片）。"""

    key: str
    name: str
    description: str
    distribution: str
    total_score: int
