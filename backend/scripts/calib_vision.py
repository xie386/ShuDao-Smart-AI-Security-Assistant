#!/usr/bin/env python
"""视觉模型坐标校准：验证 qwen3.7-plus 返回坐标与输入图片尺寸的比例关系。

原理：生成不同尺寸、含已知位置红色矩形（配电箱）的"现场感"测试图，
调 vision_client.analyze_hazard，对比模型返回 location 与真实矩形位置。
推断模型内部坐标系（缩放后图 / 固定尺寸 / 归一化偏差），确定画框换算策略。

用法（在 backend/ 目录下）：
    PYTHONPATH= C:\\python\\python.exe scripts/calib_vision.py
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from src.ai.vision import vision_client

# 真实矩形（配电箱）位置：归一化 [0.10,0.35] x [0.10,0.30]
CASES = [
    {"name": "A_1280x960", "size": (1280, 960)},
    {"name": "B_800x600", "size": (800, 600)},
    {"name": "C_1920x1080", "size": (1920, 1080)},
]


def make_scene(size: tuple[int, int]) -> bytes:
    """现场感测试图：米白墙 + 地面 + 红色配电箱（已知位置）+ 杂物。"""
    w, h = size
    img = Image.new("RGB", size, (217, 210, 192))
    d = ImageDraw.Draw(img)
    # 地面
    d.rectangle([0, int(h * 0.75), w, h], fill=(168, 160, 142))
    # 配电箱（真实位置 0.10-0.35 x 0.10-0.30）
    bx1, by1 = int(w * 0.10), int(h * 0.10)
    bx2, by2 = int(w * 0.35), int(h * 0.30)
    d.rectangle([bx1, by1, bx2, by2], fill=(178, 34, 34), outline=(60, 20, 20), width=6)
    d.rectangle([bx1 + 20, by1 + 20, bx2 - 20, by1 + 50], fill=(40, 40, 40))  # 箱门
    # 杂物：电线杆
    d.line([(int(w * 0.6), int(h * 0.75)), (int(w * 0.6), int(h * 0.3))], fill=(90, 90, 90), width=10)
    d.line([(int(w * 0.62), int(h * 0.55)), (int(w * 0.85), int(h * 0.5))], fill=(230, 126, 34), width=5)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def main() -> None:
    for case in CASES:
        name, size = case["name"], case["size"]
        w, h = size
        img_bytes = make_scene(size)
        try:
            result = vision_client.analyze_hazard(img_bytes, scene_description="配电箱")
        except Exception as exc:  # noqa: BLE001
            print(f"=== {name} 识别失败: {exc}")
            continue
        scale = result.get("_scale", (1, 1))
        hazards = result.get("hazards") or []
        print(f"=== {name} ({w}x{h}, 缩放比 scale={scale[0]:.3f},{scale[1]:.3f}) ===")
        # 真实矩形（原图像素）
        rx1, ry1, rx2, ry2 = int(w * 0.10), int(h * 0.10), int(w * 0.35), int(h * 0.30)
        print(f"  真实位置(原图px): x[{rx1},{rx2}] y[{ry1},{ry2}]  归一化 x[0.10,0.35] y[0.10,0.30]")
        for i, hz in enumerate(hazards[:2]):
            loc = hz.get("location")
            if not loc:
                print(f"  [{i}] 无坐标: {hz.get('category','')}")
                continue
            x1, y1, x2, y2 = loc
            # 模型坐标归一化（按缩放后图）
            sw, sh = w / scale[0], h / scale[1]
            nx1, ny1, nx2, ny2 = x1 / sw, y1 / sh, x2 / sw, y2 / sh
            print(f"  [{i}] {hz.get('category','')} 模型loc={loc}")
            print(f"      按缩放图归一化: x[{nx1:.3f},{nx2:.3f}] y[{ny1:.3f},{ny2:.3f}]")
            print(f"      与真实偏差: dx={nx1-0.10:+.3f}~{nx2-0.35:+.3f} dy={ny1-0.10:+.3f}~{ny2-0.30:+.3f}")


if __name__ == "__main__":
    main()
