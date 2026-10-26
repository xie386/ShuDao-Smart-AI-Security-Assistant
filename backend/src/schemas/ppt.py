"""PPT 工坊模块契约（P01–P07，D4）。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 内置模板（v2.0 大点 02：百度链路移除后只保留一套兜底版式，裁定 7/11）
BUILTIN_TEMPLATES = ("builtin_simple",)
# 任务状态机（PptTask.status 枚举：草稿/生成中/已完成/失败）
STATUSES = ("草稿", "生成中", "已完成", "失败")


class PptCreate(BaseModel):
    """P01 新建 PPT 任务：主题 → 后端调 LLM 生成大纲并落库。

    用户上传 .md 文档后，模型按文档内容生成大纲（topic 作为文档标题/PPT 主题）。
    """

    topic: str = Field(min_length=2, max_length=128, description="讲座主题/关键词（md 模式为文档标题）")
    template: str = Field(
        default="builtin_simple",
        description=f"模板：{'/'.join(BUILTIN_TEMPLATES)} 或已上传的自定义模板 id",
    )
    md_content: Optional[str] = Field(default=None, description="上传的 .md 文档内容（可选，按文档生成大纲）")

    @field_validator("template")
    @classmethod
    def _check_template(cls, v: str) -> str:
        # 内置兜底版式 或 用户上传的自定义模板 id（uuid8_原名，上传接口生成）
        if v not in BUILTIN_TEMPLATES and not (0 < len(v) <= 128):
            raise ValueError(f"模板必须是 {'/'.join(BUILTIN_TEMPLATES)} 之一，或已上传的自定义模板")
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
    file_path: Optional[str] = None  # 产物路径：新任务在 user_data/ppt 沙箱，历史任务在 ppt_files/（下载走 /api/ppt/{id}/download）
    status: str
    created_at: datetime


class PptTaskListOut(BaseModel):
    """P03 我的 PPT 任务列表（分页）。"""

    items: list[PptTaskOut]
    total: int
    page: int
    page_size: int


class GenerateRequest(BaseModel):
    """P03 快速模式：确认大纲（可选，缺省用任务已存大纲）+ 模板（可选覆盖）。"""

    outline: Optional[dict] = Field(default=None, description="覆盖任务大纲（P02 编辑后提交）")
    template: Optional[str] = Field(default=None, description="覆盖模板（P04 选择）")

    @field_validator("template")
    @classmethod
    def _check_template(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in BUILTIN_TEMPLATES and not (0 < len(v) <= 128):
            raise ValueError(f"模板必须是 {'/'.join(BUILTIN_TEMPLATES)} 之一，或已上传的自定义模板")
        return v


class PptAgentSpec(BaseModel):
    """v2.0 大点 02：PPT 生成 Agent 的输入规格（表单 6 项，规划文档 §02 §3.11）。"""

    topic: Optional[str] = Field(default=None, max_length=128, description="主题（缺省用任务主题）")
    requirements: Optional[str] = Field(
        default=None, description="详细要求（可粘 .md 内容）", max_length=50000
    )
    md_content: Optional[str] = Field(
        default=None, description="以文档内容作为素材（与 requirements 合并）", max_length=50000
    )
    pages: int = Field(default=10, ge=3, le=40, description="目标页数（含封面/目录/结尾）")
    words_per_page: int = Field(default=90, ge=30, le=400, description="每页字数参考值")
    allow_overflow: bool = Field(default=False, description="内容超了是否允许超页/超字数")
    template: Optional[str] = Field(default=None, description="模板 id（缺省用任务模板）")
