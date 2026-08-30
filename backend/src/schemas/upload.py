"""上传相关契约。"""
from pydantic import BaseModel


class ImageUploadResponse(BaseModel):
    url: str
    message: str = "上传成功"
