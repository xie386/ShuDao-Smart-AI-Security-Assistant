"""v2.0 大点 06 单测：样式/画框/编排/缺口/引用校验/落库。

不真调视觉模型与网搜（monkeypatch `_retrieve` / `_web_search` / `_synthesize`），
只验确定性逻辑；真机链路由 E2E 探针覆盖。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from src.ai import hazard_agent
from src.core import box_style
from src.services import hazard_analysis_service as svc
from src.utils import boxes


# --------------------------------------------------------------------------- #
# 1) 样式：类别定色相、等级定明度、线宽/字号自适应
# --------------------------------------------------------------------------- #
def test_level_changes_lightness_same_hue():
    major = box_style.color_for("重大", "物的不安全状态")
    minor = box_style.color_for("轻微", "物的不安全状态")
    assert major != minor
    assert sum(box_style.rgb_for("重大", "物的不安全状态")) < sum(
        box_style.rgb_for("轻微", "物的不安全状态"))


def test_category_changes_hue():
    colors = {box_style.color_for("一般", c) for c in box_style.CATEGORY_HUES}
    assert len(colors) == 4


def test_unknown_category_falls_back():
    assert box_style.color_for("一般", "未知类别") == box_style.color_for("一般", box_style.DEFAULT_CATEGORY)


def test_stroke_and_font_scale_with_width():
    assert box_style.stroke_for("重大", 4096) == 12
    assert box_style.stroke_for("重大", 1024) == 3          # 12 × 1024/4096
    assert box_style.stroke_for("轻微", 100) == 2            # 下限 2
    assert box_style.font_for(4096) == 64
    assert box_style.font_for(200) == 18                     # 下限 18


def test_palette_has_color_matrix():
    p = box_style.palette()
    assert set(p["colors"]) == set(box_style.CATEGORY_HUES)
    assert p["colors"]["人的不安全行为"]["重大"].startswith("#")
    assert p["fill_alpha"] == 0.25 and p["dim_fill_alpha"] == 0.07


# --------------------------------------------------------------------------- #
# 2) 画框：数量、格式、坐标还原、越界裁剪
# --------------------------------------------------------------------------- #
@pytest.fixture()
def sample_image(tmp_path: Path) -> Path:
    path = tmp_path / "scene.jpg"
    Image.new("RGB", (1024, 768), (90, 120, 80)).save(path, quality=90)
    return path


HAZARDS = [
    {"level": "重大", "category": "物的不安全状态", "location": [100, 120, 400, 380], "description": "木板散落"},
    {"level": "一般", "category": "人的不安全行为", "location": [500, 200, 900, 600], "description": "未戴安全帽"},
]


def test_draw_boxes_counts_and_outputs(sample_image, tmp_path):
    out = tmp_path / "scene_boxes.jpg"
    assert boxes.draw_hazard_boxes(sample_image, HAZARDS, out) == 2
    assert out.exists() and out.stat().st_size > 0
    with Image.open(out) as img:
        assert img.size == (1024, 768)


def test_jpg_much_smaller_than_png(tmp_path):
    """真实照片（有噪声）下 JPG q90 远小于无损 PNG；纯色合成图反而 PNG 更小，故用噪声图。"""
    import os

    noisy = tmp_path / "noise.jpg"
    Image.frombytes("RGB", (512, 512), os.urandom(512 * 512 * 3)).save(noisy, quality=92)
    jpg, png = tmp_path / "a.jpg", tmp_path / "a.png"
    boxes.draw_hazard_boxes(noisy, HAZARDS, jpg)
    boxes.draw_hazard_boxes(noisy, HAZARDS, png)
    assert jpg.stat().st_size < png.stat().st_size


def test_skip_null_or_invalid_location(sample_image, tmp_path):
    bad = [{"level": "一般", "location": None}, {"level": "一般", "location": [10, 10, 5, 5]},
           {"level": "一般"}]
    assert boxes.draw_hazard_boxes(sample_image, bad, tmp_path / "x.jpg") == 0


def test_scale_restores_original_coords(sample_image, tmp_path):
    """模型给 1024 缩放图坐标，原图 2048 → ×2 后仍画在框内（不越界即算通过）。"""
    big = tmp_path / "big.jpg"
    Image.new("RGB", (2048, 1536), (60, 60, 60)).save(big)
    out = tmp_path / "big_boxes.jpg"
    drawn = boxes.draw_hazard_boxes(
        big, [{"level": "较大", "category": "管理缺陷", "location": [10, 10, 100, 100]}],
        out, scale=(2.0, 2.0),
    )
    assert drawn == 1
    # 白色描边必然让原图局部像素变亮
    with Image.open(out) as img:
        assert img.getpixel((20, 20)) != (60, 60, 60)


def test_label_mode_text():
    h = {"level": "重大", "category": "物的不安全状态", "description": "电缆裸露"}
    assert boxes._label_text(3, h, "index") == "#3"
    full = boxes._label_text(3, h, "full")
    assert "#3" in full and "重大" in full


# --------------------------------------------------------------------------- #
# 3) 坐标归一化 / 缓存键
# --------------------------------------------------------------------------- #
def test_to_original_multiplies_scale():
    out = svc.to_original([{"location": [10, 20, 30, 40], "level": "一般"}], (2.0, 3.0))
    assert out[0]["location"] == [20, 60, 60, 120]


def test_to_original_keeps_missing_location():
    out = svc.to_original([{"location": None}], (2.0, 2.0))
    assert out[0]["location"] is None


def test_cache_key_depends_on_scene():
    a = svc.cache_key(b"img", "场景A")
    b = svc.cache_key(b"img", "场景B")
    assert a != b and a.startswith("hazard:ai:")


# --------------------------------------------------------------------------- #
# 4) 检索与缺口判定
# --------------------------------------------------------------------------- #
def _blocks(doc="shd-proc-check", chapter="11.材料堆放", score=0.9, text="材料分类存放"):
    return [{"metadata": {"doc": doc, "chapter": chapter}, "rerank_score": score, "text": text}]


def test_retrieve_all_detects_gaps(monkeypatch):
    def fake_retrieve(query: str):
        return _blocks(score=0.9) if "木板" in query else _blocks(score=0.5)

    monkeypatch.setattr(hazard_agent, "_retrieve", fake_retrieve)
    res = hazard_agent.retrieve_all([
        {"description": "木板散落", "category": "物的不安全状态"},
        {"description": "沟渠无警示", "category": "环境的不安全因素"},
    ])
    assert res["gaps"] == [2]
    assert res["scores"][1] == 0.9 and res["scores"][2] == 0.5


def test_retrieve_all_empty_blocks_is_gap(monkeypatch):
    monkeypatch.setattr(hazard_agent, "_retrieve", lambda q: [])
    res = hazard_agent.retrieve_all([{"description": "杂草堆积"}])
    assert res["gaps"] == [1] and res["scores"][1] == 0.0


def test_retrieve_failure_treated_as_gap(monkeypatch):
    def boom(q):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(hazard_agent, "_retrieve", boom)
    res = hazard_agent.retrieve_all([{"description": "临时用电"}])
    assert res["gaps"] == [1]


# --------------------------------------------------------------------------- #
# 5) 缺口补料：合并一次网搜
# --------------------------------------------------------------------------- #
def test_fill_gap_merges_into_one_call(monkeypatch):
    calls = []

    def fake_web(question):
        calls.append(question)
        return {"ok": True, "md": "沟渠临边应设防护栏并挂牌", "domains": ["hunan.gov.cn"], "contrib_id": 88}

    monkeypatch.setattr(hazard_agent, "_web_search", fake_web)
    hazards = [{"description": "围挡杂草堆积"}, {"description": "沟渠边缘无警示"}]
    out = hazard_agent.fill_gap(hazards, [1, 2])
    assert len(calls) == 1                       # 两条缺口只搜一次
    assert "杂草" in calls[0] and "沟渠" in calls[0]
    assert out["ok"] and out["hosts"] == ["hunan.gov.cn"] and out["contrib_id"] == 88


def test_fill_gap_disabled(monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "HAZARD_WEB_FILL_ENABLED", False, raising=False)
    assert hazard_agent.fill_gap([{"description": "x"}], [1])["ok"] is False


def test_fill_gap_failure_returns_note(monkeypatch):
    def boom(q):
        raise TimeoutError("60s")

    monkeypatch.setattr(hazard_agent, "_web_search", boom)
    out = hazard_agent.fill_gap([{"description": "沟渠"}], [1])
    assert out["ok"] is False and "TimeoutError" in out["note"]


def test_fill_gap_hard_timeout(monkeypatch):
    """run_sync 内部预算不保证总时长（实测 105s）→ 外层必须硬超时，超时按无依据返回。"""
    import time as _t

    from src.core.config import settings

    monkeypatch.setattr(settings, "HAZARD_WEB_TIMEOUT", 0.3, raising=False)
    monkeypatch.setattr(settings, "HAZARD_WEB_FILL_ENABLED", True, raising=False)
    monkeypatch.setattr(hazard_agent, "_web_search", lambda q: _t.sleep(1.5) or {"ok": True, "md": "x"})
    out = hazard_agent.fill_gap([{"description": "沟渠"}], [1])
    assert out["ok"] is False and "超时" in out["note"]


def test_fill_gap_no_gaps_is_noop(monkeypatch):
    called = []
    monkeypatch.setattr(hazard_agent, "_web_search", lambda q: called.append(q) or {})
    assert hazard_agent.fill_gap([{"description": "x"}], [])["ok"] is False
    assert called == []


# --------------------------------------------------------------------------- #
# 6) 建议汇总：引用校验（防编造）
# --------------------------------------------------------------------------- #
KB = {1: [{"doc": "shd-proc-check", "chapter": "11.材料堆放", "score": 0.91, "text": "分类存放"}]}


def test_synthesize_grounded_with_kb(monkeypatch):
    monkeypatch.setattr(hazard_agent, "_synthesize", lambda p: json.dumps([{
        "index": 1, "suggestion": "材料分类存放并设标识",
        "refs": [{"kind": "kb", "doc": "shd-proc-check", "chapter": "11.材料堆放"}],
        "grounded": True, "gap_note": "",
    }], ensure_ascii=False))
    out = hazard_agent.synthesize([{"description": "木板散落", "level": "一般", "category": "物的不安全状态"}],
                                  KB, {"ok": False, "md": "", "hosts": [], "note": ""})
    h = out["hazards"][0]
    assert h["grounded"] is True and h["suggestion"]
    assert h["refs"][0]["doc"] == "shd-proc-check"


def test_synthesize_drops_fabricated_kb_ref(monkeypatch):
    monkeypatch.setattr(hazard_agent, "_synthesize", lambda p: json.dumps([{
        "index": 1, "suggestion": "按《GB 99999》执行",
        "refs": [{"kind": "kb", "doc": "GB 99999", "chapter": "第1条"}],
        "grounded": True, "gap_note": "",
    }], ensure_ascii=False))
    out = hazard_agent.synthesize([{"description": "木板散落"}], KB, None)
    h = out["hazards"][0]
    assert h["grounded"] is False and h["suggestion"] == "" and h["refs"] == []


def test_synthesize_falls_back_to_top_block(monkeypatch):
    """模型 grounded=True 但 doc/chapter 写法对不上 → 回落该条检索到的 Top1，不白丢证据。"""
    monkeypatch.setattr(hazard_agent, "_synthesize", lambda p: json.dumps([{
        "index": 1, "suggestion": "材料分类存放并设标识",
        "refs": [{"kind": "kb", "doc": "shd-proc-check", "chapter": "第X条（章节名写错）"}],
        "grounded": True,
    }], ensure_ascii=False))
    out = hazard_agent.synthesize([{"description": "木板散落"}], KB, None)
    h = out["hazards"][0]
    assert h["grounded"] is True
    assert h["refs"][0]["doc"] == "shd-proc-check"


def test_synthesize_accepts_web_ref_when_host_known(monkeypatch):
    monkeypatch.setattr(hazard_agent, "_synthesize", lambda p: json.dumps([{
        "index": 1, "suggestion": "可参照规范设置临边防护栏并挂警示牌",
        "refs": [{"kind": "web", "host": "hunan.gov.cn"}],
        "grounded": True, "gap_note": "",
    }], ensure_ascii=False))
    out = hazard_agent.synthesize([{"description": "沟渠无警示"}], {1: []},
                                  {"ok": True, "md": "…", "hosts": ["hunan.gov.cn"], "note": ""})
    h = out["hazards"][0]
    assert h["grounded"] is True and h["refs"] == [{"kind": "web", "host": "hunan.gov.cn"}]


def test_synthesize_rejects_unknown_web_host(monkeypatch):
    monkeypatch.setattr(hazard_agent, "_synthesize", lambda p: json.dumps([{
        "index": 1, "suggestion": "照做即可",
        "refs": [{"kind": "web", "host": "evil.example.com"}],
        "grounded": True,
    }], ensure_ascii=False))
    out = hazard_agent.synthesize([{"description": "x"}], {1: []},
                                  {"ok": True, "md": "…", "hosts": ["hunan.gov.cn"], "note": ""})
    assert out["hazards"][0]["grounded"] is False


def test_synthesize_empty_when_no_evidence(monkeypatch):
    monkeypatch.setattr(hazard_agent, "_synthesize", lambda p: json.dumps([{
        "index": 1, "suggestion": "", "refs": [], "grounded": False,
        "gap_note": "知识库暂无该类隐患依据",
    }], ensure_ascii=False))
    out = hazard_agent.synthesize([{"description": "沟渠无警示"}], {1: []}, None)
    h = out["hazards"][0]
    assert h["grounded"] is False and h["suggestion"] == ""
    assert "暂无" in h["gap_note"]


def test_synthesize_survives_bad_json(monkeypatch):
    monkeypatch.setattr(hazard_agent, "_synthesize", lambda p: "这不是 JSON")
    out = hazard_agent.synthesize([{"description": "x"}], {1: []}, None)
    assert out["hazards"][0]["grounded"] is False


# --------------------------------------------------------------------------- #
# 7) 端到端编排（假依赖）
# --------------------------------------------------------------------------- #
def test_enrich_full_flow(monkeypatch):
    def fake_retrieve(q):
        return _blocks(score=0.9) if "木板" in q else []

    monkeypatch.setattr(hazard_agent, "_retrieve", fake_retrieve)
    monkeypatch.setattr(hazard_agent, "_web_search",
                        lambda q: {"ok": True, "md": "沟渠应设护栏与警示", "domains": ["hunan.gov.cn"],
                                   "contrib_id": 91})
    monkeypatch.setattr(hazard_agent, "_synthesize", lambda p: json.dumps([
        {"index": 1, "suggestion": "材料分类存放并设标识",
         "refs": [{"kind": "kb", "doc": "shd-proc-check", "chapter": "11.材料堆放"}], "grounded": True},
        {"index": 2, "suggestion": "可参照规范设置临边护栏并挂警示牌",
         "refs": [{"kind": "web", "host": "hunan.gov.cn"}], "grounded": True},
    ], ensure_ascii=False))

    out = hazard_agent.enrich([
        {"description": "木板散落", "level": "一般", "category": "物的不安全状态", "location": [1, 2, 3, 4]},
        {"description": "沟渠无警示", "level": "较大", "category": "环境的不安全因素", "location": [5, 6, 7, 8]},
    ])
    h1, h2 = out["hazards"]
    assert h1["grounded"] and h1["refs"][0]["kind"] == "kb"
    assert h2["grounded"] and h2["refs"][0]["kind"] == "web"
    assert out["gap"]["indices"] == [2]
    assert h1["location"] == [1, 2, 3, 4]        # 坐标不被破坏


def test_enrich_no_hazards():
    assert hazard_agent.enrich([])["hazards"] == []


# --------------------------------------------------------------------------- #
# 8) 落库（真实 MySQL；无隐患数据则跳过）
# --------------------------------------------------------------------------- #
def test_persist_and_load_latest(db_session):
    from sqlalchemy import select, text

    from src.models.hazard import Hazard

    hazard_id = db_session.scalar(select(Hazard.id).limit(1))
    if not hazard_id:
        pytest.skip("库里没有隐患记录，跳过落库用例")

    analysis = {"model": "qwen-test", "hazards": [
        {"level": "一般", "category": "物的不安全状态", "location": [1, 2, 3, 4],
         "suggestion": "材料分类存放", "refs": [{"kind": "kb", "doc": "d", "chapter": "c"}],
         "grounded": True}]}
    row_id = svc.persist(db_session, hazard_id, analysis, "/uploads/x_boxes.jpg")
    try:
        assert row_id
        latest = svc.load_latest(db_session, hazard_id)
        assert latest and latest["annotated_url"] == "/uploads/x_boxes.jpg"
        assert latest["hazards"][0]["suggestion"] == "材料分类存放"
    finally:
        db_session.execute(text("DELETE FROM hazard_ai_analysis WHERE id = :i"), {"i": row_id})
        db_session.commit()


def test_attach_ai_sets_annotated_url(db_session):
    from sqlalchemy import select

    from src.models.hazard import Hazard

    hazards = db_session.scalars(select(Hazard).limit(3)).all()
    if not hazards:
        pytest.skip("库里没有隐患记录")
    from src.services.hazard_service import HazardService

    HazardService.attach_ai(db_session, list(hazards))
    assert all(hasattr(h, "annotated_url") for h in hazards)


# --------------------------------------------------------------------------- #
# 9) 服务层：旧 POST 路径（analyze）与回滚开关
# --------------------------------------------------------------------------- #
def test_analyze_scales_and_merges_agent(monkeypatch):
    from src.ai import vision as vision_mod

    monkeypatch.setattr(vision_mod.vision_client, "analyze_hazard", lambda b, scene_description="": {
        "detected": True, "summary": "1 处隐患", "_scale": (2.0, 2.0),
        "hazards": [{"level": "一般", "category": "物的不安全状态",
                     "description": "木板散落", "suggestion": "模型自写", "location": [10, 20, 30, 40]}],
    })
    monkeypatch.setattr(svc, "_cache_get", lambda k: None)
    monkeypatch.setattr(svc, "_cache_set", lambda k, v: None)
    monkeypatch.setattr("src.ai.hazard_agent.enrich", lambda hazards, conversation_id=None: {
        "hazards": [dict(hazards[0], suggestion="材料分类存放", grounded=True,
                         refs=[{"kind": "kb", "doc": "d", "chapter": "c"}], gap_note="")],
        "gap": {"indices": []}, "meta": {}, "elapsed_s": 1.0,
    })
    out = svc.analyze(b"img", "场景")
    h = out["hazards"][0]
    assert h["location"] == [20, 40, 60, 80]        # 坐标已还原原图
    assert h["grounded"] is True and h["suggestion"] == "材料分类存放"


def test_analyze_rolls_back_to_vision_suggestions(monkeypatch):
    from src.ai import vision as vision_mod
    from src.core.config import settings

    monkeypatch.setattr(settings, "HAZARD_AGENT_ENABLED", False, raising=False)
    monkeypatch.setattr(vision_mod.vision_client, "analyze_hazard", lambda b, scene_description="": {
        "detected": True, "_scale": (1.0, 1.0),
        "hazards": [{"level": "一般", "suggestion": "视觉自写建议", "location": [1, 2, 3, 4]}],
    })
    monkeypatch.setattr(svc, "_cache_get", lambda k: None)
    monkeypatch.setattr(svc, "_cache_set", lambda k, v: None)
    out = svc.analyze(b"img", "")
    h = out["hazards"][0]
    assert h["suggestion"] == "视觉自写建议" and h["grounded"] is False
    assert "未启用知识库联动" in h["gap_note"]
