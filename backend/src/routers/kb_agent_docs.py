"""Agent 方法论管理（v2.0 大点 04 · 裁定 9）：仅 admin。

- GET  /api/kb-agent-docs?name=Methodology.md|cases.md
- PUT  /api/kb-agent-docs            {name, content}     （保留 .bak）
- POST /api/kb-agent-docs/regenerate                     「AI 写方法论」
- POST /api/kb-agent-docs/cases      {line}              追加一条案例
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.core.security import require_roles
from src.services import agent_docs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb-agent-docs", tags=["知识贡献 · 方法论"])


class DocWrite(BaseModel):
    name: str = Field(..., description="Methodology.md / cases.md")
    content: str


class CaseAppend(BaseModel):
    line: str = Field(..., min_length=4, max_length=1000)


@router.get("", summary="读取方法论文档（仅 admin）")
def read_doc(name: str = Query("Methodology.md"), _: dict = Depends(require_roles("admin"))):
    try:
        return agent_docs.read(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("", summary="写入方法论文档（仅 admin，留 .bak）")
def write_doc(data: DocWrite, _: dict = Depends(require_roles("admin"))):
    try:
        return agent_docs.write(data.name, data.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/regenerate", summary="AI 写方法论（读 cases.md → 覆盖 Methodology.md）")
def regenerate(_: dict = Depends(require_roles("admin"))):
    try:
        return agent_docs.regenerate()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI 写方法论失败")
        raise HTTPException(status_code=502, detail=f"生成失败：{exc}") from exc


@router.post("/cases", summary="追加一条采集案例")
def append_case(data: CaseAppend, _: dict = Depends(require_roles("admin"))):
    try:
        return agent_docs.append_case(data.line)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/version", summary="当前方法论版本号（前端/agent_runs 用）")
def doc_version(_: dict = Depends(require_roles("admin"))):
    return {"version": agent_docs.version(), "dir": str(agent_docs.docs_dir())}
