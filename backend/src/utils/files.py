"""文件上传工具：类型白名单 + 大小限制 + uuid 重命名落盘（ARCHITECTURE §6）。
 
安全设计：
- 类型白名单（jpg/jpeg/png/webp）+ MIME 双校验；
- 大小限制 ≤10MB（settings.MAX_UPLOAD_SIZE）；
- uuid 重命名落盘，杜绝路径穿越与重名覆盖；
- 只存白名单扩展名，不信任用户文件名。
"""
import uuid
"""
注释文档:
uuid作用：生成唯一文件名，避免路径穿越与重名覆盖。
"""
from pathlib import Path
"""
注释文档:
Path作用：处理文件路径，确保跨平台兼容性。
"""
from fastapi import HTTPException, UploadFile
"""
注释文档:
UploadFile作用：处理上传文件，包含文件元数据与二进制数据。
"""
from src.core.config import settings
 

def _allowed_ext() -> set[str]:
    """
    注释文档:
    _allowed_ext作用：根据配置文件返回允许的图片扩展名白名单（jpg/jpeg/png/webp）。
    """
    return {e.strip().lower() for e in settings.ALLOWED_IMAGE_EXTENSIONS.split(",") if e.strip()}
 

def validate_and_save(file: UploadFile) -> str:
    """校验并保存上传图片，返回可访问的 URL 路径（如 /uploads/ab12....jpg）。

    校验失败抛 400/413；成功落盘到 settings.UPLOAD_DIR。
    """
    # 1. 扩展名白名单（取用户文件名后缀，不信任 MIME 单一来源）
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in _allowed_ext():
        raise HTTPException(status_code=400, detail=f"不支持的图片格式: {ext or '未知'}（仅允许 jpg/jpeg/png/webp）")

    # 2. MIME 白名单（双校验）
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    if file.content_type != mime_map[ext]:
        raise HTTPException(status_code=400, detail=f"文件类型与扩展名不符: {file.content_type}")

    # 3. 大小限制（读全量字节校验）
    data = file.file.read()
    if len(data) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"图片超过大小限制 {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB")

    # 4. uuid 重命名落盘
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    (upload_dir / filename).write_bytes(data)

    return f"/uploads/{filename}"
