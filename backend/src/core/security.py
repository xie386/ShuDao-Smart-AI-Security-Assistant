"""安全模块：bcrypt 密码哈希 + JWT 签发/校验。

密码哈希直接用 bcrypt 库（全局环境 bcrypt>=5.0，passlib 与其存在兼容问题，不引入 passlib）。
"""
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from src.core.config import settings

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """bcrypt 哈希，返回 str 便于入库。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """校验密码，失败返回 False 不抛异常。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str | int, role: str, expires_minutes: int | None = None) -> str:
    """签发 JWT（HS256），sub=用户ID，role 随 token 携带供 RBAC 校验。"""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": str(subject), "role": role, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict:
    """校验并解析 JWT，无效抛 401。"""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的令牌",
        ) from exc


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """FastAPI 依赖：从 Authorization: Bearer <token> 解析当前用户（id/role）。"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
        )
    payload = decode_access_token(credentials.credentials)
    return {"id": int(payload["sub"]), "role": payload.get("role", "employee")}


def require_roles(*roles: str):
    """RBAC 依赖工厂：当前用户角色必须在 roles 中，否则 403。

    用法：`user: dict = Depends(require_roles("admin", "safety_officer"))`
    （PRD 权限模型：派单/验收仅 admin 与 safety_officer 可用）
    """

    def _checker(payload: dict = Depends(get_current_user)) -> dict:
        if payload["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要角色 {'/'.join(roles)}",
            )
        return payload

    return _checker
