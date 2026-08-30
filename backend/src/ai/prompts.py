"""提示词工程加载器：读取 prompt/prompts.yml，提供模板访问与渲染。

参考 deep_search_pro/agent/prompts.py 模式：
- yaml.safe_load（绝不使用 yaml.load，防 YAML 注入）；
- lru_cache 单次加载，全进程复用；
- render() 用 str.format 填充 {placeholder}。
 
用法：
    from src.ai.prompts import get_prompt, render

    render("safety_qa", retrieved_context=ctx, history=hist, question=q)
    # -> 完整 system_prompt 文本，可直接作为 llm_client.chat 的 system 消息
"""
import logging
import re
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# backend/src/ai/prompts.py -> parents[0]=ai, [1]=src, [2]=backend
_PROMPT_FILE = Path(__file__).resolve().parents[2] / "prompt" / "prompts.yml"

# 占位符形如 {topic}；模板中的 JSON 花括号（{"title": ...} 带引号）不会被匹配
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@lru_cache
def load_prompts() -> dict:
    """加载全部提示词场景配置（首次调用后缓存）。"""
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    logger.info("提示词工程已加载: %s（%d 个场景）", _PROMPT_FILE, len(data))
    return data


def get_prompt(name: str) -> dict:
    """按场景名取提示词配置（含 name/description/system_prompt）。"""
    prompts = load_prompts()
    if name not in prompts:
        raise KeyError(
            f"未定义的提示词场景: {name!r}（prompts.yml 中可用: {list(prompts)}）"
        )
    return prompts[name]


def render(name: str, **kwargs) -> str:
    """渲染 system_prompt。

    注意：不能用 str.format()——模板中 JSON 的花括号会被误判为占位符。
    改为白名单 replace：先校验缺参，再逐占位符替换；JSON 花括号原样保留。
    """
    prompt = get_prompt(name)["system_prompt"]
    placeholders = set(_PLACEHOLDER_RE.findall(prompt))
    missing = placeholders - set(kwargs)
    if missing:
        raise KeyError(f"渲染 {name!r} 缺少占位符参数: {sorted(missing)}")
    for key, value in kwargs.items():
        prompt = prompt.replace("{" + key + "}", str(value))
    return prompt
