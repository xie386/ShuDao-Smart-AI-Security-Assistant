#!/usr/bin/env python
"""LLM 客户端冒烟测试（D1）：真实调用 DeepSeek（非流式 + 流式）。
 
用法（在 backend/ 目录下）：
    PYTHONPATH= C:\\python\\python.exe scripts/llm_smoke.py

需要 .env 中 DEEPSEEK_API_KEY 已配置。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.llm_client import llm_client  # noqa: E402

MESSAGES = [
    {"role": "system", "content": "你是安全生产助手，回答请简洁。"},
    {"role": "user", "content": "用一句话说明高处作业为什么要系安全带？"},
]


def main() -> None:
    print("=== 非流式 chat ===")
    answer = llm_client.chat(MESSAGES, temperature=0.3)
    print(f"完整回答({len(answer)}字): {answer[:120]}...")

    print("\n=== 流式 chat_stream ===")
    pieces = []
    for chunk in llm_client.chat_stream(MESSAGES, temperature=0.3):
        pieces.append(chunk)
        print(chunk, end="", flush=True)
    print(f"\n流式累计 {len(''.join(pieces))} 字")


if __name__ == "__main__":
    main()
