"""隐患定位画框工具（v2.0 大点 06 重写 · 裁定 1/3/4）。

变更点（相对旧版）：
1. 配色改为 **类别色相 × 等级明度**（`src/core/box_style.py` 是唯一事实源），不再用旧的 4 色 LEVEL_COLORS；
2. 框内加**同色半透明填充**（常态 25%），重叠区自然加深；
3. 双层描边：白色外扩 + 同色内描边，深色背景也看得见；
4. 线宽/字号**按图宽自适应**（4096 宽：线 6~12 px、字 64 px）；
5. 标签默认**只显示序号**（`#1`），`labels="full"` 时显示「#n 等级 类别 名称」；
6. 输出格式跟随后缀：`.jpg` → JPEG q90（4096 图 ≈2.5 MB），`.png` → 无损（旧行为）。

坐标约定不变：`location` 默认按 `scale` 还原到原图（模型坐标相对缩放图时传 `_scale`）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.core import box_style

logger = logging.getLogger(__name__)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """优先取中文字体（微软雅黑），否则默认字体（中文可能显示为方块）。"""
    for path in (
        "C:/Windows/Fonts/msyh.ttc",    # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",  # 黑体
        "C:/Windows/Fonts/simsun.ttc",  # 宋体
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _label_text(index: int, h: dict, mode: str) -> str:
    if mode == "index":
        return f"#{index}"
    name = (h.get("name") or h.get("title") or h.get("description") or "").strip()
    name = name[:10] + ("…" if len(name) > 10 else "")
    parts = [f"#{index}", h.get("level") or "", h.get("category") or "", name]
    return " ".join(p for p in parts if p)


def draw_hazard_boxes(
    image_path: str | Path,
    hazards: list,
    out_path: str | Path,
    scale: tuple[float, float] | None = None,
    *,
    labels: str = box_style.LABEL_MODE_DEFAULT,
    jpeg_quality: int = 90,
) -> int:
    """在原图副本上画隐患框，返回实际画框数（`location` 缺失/无效的跳过）。

    Args:
        image_path: 原图路径（uploads/ 下已落盘文件）。
        hazards: 隐患列表，元素含 level/category/location（可选 name/description）。
        out_path: 输出路径；后缀决定格式（.jpg/.jpeg → JPEG，其它 → 原格式）。
        scale: (scale_x, scale_y)，模型坐标相对缩放图时传，用于还原到原图。
        labels: "index"（只显示 #n）| "full"（#n 等级 类别 名称）。
        jpeg_quality: JPEG 质量（默认 90）。
    """
    src = Path(image_path)
    out = Path(out_path)
    img = Image.open(src)
    if img.mode != "RGB":
        img = img.convert("RGB")
    W, H = img.size

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    sx, sy = scale or (1.0, 1.0)
    font_px = box_style.font_for(W)
    font = _font(font_px)
    white_w = max(1, box_style.WHITE_OUTLINE_WIDTH)

    drawn = 0
    for i, h in enumerate(hazards, start=1):
        loc = h.get("location")
        if not loc or not isinstance(loc, (list, tuple)) or len(loc) != 4:
            continue  # 模型约束：无法精确定位时输出 null，跳过
        try:
            x1, y1, x2, y2 = (float(v) for v in loc)
        except (TypeError, ValueError):
            continue
        x1, y1 = round(x1 * sx), round(y1 * sy)
        x2, y2 = round(x2 * sx), round(y2 * sy)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(W, x2), min(H, y2)
        if x2 <= x1 or y2 <= y1:
            continue

        level = h.get("level") or "一般"
        category = h.get("category") or box_style.DEFAULT_CATEGORY
        rgb = box_style.rgb_for(level, category)
        lw = box_style.stroke_for(level, W)

        # 1) 同色半透明填充（重叠自动加深）
        draw.rectangle([x1, y1, x2, y2], fill=rgb + (int(255 * box_style.FILL_ALPHA),))
        # 2) 白色外描边（先粗白，再色线）—— 深浅背景都可见
        if white_w:
            draw.rectangle([x1, y1, x2, y2], outline=(255, 255, 255, 235), width=lw + white_w * 2)
        # 3) 同色内描边
        draw.rectangle([x1, y1, x2, y2], outline=rgb + (int(255 * box_style.OUTLINE_ALPHA),), width=lw)

        # 4) 标签（默认只有序号）：贴框左上角内侧，越界自动内收
        text = _label_text(i, h, labels)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th, top = bbox[2] - bbox[0], bbox[3] - bbox[1], bbox[1]
        except Exception:  # noqa: BLE001 —— 老版本 Pillow 无 textbbox
            tw, th, top = font_px * max(1, len(text)) * 0.62, font_px, 0
        pad_x, pad_y = max(6, font_px // 5), max(3, font_px // 8)
        lx, ly = x1 + pad_x, y1 + pad_y
        if lx + tw + pad_x > x2:      # 框太窄 → 贴右边内侧
            lx = max(x1, x2 - tw - pad_x)
        if ly + th + pad_y > y2:      # 框太矮 → 贴底边内侧
            ly = max(y1, y2 - th - pad_y)
        draw.rectangle(
            [lx - pad_x, ly - pad_y, lx + tw + pad_x, ly + th + pad_y],
            fill=rgb + (215,),
        )
        draw.text((lx, ly - top), text, font=font, fill=(255, 255, 255, 255))
        drawn += 1

    result = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in {".jpg", ".jpeg"}:
        result.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
    else:
        result.save(out)
    logger.info("画框完成：%d 个 @ %dx%d → %s（labels=%s）", drawn, W, H, out.name, labels)
    return drawn
