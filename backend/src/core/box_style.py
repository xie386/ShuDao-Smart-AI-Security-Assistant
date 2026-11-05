"""隐患框样式：**前后端唯一事实源**（v2.0 大点 06 · 裁定 1/3/4）。

规则（裁定 1）：
- **色相 = 类别**（4 类固定色相）；
- **明度 = 等级**（重大最深 → 轻微最浅，S 固定 0.62，L = 38%/46%/55%/64%）；
- 边框加白色外描边（深/浅背景都可见）；边框粗细随等级递增。

其他常量（裁定 3/4）：
- 标签默认只显示序号（`#1`），悬停/聚焦才展开文字；
- 填充透明度：常态 25%、聚焦 50%、非聚焦淡化 7%；
- 线宽/字号按图宽自适应（以 4096 px 为基准）。

前端通过 `GET /api/vision/box-style` 拿这份配置，**不允许在前端另写一份色板**。
"""
from __future__ import annotations

import colorsys

CATEGORY_HUES: dict[str, int] = {
    "人的不安全行为": 212,
    "物的不安全状态": 32,
    "环境的不安全因素": 145,
    "管理缺陷": 268,
}
#: 未知类别（模型偶尔给别的措辞）落到"物的不安全状态"色系
DEFAULT_CATEGORY = "物的不安全状态"

LEVEL_LIGHTNESS: dict[str, float] = {"重大": 0.38, "较大": 0.46, "一般": 0.55, "轻微": 0.64}
SATURATION = 0.62

#: 以 4096 px 宽图为基准的线宽（裁定 1）
LEVEL_STROKE_BASE = {"重大": 12, "较大": 9, "一般": 7, "轻微": 6}
BASE_WIDTH = 4096

FILL_ALPHA = 0.25          # 常态填充
FOCUS_FILL_ALPHA = 0.50    # 聚焦
DIM_FILL_ALPHA = 0.07      # 非聚焦淡化（裁定 2：6~8%）
OUTLINE_ALPHA = 1.0
WHITE_OUTLINE_WIDTH = 2    # 白描边（像素，基准 4096）

LABEL_MODE_DEFAULT = "index"   # index | full（裁定 3）


def _hex(r: float, g: float, b: float) -> str:
    return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


def color_for(level: str, category: str) -> str:
    """类别色相 × 等级明度 → #RRGGBB。"""
    hue = CATEGORY_HUES.get(category, CATEGORY_HUES[DEFAULT_CATEGORY])
    light = LEVEL_LIGHTNESS.get(level, LEVEL_LIGHTNESS["一般"])
    r, g, b = colorsys.hls_to_rgb((hue % 360) / 360, light, SATURATION)
    return _hex(r, g, b)


def rgb_for(level: str, category: str) -> tuple[int, int, int]:
    hexv = color_for(level, category)
    return int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16)


def stroke_for(level: str, image_width: int) -> int:
    """线宽按图宽自适应：4096 宽时 6~12 px，1080 宽时约 2~3 px（下限 2）。"""
    base = LEVEL_STROKE_BASE.get(level, LEVEL_STROKE_BASE["一般"])
    return max(2, round(base * max(1, image_width) / BASE_WIDTH))


def font_for(image_width: int) -> int:
    """标签字号自适应：4096 宽 → 64 px；下限 18 px（缩略图不至于糊成一团）。"""
    return max(18, round(max(1, image_width) / 64))


def palette() -> dict:
    """给前端的完整配置（含每个类别 × 等级的色值矩阵）。"""
    matrix = {
        cat: {lvl: color_for(lvl, cat) for lvl in LEVEL_LIGHTNESS}
        for cat in CATEGORY_HUES
    }
    return {
        "category_hues": CATEGORY_HUES,
        "default_category": DEFAULT_CATEGORY,
        "level_lightness": LEVEL_LIGHTNESS,
        "saturation": SATURATION,
        "colors": matrix,
        "level_stroke_base": LEVEL_STROKE_BASE,
        "base_width": BASE_WIDTH,
        "fill_alpha": FILL_ALPHA,
        "focus_fill_alpha": FOCUS_FILL_ALPHA,
        "dim_fill_alpha": DIM_FILL_ALPHA,
        "outline_alpha": OUTLINE_ALPHA,
        "white_outline_width": WHITE_OUTLINE_WIDTH,
        "label_mode_default": LABEL_MODE_DEFAULT,
        "font_ratio": 1 / 64,
        "font_min": 18,
        "stroke_min": 2,
    }
