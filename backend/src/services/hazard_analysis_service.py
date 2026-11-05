"""隐患 AI 识别编排服务（v2.0 大点 06）。

职责：
1. 把视觉结果的坐标**归一化到原图坐标系**（模型给的是 1024 缩放图坐标，此处 × scale 还原后丢弃 `_scale`）；
2. 调 `hazard_agent` 补齐建议（B 混合编排），非流式一次跑完；
3. 服务端烘焙标注图（JPG q90，裁定 4）；
4. 上报落库 `hazard_ai_analysis`（裁定 5）；
5. 同图同场景 10 分钟内结果缓存（Redis，命中直接返回，跳过视觉 + RAG + 网搜）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)


def _safe_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def to_original(hazards: list[dict], scale: tuple[float, float] | None) -> list[dict]:
    """模型坐标（缩放图）→ 原图坐标。就地返回新列表，保留其余字段。"""
    sx, sy = scale or (1.0, 1.0)
    out = []
    for h in hazards or []:
        h = dict(h)
        loc = h.get("location")
        if isinstance(loc, (list, tuple)) and len(loc) == 4:
            try:
                x1, y1, x2, y2 = (float(v) for v in loc)
                h["location"] = [round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy)]
            except (TypeError, ValueError):
                h["location"] = None
        out.append(h)
    return out


def cache_key(image_bytes: bytes, scene: str) -> str:
    h = hashlib.sha1()
    h.update(image_bytes)
    h.update(b"\x00")
    h.update((scene or "").encode("utf-8"))
    return f"hazard:ai:{h.hexdigest()}"


def _cache_get(key: str) -> dict | None:
    if not settings.HAZARD_CACHE_TTL:
        return None
    try:
        from src.core.redis_client import get_redis

        raw = get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001 —— 缓存不可用不影响主流程
        logger.debug("隐患缓存读失败：%s", exc)
        return None


def _cache_set(key: str, payload: dict) -> None:
    if not settings.HAZARD_CACHE_TTL:
        return
    try:
        from src.core.redis_client import get_redis

        get_redis().setex(key, settings.HAZARD_CACHE_TTL, _safe_json(payload))
    except Exception as exc:  # noqa: BLE001
        logger.debug("隐患缓存写失败：%s", exc)


def analyze(image_bytes: bytes, scene: str = "", *, conversation_id: int | None = None,
            use_agent: bool = True) -> dict:
    """视觉 + Agent 一次跑完（旧 POST 路径）。返回完整结果（含 hazards 建议/引用）。"""
    from src.ai.hazard_agent import enrich
    from src.ai.vision import vision_client

    key = cache_key(image_bytes, scene)
    cached = _cache_get(key)
    if cached:
        logger.info("隐患识别命中缓存：%s", key)
        cached["cached"] = True
        return cached

    res = vision_client.analyze_hazard(image_bytes, scene_description=scene)
    scale = res.pop("_scale", None)
    hazards = res.get("hazards") or []

    # 视觉结果往往是 1024 缩放图坐标 → 还原原图，前端 SVG 与后端烘焙共用一套坐标
    hazards = to_original(hazards, scale)
    res["hazards"] = hazards

    if use_agent and settings.HAZARD_AGENT_ENABLED and hazards:
        try:
            agent = enrich(hazards, conversation_id=conversation_id)
            res["hazards"] = agent["hazards"]
            res["gap"] = agent.get("gap")
            res["agent_meta"] = agent.get("meta")
            res["agent_elapsed_s"] = agent.get("elapsed_s")
        except Exception as exc:  # noqa: BLE001 —— Agent 挂了不能吞掉识别结果
            logger.warning("隐患 Agent 失败，退回视觉建议：%s", exc)
            res["agent_error"] = f"{type(exc).__name__}: {exc}"[:200]
    else:
        # 回滚口径：视觉模型自己写建议（旧行为），标记未走知识库
        for h in res["hazards"]:
            h.setdefault("grounded", False)
            h.setdefault("refs", [])
            h.setdefault("gap_note", "未启用知识库联动")

    res["cached"] = False
    _cache_set(key, res)
    return res


def render_annotated(image_path: str | Path, hazards: list[dict],
                     out_path: str | Path, *, labels: str | None = None) -> int:
    """服务端烘焙标注图（JPG）。坐标已是原图坐标，不需要再缩放。"""
    from src.utils.boxes import draw_hazard_boxes

    kwargs = {}
    if labels:
        kwargs["labels"] = labels
    return draw_hazard_boxes(
        image_path, hazards, out_path,
        jpeg_quality=settings.HAZARD_ANNOTATE_JPEG_QUALITY, **kwargs,
    )


def persist(db, hazard_id: int, analysis: dict, annotated_url: str | None,
            generated_by: str = "uploader", commit: bool = True) -> int | None:
    """上报时落库（裁定 5）。失败只记日志，不影响上报本身。

    `commit=False` 供 H01 复用同一事务（此时不 refresh，也不回滚外层会话）。
    """
    from src.models.hazard_ai import HazardAiAnalysis

    try:
        row = HazardAiAnalysis(
            hazard_id=hazard_id,
            model=(analysis.get("model") or settings.QWEN_MODEL)[:64],
            boxes_json=_safe_json(analysis.get("hazards") or []),
            raw_json=_safe_json(analysis)[:60000],
            annotated_url=(annotated_url or "")[:255] or None,
            generated_by=generated_by[:16],
        )
        db.add(row)
        if commit:
            db.commit()
            db.refresh(row)
        else:
            db.flush()
        return row.id
    except Exception as exc:  # noqa: BLE001
        if commit:
            db.rollback()
        logger.warning("隐患 AI 分析落库失败（hazard=%s）：%s", hazard_id, exc)
        return None


def load_latest(db, hazard_id: int) -> dict | None:
    """取该隐患最近一次 AI 分析（详情页用）。"""
    from src.models.hazard_ai import HazardAiAnalysis

    row = (
        db.query(HazardAiAnalysis)
        .filter(HazardAiAnalysis.hazard_id == hazard_id)
        .order_by(HazardAiAnalysis.id.desc())
        .first()
    )
    if not row:
        return None
    try:
        boxes = json.loads(row.boxes_json or "[]")
    except json.JSONDecodeError:
        boxes = []
    return {
        "id": row.id,
        "model": row.model,
        "hazards": boxes,
        "annotated_url": row.annotated_url,
        "generated_by": row.generated_by,
        "created_at": row.created_at.isoformat(sep=" ", timespec="seconds") if row.created_at else None,
    }
