#!/usr/bin/env python
"""隐患定位画框 CLI：根据视觉模型返回的 location 像素坐标，在原图上画框标注。

核心实现见 src/utils/boxes.py（draw_hazard_boxes），本脚本只是 CLI 入口，
供离线验证 / 批量处理使用；后端接口（POST /api/vision/hazard）内部也复用同一函数。

两种用法（在 backend/ 目录下，全局 Python）：
1) 离线画框（推荐先测，不调用模型）：
   PYTHONPATH= C:\\python\\python.exe scripts/draw_boxes.py uploads/1.png --json result.json
   # result.json 格式: {"detected": true, "hazards": [{"level","category","description","location":[x1,y1,x2,y2]}], "summary": "..."}
2) 在线分析并画框（调 qwen3.8max，10-30s）：
   PYTHONPATH= C:\\python\\python.exe scripts/draw_boxes.py uploads/1.png
   # 等价于 test_vision.py + 画框；可用 --desc "现场描述"

输出：<图片名>_boxes.png（与原图同目录）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.boxes import draw_hazard_boxes  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="隐患定位画框（视觉模型 location 坐标 → 原图标框）")
    ap.add_argument("image", help="图片路径（如 uploads/1.png）")
    ap.add_argument("--json", dest="json_file", default="", help="离线模式：直接读模型结果 JSON 文件（不调用模型）")
    ap.add_argument("--desc", default="", help="在线模式：现场描述（可选）")
    args = ap.parse_args()

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"图片不存在: {img_path}")
        sys.exit(1)

    if args.json_file:
        # ---- 离线模式：读已有结果 JSON ----
        jf = Path(args.json_file)
        if not jf.exists():
            print(f"结果 JSON 不存在: {jf}")
            sys.exit(1)
        result = json.loads(jf.read_text(encoding="utf-8"))
        print(f"离线模式：读取 {jf} 的结果画框")
    else:
        # ---- 在线模式：调视觉模型现分析 ----
        print("调用 qwen3.8max 视觉分析中（约 10-30s）...")
        from src.ai.vision import vision_client  # noqa: E402

        try:
            result = vision_client.analyze_hazard(img_path.read_bytes(), args.desc)
        except RuntimeError as exc:
            print(f"[失败] {exc}")
            sys.exit(1)
        print(f"检出隐患: {result.get('detected')} | 数量: {len(result.get('hazards') or [])}")

    hazards = result.get("hazards") or []
    out_path = img_path.with_name(img_path.stem + "_boxes.png")
    # 在线模式：vision 已缩放识别，坐标相对缩放图 → 传 _scale 还原原图坐标
    scale = result.get("_scale")
    drawn = draw_hazard_boxes(img_path, hazards, out_path, scale=scale)
    if drawn:
        print(f"\n📁 标注图已保存: {out_path}（共画 {drawn} 个框）")
    else:
        print("⚠️  没有可画框的隐患（全部无坐标或坐标为 null）")


if __name__ == "__main__":
    main()
