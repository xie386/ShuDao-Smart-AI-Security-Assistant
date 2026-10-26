"""可选直连 PPT LLM 的**抽象位**（v2.0 大点 02 方式 A）。

为什么只留接口不留实现：
- 用户要"两种工作方式"（可选直连 PPT LLM / 常驻 PPT 生成 Agent）；
- 唯一实现（百度千帆 PPT）已转付费且额度是项目级共享 → 大点 02 整体移除（裁定 1）；
- 现在没有任何可用的付费 provider，所以这里只定义契约 + 配置位（裁定 2）。

以后接新 provider 的步骤（不需要改业务代码）：
1. 写一个 `PptLlmProvider` 实现（`generate(outline, spec) -> Path`）；
2. 在 `_REGISTRY` 里登记名字；
3. `.env` 填 `PPT_LLM_PROVIDER` / `PPT_LLM_BASE_URL` / `PPT_LLM_API_KEY`；
4. `GET /api/ppt/capabilities` 会自动把它报成可用，前端出现该选项。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from src.core.config import settings


@runtime_checkable
class PptLlmProvider(Protocol):
    """直连 PPT LLM 的契约：给大纲与规格，返回已落盘的 pptx 路径。"""

    name: str

    def generate(self, outline: dict, spec: dict) -> Path:  # pragma: no cover - 协议
        ...


#: provider 注册表：name -> 工厂函数。当前为空（百度实现已移除）。
_REGISTRY: dict[str, object] = {}


def register(name: str, factory: object) -> None:
    """注册一个 provider 工厂（供以后新增实现时调用）。"""
    _REGISTRY[name] = factory


def build_provider() -> PptLlmProvider | None:
    """按配置构造 provider；未配置或没有实现时返回 None（前端不显示该入口）。"""
    name = (settings.PPT_LLM_PROVIDER or "").strip().lower()
    if not name or name not in _REGISTRY:
        return None
    factory = _REGISTRY[name]
    try:
        provider = factory() if callable(factory) else factory
    except Exception:  # noqa: BLE001 —— 构造失败等同于不可用
        return None
    return provider  # type: ignore[return-value]


def available() -> bool:
    return build_provider() is not None


def capabilities() -> dict:
    """给 `GET /api/ppt/capabilities` 用的能力清单。"""
    from src.ai import ppt_tools, vision_agent

    return {
        "agent": bool(settings.PPT_AGENT_ENABLED),
        "ppt_llm": available(),
        "ppt_llm_provider": (settings.PPT_LLM_PROVIDER or "") or None,
        "officecli": ppt_tools.officecli_available(),
        "officecli_path": str(ppt_tools.officecli_path()) if ppt_tools.officecli_path() else None,
        "vision": vision_agent.available(),
        "vision_model": (settings.PPT_VISION_MODEL if vision_agent.available() else None),
        "images": bool(settings.PPT_IMAGES_ENABLED),
        "image_provider": (ppt_tools.current_provider() if settings.PPT_IMAGES_ENABLED else None),
        "page_limit": settings.PPT_AGENT_PAGE_LIMIT,
    }
