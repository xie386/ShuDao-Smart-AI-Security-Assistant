"""认证相关请求/响应契约。""" 
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    """注册请求。

    安全约定：不接收 role 字段，新注册用户一律为普通员工（employee），
    管理员/安全员由初始化脚本或数据维护创建。
    """

    username: str = Field(
        min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_]+$", description="登录名（字母/数字/下划线）"
    )
    password: str = Field(min_length=6, max_length=64, description="密码 6-64 位")
    real_name: Optional[str] = Field(default=None, max_length=64, description="真实姓名（可选）")


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    real_name: Optional[str] = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
