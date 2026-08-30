"""认证业务：注册（硬编码 employee）、登录、令牌签发、用户查询。"""
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.security import create_access_token, hash_password, verify_password
from src.models.user import User
from src.schemas.auth import RegisterRequest


class AuthService:
    @staticmethod
    def register(db: Session, data: RegisterRequest) -> User:
        """注册新用户。用户名冲突抛 409；角色固定 employee（安全约定）。"""
        if db.scalar(select(User).where(User.username == data.username)):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")

        user = User(
            username=data.username,
            hashed_password=hash_password(data.password),
            role=data.role,  # employee/safety_officer 可注册；admin 由 schema 枚举拦截
            real_name=data.real_name,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def authenticate(db: Session, username: str, password: str) -> User:
        """校验用户名/密码，失败统一抛 401（不泄露账号是否存在）。"""
        user = db.scalar(select(User).where(User.username == username))
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
            )
        return user

    @staticmethod
    def issue_token(user: User) -> dict:
        """签发 JWT 并组装登录响应。"""
        return {
            "access_token": create_access_token(user.id, user.role),
            "token_type": "bearer",
            "user": user,
        }

    @staticmethod
    def get_by_id(db: Session, user_id: int) -> User:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        return user
