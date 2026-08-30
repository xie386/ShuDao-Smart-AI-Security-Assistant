"""PPT 工坊模块契约（P01–P07，D4）。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 模板选项（P04：与 ppt_baidu.TEMPLATE_MAP 键一致）
TEMPLATES = ("business", "simple", "tech")
# 任务状态机（PptTask.status 枚举：草稿/生成中/已完成/失败）
STATUSES = ("草稿", "生成中", "已完成", "失败")


class PptCreate(BaseModel):
    """P01 新建 PPT 任务：主题 → 后端调 LLM 生成大纲并落库。

    领导优化：支持可选 md_content——用户上传 .md 文档内容后，
    模型按文档内容生成大纲（topic 作为文档标题/PPT 主题）。
    """

    topic: str = Field(min_length=2, max_length=128, description="讲座主题/关键词（md 模式为文档标题）")
    template: str = Field(default="business", description=f"模板风格：{'/'.join(TEMPLATES)}")
    md_content: Optional[str] = Field(default=None, description="上传的 .md 文档内容（可选，按文档生成大纲）")

    @field_validator("template")
    @classmethod
    def _check_template(cls, v: str) -> str:
        # 内置 3 套 或 用户上传的自定义模板 id（uuid8_原名，上传接口生成）
        if v not in TEMPLATES and not (0 < len(v) <= 128):
            raise ValueError(f"模板必须是 {'/'.join(TEMPLATES)} 之一，或已上传的自定义模板")
        return v

    @field_validator("md_content")
    @classmethod
    def _check_md(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v.strip()) < 10:
            raise ValueError("文档内容过短，无法生成大纲（至少 10 字）")
        if v is not None and len(v) > 50000:
            raise ValueError("文档内容过长（≤50000 字符），请精简后重试")
        return v


class OutlineUpdate(BaseModel):
    """P02 大纲编辑：整体替换大纲 JSON。"""

    outline: dict = Field(description="大纲 JSON：{title, subtitle, sections:[{name, points}]}")


class PptTaskOut(BaseModel):
    """PPT 任务响应（列表/详情共用）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    topic: str
    outline: Optional[str] = None  # JSON 字符串（前端自行 parse）
    template: str
    file_path: Optional[str] = None  # 相对路径（前端拼 /ppt-files/）
    status: str
    created_at: datetime


class PptTaskListOut(BaseModel):
    """P03 我的 PPT 任务列表（分页）。"""

    items: list[PptTaskOut]
    total: int
    page: int
    page_size: int


class GenerateRequest(BaseModel):
    """P03 触发生成：确认大纲（可选，缺省用任务已存大纲）+ 模板（可选覆盖）。"""

    outline: Optional[dict] = Field(default=None, description="覆盖任务大纲（P02 编辑后提交）")
    template: Optional[str] = Field(default=None, description="覆盖模板（P04 选择）")

    @field_validator("template")
    @classmethod
    def _check_template(cls, v: Optional[str]) -> Optional[str]:
        # 内置 3 套 或 用户上传的自定义模板 id
        if v is not None and v not in TEMPLATES and not (0 < len(v) <= 128):
            raise ValueError(f"模板必须是 {'/'.join(TEMPLATES)} 之一，或已上传的自定义模板")
        return v
