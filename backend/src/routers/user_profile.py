"""个人主页路由（前缀 /api/me，与 auth.py 的 /me 区分）。

端点：
- GET    /api/me/profile         当前用户完整资料（users + user_profiles 合并）
- PATCH  /api/me/username        改用户名（查重，冲突 409）
- PATCH  /api/me/password        改密码（旧密码校验，错 401）
- PATCH  /api/me/email           填/改邮箱（可空清除）
- PATCH  /api/me/avatar          绑头像 URL（文件先走 POST /api/uploads/avatar）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.security import get_current_user
from src.schemas.user_profile import (
    AvatarUpdate,
    EmailUpdate,
    PasswordUpdate,
    ProfileOut,
    UsernameUpdate,
)
from src.services import user_profile_service as svc

router = APIRouter(prefix="/api/me", tags=["个人主页"])


@router.get("/profile", response_model=ProfileOut, summary="当前用户完整资料")
def get_profile(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.UserProfileService.get_profile(db, user["id"])


@router.patch("/username", summary="修改用户名（查重）")
def update_username(
    data: UsernameUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    svc.UserProfileService.update_username(db, user["id"], data.username)
    return {"ok": True, "username": data.username}


@router.patch("/password", summary="修改密码（旧密码校验）")
def update_password(
    data: PasswordUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    svc.UserProfileService.update_password(db, user["id"], data.old_password, data.new_password)
    return {"ok": True}


@router.patch("/email", summary="填/改邮箱")
def update_email(
    data: EmailUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    svc.UserProfileService.update_email(db, user["id"], data.email)
    return {"ok": True, "email": data.email}


@router.patch("/avatar", summary="绑定头像 URL")
def update_avatar(
    data: AvatarUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    svc.UserProfileService.update_avatar(db, user["id"], data.avatar)
    return {"ok": True, "avatar": data.avatar}
