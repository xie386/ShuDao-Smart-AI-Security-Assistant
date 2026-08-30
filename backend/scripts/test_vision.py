#!/usr/bin/env python
"""图片隐患识别测试：调 qwen3.8max 视觉子模型分析图片。

用法（在 backend/ 目录下，使用全局 Python）：
    PYTHONPATH= C:\\python\\python.exe scripts/test_vision.py "uploads/1.png"
    PYTHONPATH= C:\\python\\python.exe scripts/test_vision.py "uploads/1.png" "配电房门口堆放易燃物"

需要 .env 配置 QWEN_API_KEY / QWEN_BASE_URL（当前已配置）。
直接调 vision_client（不经过 HTTP/鉴权），适合快速验证模型识别效果。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.vision import vision_client  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python scripts/test_vision.py <图片路径> [现场描述]")
        sys.exit(1)
    img = Path(sys.argv[1])
    if not img.exists():
        print(f"图片不存在: {img}")
        sys.exit(1)
    desc = sys.argv[2] if len(sys.argv) > 2 else ""

    data = img.read_bytes()
    print(f"图片: {img} ({len(data) // 1024}KB), 现场描述: {desc or '（无）'}")
    print("调用 qwen3.8max 视觉分析中（约 10-30s）...")
    try:
        result = vision_client.analyze_hazard(data, desc)
    except RuntimeError as exc:
        print(f"[失败] {exc}")
        sys.exit(1)

    print("\n===== 识别结果 =====")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    hazards = result.get("hazards") or []
    print(f"\n检出隐患: {result.get('detected')} | 数量: {len(hazards)}")
    for h in hazards:
        loc = h.get("location") or ""
        loc_s = f" | 坐标: {loc}" if loc else ""
        print(f"  - [{h.get('level', '?')}] {h.get('category', '?')}: {h.get('description', '')[:50]}{loc_s}")


if __name__ == "__main__":
    main()
