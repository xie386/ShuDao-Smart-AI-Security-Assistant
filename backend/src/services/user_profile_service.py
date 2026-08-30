"""个人主页业务（user_profiles 扩展资料 + users 核心字段自助维护）。

设计（对齐 hazard_service / knowledge_contrib_service 范式）：
- **users 表零改动**（守 P3 铁律）：email/avatar 落 user_profiles 一对一表；
- 改密复用 security.hash_password / verify_password（bcrypt，库直用）；
- 所有写操作强制 user_id == 当前登录用户（自助语义，无越权）；
- get_or_create：首次访问自动建空 profile 行（避免前端拿不到资料报错）。
"""
import logging
import re

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.security import hash_password, verify_password
from src.models.user import User
from src.models.user_profile import UserProfile

logger = logging.getLogger(__name__)

# 邮箱格式（宽松）：非空时必须是合法邮箱
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[\w一-鿿.-]{2,64}$")  # 2-64 字，允许中文/字母数字/_-.


class UserProfileService:
    # ---------- 读取（合并 users + user_profiles） ----------

    @staticmethod
    def get_profile(db: Session, user_id: int) -> dict:
        """返回合并资料：username/role/real_name（来自 users）+ email/avatar（来自 profile）。"""
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        profile = db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        ).scalar_one_or_none()
        return {
            "user_id": user.id,
            "username": user.username,
            "role": user.role,
            "real_name": user.real_name,
            "email": profile.email if profile else None,
            "avatar": profile.avatar if profile else None,
        }

    @staticmethod
    def _get_or_create(db: Session, user_id: int) -> UserProfile:
        prof = db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        ).scalar_one_or_none()
        if prof is None:
            prof = UserProfile(user_id=user_id)
            db.add(prof)
            db.flush()
        return prof

    # ---------- 自助修改 ----------

    @staticmethod
    def update_username(db: Session, user_id: int, new_username: str) -> None:
        new_username = (new_username or "").strip()
        if not _USERNAME_RE.match(new_username):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="用户名需 2-64 字（中文/字母数字/_-.)",
            )
        # 查重（排除自己）
        clash = db.execute(
            select(User).where(User.username == new_username, User.id != user_id)
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="该用户名已被占用"
            )
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        user.username = new_username
        db.commit()

    @staticmethod
    def update_password(db: Session, user_id: int, old_pwd: str, new_pwd: str) -> None:
        if not old_pwd or not new_pwd:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="新旧密码均必填")
        if len(new_pwd) < 6:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="新密码至少 6 位")
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        if not verify_password(old_pwd, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="原密码错误")
        user.hashed_password = hash_password(new_pwd)
        db.commit()

    @staticmethod
    def update_email(db: Session, user_id: int, email: str | None) -> None:
        email = (email or "").strip() or None
        if email and not _EMAIL_RE.match(email):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="邮箱格式不合法")
        prof = UserProfileService._get_or_create(db, user_id)
        prof.email = email
        db.commit()

    @staticmethod
    def update_avatar(db: Session, user_id: int, avatar_url: str) -> None:
        if not avatar_url or not avatar_url.startswith("/uploads/"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="头像 URL 非法")
        prof = UserProfileService._get_or_create(db, user_id)
        prof.avatar = avatar_url
        db.commit()
