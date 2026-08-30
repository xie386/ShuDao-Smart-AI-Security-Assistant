"""个人主页契约。"""
from typing import Optional

from pydantic import BaseModel, Field


class ProfileOut(BaseModel):
    user_id: int
    username: str
    role: str
    real_name: Optional[str] = None
    email: Optional[str] = None
    avatar: Optional[str] = None


class UsernameUpdate(BaseModel):
    username: str = Field(min_length=2, max_length=64)


class PasswordUpdate(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=64)


class EmailUpdate(BaseModel):
    email: Optional[str] = Field(default=None, max_length=128)  # 空=清除


class AvatarUpdate(BaseModel):
    avatar: str = Field(..., description="头像 URL（/uploads/xxx.jpg）")
