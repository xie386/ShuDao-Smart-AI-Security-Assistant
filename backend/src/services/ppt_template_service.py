"""PPT 模板管理（领导优化：用户上传模板永久保存，后续可选择生成）。

设计：
- 存储目录：knowledge_src/ppt_model/（与知识库平级，.pptx 不被 RAG 采集）；
- 内置 3 套模板（business/simple/tech）为"风格预设"，用户模板为真实 .pptx 文件；
- 上传校验：扩展名 .pptx、大小 ≤20MB、python-pptx 可打开（防损坏文件入库）；
- 列表返回：内置 3 套 + 用户上传的全部模板，供前端下拉选择。
"""
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from src.core.config import settings

logger = logging.getLogger(__name__)

# 模板存储目录（相对 backend/；与知识库平级，.pptx 不会被 collect_kb 采集）
PPT_MODEL_DIR = Path(settings.KNOWLEDGE_SRC_DIR) / "ppt_model"
MAX_TEMPLATE_SIZE = 20 * 1024 * 1024  # ≤20MB

# 内置模板（v2.0 大点 02，裁定 7/11）：百度链路移除后只保留一套兜底版式；
# 真正的样式来源是用户上传的 .pptx（读取其 theme1.xml 的主色/强调色）。
BUILTIN_TEMPLATES = [
    {"id": "builtin_simple", "name": "内置简洁（深蓝金）", "builtin": True},
]


def _ensure_dir() -> Path:
    PPT_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    return PPT_MODEL_DIR


def upload_template(file: UploadFile, user_id: int) -> dict:
    """保存用户上传的 PPT 模板（.pptx ≤20MB），返回模板信息。

    - 文件名规范：<uuid8>_<原始文件名>（避免中文/重名冲突，保留可读性）；
    - 校验：扩展名 + 大小 + python-pptx 可打开（损坏文件直接拒收）。
    """
    d = _ensure_dir()
    # 1. 扩展名校验
    original = file.filename or "template.pptx"
    if not original.lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="仅支持 .pptx 模板文件")
    # 2. 大小校验（Content-Length 优先，回退读流统计）
    size = 0
    chunk = file.file.read(1024 * 1024)
    while chunk:
        size += len(chunk)
        if size > MAX_TEMPLATE_SIZE:
            raise HTTPException(status_code=400, detail="模板文件超过 20MB 上限")
        chunk = file.file.read(1024 * 1024)
    if size == 0:
        raise HTTPException(status_code=400, detail="模板文件为空")
    file.file.seek(0)

    # 3. 落盘
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(original).name}"
    dest = d / safe_name
    with dest.open("wb") as f:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    # 4. python-pptx 可打开校验（损坏文件拒收并清理）
    try:
        from pptx import Presentation  # lazy

        Presentation(str(dest))
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"模板文件无法解析（非有效 pptx）：{exc}") from exc

    logger.info("PPT 模板上传成功: %s (user=%s)", safe_name, user_id)
    return {
        "id": safe_name,
        "name": Path(original).stem,
        "filename": safe_name,
        "builtin": False,
        "size": dest.stat().st_size,
    }


def list_templates() -> list[dict]:
    """模板列表：内置兜底 1 套 + 共享模板库里的用户上传模板。"""
    items = list(BUILTIN_TEMPLATES)
    d = _ensure_dir()
    for f in sorted(d.glob("*.pptx")):
        items.append(
            {
                "id": f.name,
                "name": f.stem.split("_", 1)[1] if "_" in f.stem else f.stem,
                "filename": f.name,
                "builtin": False,
                "size": f.stat().st_size,
            }
        )
    return items


def is_custom_template(template_id: str) -> bool:
    """判断模板 id 是否为用户上传的自定义模板（内置兜底为 False）。

    大点 02 起百度链路已移除，本函数不再参与"选链路"；仍保留给调用方做区分。
    """
    if not template_id or template_id in {t["id"] for t in BUILTIN_TEMPLATES}:
        return False
    return (PPT_MODEL_DIR / template_id).is_file()


def get_custom_template_path(template_id: str) -> Path:
    """取自定义模板文件路径（不存在抛 404）。"""
    target = (PPT_MODEL_DIR / template_id).resolve()
    if PPT_MODEL_DIR.resolve() not in target.parents or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模板不存在")
    return target
