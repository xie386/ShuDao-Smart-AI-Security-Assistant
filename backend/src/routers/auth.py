"""认证路由：注册 / 登录 / 当前用户。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.security import get_current_user
from src.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from src.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=201,
    summary="用户注册（新用户角色固定为普通员工）",
)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    return AuthService.register(db, data)


@router.post("/login", response_model=TokenResponse, summary="登录获取 JWT")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = AuthService.authenticate(db, data.username, data.password)
    return AuthService.issue_token(user)


@router.get("/me", response_model=UserOut, summary="当前登录用户信息")
def me(payload: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return AuthService.get_by_id(db, payload["id"])
