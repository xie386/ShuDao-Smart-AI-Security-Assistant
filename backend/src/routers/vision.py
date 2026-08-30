"""图片隐患识别路由（视觉子模型 qwen3.8max，方案 2）。 

注意：需 .env 配置 QWEN_API_KEY/QWEN_BASE_URL 后方可调用；
key 未配置时返回 503 与清晰提示。

A 方案（2026-08-14）：识别结果若含 location 像素坐标，同步生成 <原名>_boxes.png
标注图（src/utils/boxes.py），响应携带 boxes_url 供前端直接 <img> 展示隐患框。
"""
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.ai.vision import vision_client
from src.core.config import settings
from src.core.security import get_current_user
from src.utils.boxes import draw_hazard_boxes
from src.utils.files import validate_and_save

router = APIRouter(prefix="/api/vision", tags=["图片识别"])


@router.post(
    "/hazard",
    summary="图片隐患识别（qwen3.8max 视觉）",
    description="上传现场图片 + 可选现场描述，返回结构化隐患分析（类别/等级/整改建议/像素坐标），"
    "并在含坐标时生成标注图返回 boxes_url。图片同时落盘 uploads/。",
)
def analyze_hazard(
    file: UploadFile = File(..., description="jpg/jpeg/png/webp，≤10MB"),
    scene_description: str = Form(default="", description="现场描述（可选）"),
    user: dict = Depends(get_current_user),
):
    # 先走上传校验（白名单/大小），并落盘复用 H01 图片链路
    url = validate_and_save(file)
    file.file.seek(0)  # validate_and_save 读完了流，回到开头再读给视觉
    try:
        result = vision_client.analyze_hazard(file.file.read(), scene_description)
    except RuntimeError as exc:
        # key 未配置 / 模型不可用
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # A 方案：含 location 坐标 → 生成标注图（src/utils/boxes.py），返回 boxes_url
    boxes_url = None
    hazards = result.get("hazards") or []
    if any(h.get("location") for h in hazards):
        src_path = Path(settings.UPLOAD_DIR) / Path(url).name
        boxes_path = src_path.with_name(src_path.stem + "_boxes.png")
        # 坐标错位修复：vision 先缩放再识别，location 相对缩放图 →
        # 传 _scale 让画框函数还原到原图坐标
        scale = result.get("_scale")
        if draw_hazard_boxes(src_path, hazards, boxes_path, scale=scale):
            boxes_url = f"/uploads/{boxes_path.name}"

    # 清理内部字段（不返回给前端）
    result.pop("_scale", None)
    return {"image_url": url, "boxes_url": boxes_url, "analysis": result}
