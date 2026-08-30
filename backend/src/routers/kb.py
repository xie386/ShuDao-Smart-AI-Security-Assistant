"""知识库文档只读接口：供前端「引用来源」跳转阅读治理后的 md 原文 + 首页政策文件浏览。

安全设计：
- 仅允许 knowledge_src 四类目录下的 .md/.txt（治理清洗后的干净文件）；
- 路径参数经 resolve + 前缀校验，杜绝路径穿越（../ 等）；
- 需登录（Bearer token），只读不写。
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from src.core.config import settings
from src.core.security import get_current_user

router = APIRouter(prefix="/api/kb", tags=["知识库"])

# 允许的目录名（与 knowledge_src 四类子目录一致）→ 展示名
_ALLOWED_SUBDIRS = {"laws", "procedures", "plans", "cases"}
_SUBDIR_LABELS = {"laws": "法规/制度", "procedures": "操作规程", "plans": "应急预案", "cases": "事故案例"}


@router.get("/list", summary="知识库文档目录（四类，md/txt）")
def list_kb_docs(
    q: str = Query(default="", description="文件名/标题模糊搜索（可选）"),
    user: dict = Depends(get_current_user),
):
    """返回 knowledge_src 四类子目录下的全部 .md/.txt 元信息（不含正文）。

    供首页「政策文件」模块：分类展示 + 前端本地模糊搜索；点击后用 GET /doc 拉正文。
    搜索依据 = 文件名 + 首行标题（文件名多为英文 source_id，中文标题才是用户可搜的关键词）。
    """
    kb_root = Path(settings.KNOWLEDGE_SRC_DIR).resolve()
    items: list[dict] = []
    for subdir in sorted(_ALLOWED_SUBDIRS):
        d = kb_root / subdir
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not (f.is_file() and f.suffix.lower() in {".md", ".txt"}):
                continue
            items.append(
                {
                    "path": f"knowledge_src/{subdir}/{f.name}",
                    "name": f.stem,
                    "title": _first_title(f),
                    "category": subdir,
                    "category_label": _SUBDIR_LABELS[subdir],
                }
            )
    if q.strip():
        ql = q.strip().lower()
        items = [
            it
            for it in items
            if ql in it["name"].lower()
            or ql in (it["title"] or "").lower()
            or ql in (it["category_label"] or "").lower()
        ]
    return {"total": len(items), "items": items}


def _first_title(f: Path) -> str:
    """读取文件首行 md 标题（# / ## 打头），失败/无标题返回空串。"""
    try:
        with f.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#"):
                    return line.lstrip("#").strip()
                if line:
                    return line.strip()[:60]  # 无 # 则取首个非空行前 60 字
    except OSError:
        pass
    return ""


@router.get("/doc", summary="读取知识库源文档（md/txt 原文）")
def read_kb_doc(
    path: str,
    user: dict = Depends(get_current_user),
):
    """path 形如 knowledge_src/laws/xxx.md（rag.py 的 sources[].source 原样传回）。"""
    if not path:
        raise HTTPException(status_code=400, detail="缺少 path 参数")

    # 归一化 + 白名单：必须位于 knowledge_src/<四类目录>/ 下
    # source 字段形如 "knowledge_src/laws/xxx.md"（相对 backend/），剥掉前缀后按相对 kb_root 解析
    kb_root = Path(settings.KNOWLEDGE_SRC_DIR).resolve()
    rel_path = path.replace("\\", "/")
    if rel_path.startswith("knowledge_src/"):
        rel_path = rel_path[len("knowledge_src/"):]
    try:
        target = (kb_root / rel_path).resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="非法路径") from None

    # 前缀校验（防 ../ 穿越到 knowledge_src 外）
    if target != kb_root and kb_root not in target.parents:
        raise HTTPException(status_code=400, detail="路径越界，仅允许 knowledge_src 内文档")

    rel = target.relative_to(kb_root)
    if rel.parts[0] not in _ALLOWED_SUBDIRS:
        raise HTTPException(status_code=400, detail="仅允许 laws/procedures/plans/cases 四类目录")

    # 扩展名白名单：只读治理后的 md/txt
    if target.suffix.lower() not in {".md", ".txt"}:
        raise HTTPException(status_code=400, detail="仅支持 .md/.txt 文档")

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"文档不存在: {rel}")

    content = target.read_text(encoding="utf-8")
    return {"path": str(rel), "name": target.stem, "content": content}
