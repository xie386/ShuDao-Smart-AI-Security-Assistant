#!/usr/bin/env python
"""提示词工程冒烟验证：加载 YAML → 渲染模板 → 真实 LLM 调用（安全问答场景）。

用法（在 backend/ 目录下）：
    PYTHONPATH= C:\\python\\python.exe scripts/prompt_smoke.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.prompts import get_prompt, load_prompts, render  # noqa: E402


def main() -> None:
    prompts = load_prompts()
    print(f"已加载场景: {list(prompts)}")

    print("\n=== 1. 渲染 safety_qa（RAG 问答）===")
    qa = render(
        "safety_qa",
        retrieved_context="[法规] 《建设工程安全生产管理条例》第26条：高处作业必须系挂安全带，做到高挂低用。",
        history="用户: 高处作业注意什么？\n助手: 高处作业需系挂安全带，做到高挂低用。",
        question="脚手架搭设有什么要求？",
    )
    print(qa[:300], "...")

    print("\n=== 2. 渲染 ppt_outline（P01 大纲）===")
    outline = render("ppt_outline", topic="施工现场高处作业安全")
    print(outline[:200], "...")

    print("\n=== 3. 渲染 hazard_vision（视觉隐患识别）===")
    vision = render("hazard_vision", scene_description="（无）")
    print(vision[:150], "...")

    print("\n=== 4. 渲染 query_rewrite（查询改写）===")
    qr = render("query_rewrite", question="高处作业有啥要求")
    print(qr[:150], "...")

    print("\n=== 5. 真实 LLM 调用（safety_qa 走 llm_client）===")
    from src.ai.llm_client import llm_client

    messages = [
        {"role": "system", "content": get_prompt("safety_qa")["system_prompt"].format(
            retrieved_context="[法规] 高处作业必须系挂安全带，做到高挂低用。",
            history="",
            question="高处作业为什么要系安全带？",
        )},
    ]
    answer = llm_client.chat(messages, temperature=0.3)
    print(f"AI 回答: {answer[:120]}...")


if __name__ == "__main__":
    main()
