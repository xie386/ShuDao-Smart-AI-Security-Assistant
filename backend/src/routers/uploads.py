"""文件上传路由（H01 隐患上报图 / H05 整改照共用）。

功能说明：
- 单接口两处复用：隐患上报（H01，image_urls）与整改反馈（H05，rectify_images）都调用本接口上传图片；
- 需登录：通过 get_current_user 依赖强制校验 Bearer Token，未认证返回 401；
- 校验与落盘逻辑集中在 src/utils/files.py 的 validate_and_save()（扩展名白名单 + MIME 双校验 + ≤10MB + uuid 重命名）；
- 响应只返回 url，前端将其填入上报/整改表单的 image_urls / rectify_images 字段一并提交。

安全设计（ARCHITECTURE §6）：
- 不信任用户文件名：uuid 重命名落盘，杜绝路径穿越与重名覆盖；
- 类型白名单 + MIME 双校验，只允许 jpg/jpeg/png/webp；
- 文件落盘到 settings.UPLOAD_DIR（默认 uploads/），返回静态路径 /uploads/<uuid>.<ext>。
"""
from fastapi import APIRouter, Depends, File, UploadFile
"""
注释文档:
APIRouter：创建子路由实例，统一前缀 /api/uploads，便于在 main.py 挂载到主应用。
Depends：FastAPI 依赖注入，用于注入 get_current_user 完成登录鉴权。
File/UploadFile：声明 multipart/form-data 文件上传参数；UploadFile 携带文件名、Content-Type 与字节流。
"""
 
from src.core.security import get_current_user
"""
注释文档:
get_current_user：JWT 鉴权依赖。解析 Authorization: Bearer <token>，返回 {id, role}；
未携带或 token 无效/过期时直接抛出 401（detail 区分文案）。
"""
from src.schemas.upload import ImageUploadResponse
"""
注释文档:
ImageUploadResponse：上传成功响应契约 {url, message="上传成功"}，url 供隐患上报/整改表单引用。
"""
from src.utils.files import validate_and_save
"""
注释文档:
validate_and_save：核心校验落盘函数。流程：扩展名白名单 → MIME 双校验 → 大小限制(≤10MB)
→ uuid 重命名写入 uploads/ → 返回可访问 URL；任一校验失败抛 400/413。
"""

# 路由实例：prefix 统一 /api/uploads，tags 用于 Swagger(/docs) 分组展示为「文件上传」
router = APIRouter(prefix="/api/uploads", tags=["文件上传"])


@router.post(
    "/image",
    response_model=ImageUploadResponse,
    status_code=201,
    summary="上传隐患图片（H01/H05）",
)
def upload_image(
    file: UploadFile = File(..., description="jpg/jpeg/png/webp，≤10MB"),
    _: dict = Depends(get_current_user),
):
    """上传一张隐患/整改图片。

    流程：
    1. 鉴权：Depends(get_current_user) 校验 Bearer Token（未携带/无效 → 401）；
    2. 校验落盘：validate_and_save() 完成扩展名白名单、MIME、大小三重校验，
       通过后以 uuid 重命名写入 uploads/ 目录；
    3. 返回 URL：前端拿到 /uploads/<uuid>.<ext> 后，作为 image_urls（H01 上报）
       或 rectify_images（H05 整改）字段随表单一并提交。

    参数:
        file: multipart/form-data 文件字段（表单名必须为 file），
             仅支持 jpg/jpeg/png/webp，大小 ≤10MB。

    返回:
        201 + {"url": "/uploads/xxx.jpg", "message": "上传成功"}

    异常:
        400: 扩展名不在白名单 / MIME 与扩展名不符
        401: 未登录或 token 无效
        413: 文件超过 10MB
    """
    # 校验 + 落盘（返回可访问 URL），失败时由 validate_and_save 抛对应 HTTPException
    url = validate_and_save(file)
    return ImageUploadResponse(url=url)


@router.post(
    "/avatar",
    response_model=ImageUploadResponse,
    status_code=201,
    summary="上传个人头像（复用图片白名单，个人主页用）",
)
def upload_avatar(
    file: UploadFile = File(..., description="jpg/jpeg/png/webp，≤10MB"),
    user: dict = Depends(get_current_user),
):
    """上传当前登录用户的头像。

    复用 utils/files.validate_and_save()（白名单 jpg/jpeg/png/webp + ≤10MB + uuid 重命名），
    返回 URL 后由前端调 PATCH /api/me/avatar 绑定到 user_profiles.avatar。
    不单独限制尺寸（保持与隐患图片同一套校验，简单一致）。
    """
    url = validate_and_save(file)
    return ImageUploadResponse(url=url)
