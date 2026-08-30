"""隐患定位画框工具：根据视觉模型返回的 location 像素坐标，在原图上画框标注（A 方案，前后端共用）。
 
背景（2026-08-14 用户需求）：
- qwen3.8max 视觉返回 hazards[].location = [x1, y1, x2, y2]（图片真实像素坐标）；
- 此前前端只显示 类别/等级/描述/建议，坐标被丢弃；
- 本模块生成 <原名>_boxes.png 标注图供前端 <img> 直接展示，脚本 draw_boxes.py 复用本函数。

等级 → 框线颜色（与前端徽章配色同源）：
    重大 #C0392B / 较大 #D68910 / 一般 #B7950B / 轻微 #7F8C8D
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LEVEL_COLORS = {
    "重大": (192, 57, 43),    # #C0392B 
    "较大": (214, 105, 16),   # #D68910
    "一般": (183, 149, 11),   # #B7950B
    "轻微": (127, 140, 141),  # #7F8C8D
}
DEFAULT_COLOR = (41, 128, 185)  # 未识别等级兜底蓝色
BOX_WIDTH = 4
LABEL_BG_ALPHA = 200  # 标签背景不透明度 0-255


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """优先取中文字体（微软雅黑），否则默认字体（中文可能显示为方块）。"""
    for path in [
        "C:/Windows/Fonts/msyh.ttc",   # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",  # 黑体
        "C:/Windows/Fonts/simsun.ttc",  # 宋体
    ]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_hazard_boxes(
    image_path: str | Path,
    hazards: list,
    out_path: str | Path,
    scale: tuple[float, float] | None = None,
) -> int:
    """在原图副本上给每个含 location 的隐患画框 + 等级/类别标签。

    Args:
        image_path: 原图路径（uploads/ 下已落盘文件）。
        hazards: 视觉模型返回的 hazards 列表（元素含 level/category/location）。
        out_path: 标注图输出路径。
        scale: (scale_x, scale_y)——模型坐标是**缩放后图**的像素时传此参数，
            画框前把坐标还原到原图（2026-08-17 错位修复：vision 先缩放再识别，
            location 相对缩放图，必须乘回比例；None 表示坐标已是原图坐标）。

    Returns:
        实际画框数量（location 缺失/无效的隐患被跳过，返回 0 表示无框可画）。
    """
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    W, H = img.size
    font = _font(20)
    sx, sy = scale or (1.0, 1.0)

    drawn = 0
    for i, h in enumerate(hazards, start=1):
        loc = h.get("location")
        if not loc or not isinstance(loc, (list, tuple)) or len(loc) != 4:
            continue  # 模型约束：无法精确定位时输出 null，跳过
        x1, y1, x2, y2 = (int(v) for v in loc)
        # 坐标错位修复：模型坐标相对缩放图 → 还原到原图坐标
        x1, y1 = round(x1 * sx), round(y1 * sy)
        x2, y2 = round(x2 * sx), round(y2 * sy)
        # 坐标越界 clamp 到图片边界（模型偶尔输出轻微超界）
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(W, x2), min(H, y2)
        if x2 <= x1 or y2 <= y1:
            continue

        color = LEVEL_COLORS.get(h.get("level", ""), DEFAULT_COLOR)
        draw.rectangle([x1, y1, x2, y2], outline=color + (255,), width=BOX_WIDTH)

        # 标签（等级+序号+类别）画在框上方，越界则贴框内顶部
        label = f"{h.get('level', '?')} #{i} {h.get('category', '')}"
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 4
        ly = max(0, y1 - th - pad * 2 - 2)
        draw.rectangle(
            [x1, ly, x1 + tw + pad * 2, ly + th + pad * 2],
            fill=color + (LABEL_BG_ALPHA,),
        )
        draw.text((x1 + pad, ly + pad - bbox[1]), label, fill=(255, 255, 255, 255), font=font)
        drawn += 1

    if drawn:
        img.save(out_path)
    return drawn
