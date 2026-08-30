"""认证相关请求/响应契约。"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    """注册请求。

    安全约定（2026-08-13 修订）：开放 employee / safety_officer 两种角色注册
    （业务需要安全员/责任人账号）；admin 仍禁止通过注册创建，只能由
    scripts/init_admin.py 初始化。
    """

    username: str = Field(
        min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\u4e00-\u9fa5]+$",
        description="登录名（中文/字母/数字/下划线，2026-08-16 放开中文）"
    )
    password: str = Field(min_length=6, max_length=64, description="密码 6-64 位")
    real_name: Optional[str] = Field(default=None, max_length=64, description="真实姓名（可选）")
    role: Literal["employee", "safety_officer"] = Field(
        default="employee", description="角色：普通员工/安全员（admin 禁止注册）"
    )


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
