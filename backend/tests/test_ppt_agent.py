"""v2.0 大点 02 · PPT 工坊升级 单测。

覆盖（对照规划文档 §02 §3.15 验证项）：
- 沙箱路径（`src/core/paths.py`）：文件名净化、`..` 越界拦截、任务目录命名、清理保留成品 —— 验证项 6/8
- 版式（`src/ai/ppt_layout.py`）：parent 注入、要点不静默截断、双栏、`_fix` 修补 —— 验证项 4
- 编排（`src/ai/ppt_agent.py`）：页面序列、规划兜底、修法递进、模板主题色读取
- 进度/锁（`src/services/ppt_progress.py`）：Redis 正常读回、Redis 不可用降级
- 下载（`src/services/ppt_service.py`）：沙箱优先、越界 404
- 能力接口：未登录 401、登录后字段齐全

设计约束：不调 LLM、不联网、不动真实 `user_data/`（一律 tmp_path）。
"""
import json
import types
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.ai import ppt_agent, ppt_layout
from src.core import paths as ppt_paths
from src.core.db import get_db
from src.core.security import create_access_token
from src.main import app
from src.services import ppt_progress
from src.services.ppt_service import PptService

UID, OTHER = 17, 608


def _auth(uid: int = UID) -> dict:
    return {"Authorization": f"Bearer {create_access_token(uid, 'employee')}"}


# --------------------------------------------------------------------------- #
# 沙箱路径
# --------------------------------------------------------------------------- #
def test_safe_name_strips_path_and_illegal_chars():
    assert ppt_paths.safe_name("../../etc/passwd") == "passwd"
    assert ppt_paths.safe_name(r"..\..\win.ini") == "win.ini"
    assert ppt_paths.safe_name("隧道:施工*安全?.pptx") == "隧道_施工_安全_.pptx"
    assert ppt_paths.safe_name("..") == ""
    assert ppt_paths.safe_name("   ") == ""
    assert ppt_paths.safe_name("", fallback="ppt") == "ppt"


def test_safe_join_neutralises_traversal(tmp_path):
    """`../x` 不是"报错"而是"被压成 x"——落在根内；空名字才报错。"""
    root = tmp_path / "u17"
    root.mkdir()
    assert ppt_paths.safe_join(root, "shot.png").parent == root.resolve()
    assert ppt_paths.safe_join(root, "../escape.png") == (root / "escape.png").resolve()
    assert ppt_paths.safe_join(root, "C:/Windows/system32/x.dll") == (root / "x.dll").resolve()
    for bad in ("..", "", "   "):
        with pytest.raises(ValueError):
            ppt_paths.safe_join(root, bad)


def test_task_dirs_and_out_name_are_per_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(ppt_paths, "SANDBOX_ROOT", tmp_path)
    a = ppt_paths.out_path(UID, 6, "高处作业安全")
    b = ppt_paths.out_path(OTHER, 21, "高处作业安全")
    assert a != b
    assert a.name.startswith(f"u{UID}_t6_") and a.suffix == ".pptx"
    assert ppt_paths.task_dir(UID, 6, "shots").parent.name == "t6"
    assert ppt_paths.task_dir(UID, 6, "logs").parent == ppt_paths.task_root(UID, 6)
    with pytest.raises(ValueError):
        ppt_paths.task_dir(UID, 6, "screenshots")


def test_cleanup_keeps_out_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(ppt_paths, "SANDBOX_ROOT", tmp_path)
    for kind in ppt_paths.TASK_KINDS:
        d = ppt_paths.task_dir(UID, 7, kind)
        ppt_paths.ensure_dir(d)
        (d / "x.txt").write_text("x", encoding="utf-8")
    result = ppt_paths.cleanup_task(UID, 7, keep_out=True)
    assert set(result["removed"]) == {"images", "shots", "drafts", "logs"}
    assert result["kept"] == ["out"]
    assert result["files"] == 4
    assert (ppt_paths.task_dir(UID, 7, "out") / "x.txt").exists()
    assert not ppt_paths.task_dir(UID, 7, "shots").exists()

    result2 = ppt_paths.cleanup_task(UID, 7, keep_out=False)
    assert result2["kept"] == [] and "out" in result2["removed"]


# --------------------------------------------------------------------------- #
# OfficeCLI 定位（随项目分发：clone 即用，不需要每人配 .env）
# --------------------------------------------------------------------------- #
def test_officecli_path_prefers_vendored(monkeypatch):
    from src.ai import ppt_tools

    monkeypatch.setattr(ppt_tools.settings, "PPT_OFFICECLI_PATH", "")
    if not ppt_tools.VENDORED_OFFICECLI.is_file():
        pytest.skip("本仓库未分发 tools/officecli/officecli.exe")
    p = ppt_tools.officecli_path()
    assert p == ppt_tools.VENDORED_OFFICECLI
    assert ppt_tools.officecli_available() is True


def test_officecli_explicit_path_wins_and_relative_is_project_rooted(tmp_path, monkeypatch):
    from src.ai import ppt_tools

    fake = tmp_path / "custom" / "officecli.exe"
    fake.parent.mkdir()
    fake.write_bytes(b"MZ")
    monkeypatch.setattr(ppt_tools.settings, "PPT_OFFICECLI_PATH", str(fake))
    assert ppt_tools.officecli_path() == fake

    # 相对路径按项目根解析（而不是 CWD）
    monkeypatch.setattr(ppt_tools.settings, "PPT_OFFICECLI_PATH", "tools/officecli/officecli.exe")
    resolved = ppt_tools.officecli_path()
    if ppt_tools.VENDORED_OFFICECLI.is_file():
        assert resolved == ppt_tools.VENDORED_OFFICECLI


def test_officecli_missing_degrades_to_none(tmp_path, monkeypatch):
    from src.ai import ppt_tools

    monkeypatch.setattr(ppt_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ppt_tools, "VENDORED_OFFICECLI", tmp_path / "none1.exe")
    monkeypatch.setattr(ppt_tools, "LEGACY_OFFICECLI", tmp_path / "none2.exe")
    monkeypatch.setattr(ppt_tools.settings, "PPT_OFFICECLI_PATH", str(tmp_path / "nope.exe"))
    assert ppt_tools.officecli_path() is None
    assert ppt_tools.officecli_available() is False
    # 没有可执行文件时调用必须抛明确错误（上层据此走 python-pptx 兜底）
    with pytest.raises(RuntimeError, match="OfficeCLI 不可用"):
        ppt_tools._oci("--version")


# --------------------------------------------------------------------------- #
# 版式
# --------------------------------------------------------------------------- #
def _commands(spec, image=None, theme=None, slide=1):
    return ppt_layout.build(slide, spec, slide, image=image, theme=theme)


def test_layout_injects_parent_for_all_elements():
    cmds = _commands({"type": "content", "title": "标题", "bullets": ["a", "b"]})
    slides = [c for c in cmds if c.get("type") == "slide"]
    assert len(slides) == 1 and slides[0]["parent"] == "/"
    others = [c for c in cmds if c.get("type") != "slide"]
    assert others and all(c["parent"] == "/slide[1]" for c in others)


def test_layout_does_not_silently_truncate_below_cap():
    bullets = [f"要点{i}" for i in range(6)]
    cmds = _commands({"type": "content", "title": "六条", "bullets": bullets})
    body = [c for c in cmds if c.get("type") == "textbox" and "▸" in c["props"]["text"]]
    joined = "".join(c["props"]["text"] for c in body)
    assert len(body) == 2                       # 6 条 → 双栏
    assert all(f"要点{i}" in joined for i in range(6))


def test_layout_truncates_above_cap_only():
    bullets = [f"要点{i}" for i in range(14)]
    cmds = _commands({"type": "content", "title": "太多", "bullets": bullets})
    body = [c for c in cmds if c.get("type") == "textbox" and "▸" in c["props"]["text"]]
    joined = "".join(c["props"]["text"] for c in body)
    shown = joined.count("▸")
    assert shown == ppt_layout.MAX_BULLETS
    assert "要点13" not in joined


def test_layout_fix_shrinks_and_drops_image():
    image = {"path": "x.png", "attribution": "CC BY 某人"}
    base = _commands({"type": "content", "title": "t", "bullets": ["a", "b", "c"]}, image=image)
    fixed = _commands({"type": "content", "title": "t", "bullets": ["a", "b", "c"],
                       "_fix": {"font_delta": -4, "drop_image": True}}, image=image)
    base_size = [c for c in base if c.get("type") == "textbox" and "▸" in c["props"]["text"]][0]["props"]["size"]
    fix_size = [c for c in fixed if c.get("type") == "textbox" and "▸" in c["props"]["text"]][0]["props"]["size"]
    assert fix_size < base_size
    assert not [c for c in fixed if c.get("type") == "picture"]


def test_layout_theme_override_changes_colors():
    cmds = _commands({"type": "content", "title": "t", "bullets": ["a"]},
                     theme={"primary": "#123456", "accent": "#ABCDEF", "font": "宋体"})
    bar = [c for c in cmds if c.get("type") == "shape"][0]
    assert bar["props"]["fill"] == "#123456"
    title_box = [c for c in cmds if c.get("type") == "textbox"][0]
    assert title_box["props"]["font"] == "宋体"


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
def test_alloc_budget_matches_target():
    """页数预算必须能凑够目标页数（允许 1 页误差：内容不足以再拆时）。"""
    for target in (3, 6, 8, 10, 14, 20, 30):
        sections, content = ppt_agent._alloc(target, has_refs=True)
        total = 3 + 1 + sections + content
        assert total >= target - 1, (target, sections, content, total)
        assert content >= 1
        if target <= 7:
            assert sections == 0          # 小页数不排章节页，页数留给内容
        else:
            assert sections >= 1


def test_rebalance_trims_and_splits():
    plan = {"sections": [{"name": "A", "pages": [
        {"title": "p1", "bullets": ["1", "2", "3", "4", "5", "6"]},
        {"title": "p2", "bullets": ["a", "b"]},
        {"title": "p3", "bullets": ["x", "y"]},
    ]}]}
    trimmed = ppt_agent._rebalance(json.loads(json.dumps(plan)), 2)
    assert sum(len(s["pages"]) for s in trimmed["sections"]) == 2

    grown = ppt_agent._rebalance(json.loads(json.dumps(plan)), 4)
    pages = [p for s in grown["sections"] for p in s["pages"]]
    assert len(pages) == 4                                # 3 页拆 1 次 → 4 页
    assert all(p.get("bullets") for p in pages)           # 拆页不产生空页
    assert any(str(p["title"]).endswith("（续）") for p in pages)
    # 内容不足以继续拆时允许欠页（不编造内容）
    capped = ppt_agent._rebalance(json.loads(json.dumps(plan)), 9)
    assert sum(len(s["pages"]) for s in capped["sections"]) == 4


def test_build_pages_order_and_refs_presence():
    plan = {
        "title": "高处作业", "subtitle": "培训",
        "sections": [
            {"name": "风险", "pages": [{"title": "三大风险", "bullets": ["a"]}]},
            {"name": "做法", "pages": [{"title": "四项确认", "bullets": ["b", "c"]}]},
        ],
    }
    pages = ppt_agent.build_pages({"pages": 10}, plan, has_refs=True)
    kinds = [p["type"] for p in pages]
    assert kinds[0] == "cover" and kinds[1] == "toc"
    assert kinds[-1] == "end" and kinds[-2] == "refs"
    assert kinds.count("section") == 2 and kinds.count("content") == 2

    no_refs = ppt_agent.build_pages({"pages": 10}, plan, has_refs=False)
    assert "refs" not in [p["type"] for p in no_refs]

    # 小页数（≤7）：不插章节页，页数留给内容
    small = ppt_agent.build_pages({"pages": 6}, plan, has_refs=False)
    assert "section" not in [p["type"] for p in small]
    assert [p["type"] for p in small][:2] == ["cover", "toc"] and small[-1]["type"] == "end"


def test_plan_falls_back_when_llm_fails(monkeypatch):
    class _Boom:
        def chat(self, *a, **kw):
            raise RuntimeError("llm down")

    monkeypatch.setattr("src.ai.llm_client.llm_client", _Boom())
    plan = ppt_agent.plan_outline({"topic": "临时用电", "pages": 8}, "用电安全要求。\n\n配电箱必须上锁。")
    assert plan["sections"]
    assert plan["source"].startswith("fallback")


def test_next_fix_escalates_then_stops():
    verdict = {"ok": False, "problems": [{"type": "overflow", "detail": "太多"}], "fix_hints": []}
    f0 = {}
    f1 = ppt_agent._next_fix(f0, verdict)
    assert f1["font_delta"] == -2 and f1["max_bullets"] == ppt_layout.MAX_BULLETS
    f2 = ppt_agent._next_fix(f1, verdict)
    assert f2["font_delta"] == -4 and f2["max_bullets"] == 4
    # overlap → 去配图
    f3 = ppt_agent._next_fix(f0, {"problems": [{"type": "overlap"}]})
    assert f3["drop_image"] is True


def test_read_template_theme_from_real_pptx(tmp_path):
    from pptx import Presentation

    deck = tmp_path / "tpl.pptx"
    prs = Presentation()
    prs.save(str(deck))
    theme = ppt_agent.read_template_theme(deck)
    assert theme is not None and theme["primary"].startswith("#")
    assert len(theme["primary"]) == 7 and len(theme["accent"]) == 7
    assert ppt_agent.read_template_theme(tmp_path / "missing.pptx") is None


def test_version_theme_zip_is_read():
    """主题色必须来自模板的 theme1.xml（不是写死）。"""
    from pptx import Presentation

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.pptx"
        Presentation().save(str(p))
        with zipfile.ZipFile(p) as zf:
            xml = zf.read("ppt/theme/theme1.xml").decode("utf-8", "ignore")
    assert "srgbClr" in xml


# --------------------------------------------------------------------------- #
# 配图（Openverse）—— 用假传输层验证真实下载/落盘/署名链路
# --------------------------------------------------------------------------- #
def test_search_images_downloads_and_records_sources(tmp_path, monkeypatch):
    """Openverse 通道（备选源）：字段映射 + 落盘 + 署名。"""
    import httpx

    from src.ai import ppt_tools

    monkeypatch.setattr(ppt_tools.settings, "PPT_IMAGE_PROVIDER", "openverse")

    png = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    def handler(request: httpx.Request) -> httpx.Response:
        if "openverse" in str(request.url):
            return httpx.Response(200, json={"results": [{
                "url": "https://cdn.example.com/a.jpg",
                "title": "Tunnel",
                "license": "by",
                "license_version": "4.0",
                "creator": "Alice",
                "attribution": "Tunnel by Alice, CC BY 4.0",
                "foreign_landing_url": "https://example.com/a",
            }]})
        return httpx.Response(200, content=png, headers={"content-type": "image/jpeg"})

    monkeypatch.setattr(ppt_tools, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    items = ppt_tools.search_images("tunnel construction", 1, tmp_path)
    assert len(items) == 1
    item = items[0]
    assert Path(item["path"]).is_file() and Path(item["path"]).read_bytes() == png
    assert item["license"] == "by" and item["creator"] == "Alice"

    src = ppt_tools.write_sources(tmp_path, items)
    data = json.loads(src.read_text(encoding="utf-8"))
    assert data[0]["license"] == "by" and data[0]["source_url"] == "https://example.com/a"
    assert item["provider"] == "openverse"


def test_search_images_degrades_on_timeout(tmp_path, monkeypatch):
    import httpx

    from src.ai import ppt_tools

    monkeypatch.setattr(ppt_tools.settings, "PPT_IMAGE_PROVIDER", "openverse")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    monkeypatch.setattr(ppt_tools, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    assert ppt_tools.search_images("tunnel", 2, tmp_path) == []      # 不抛异常，交给版式降级
    assert not [p for p in tmp_path.glob("*.jpg")]


def test_unsplash_maps_fields_and_tracks_download(tmp_path, monkeypatch):
    """Unsplash 是默认源：字段映射（授权/署名/来源）+ 下载事件上报。"""
    import httpx

    from src.ai import ppt_tools

    monkeypatch.setattr(ppt_tools.settings, "PPT_IMAGE_PROVIDER", "unsplash")
    monkeypatch.setattr(ppt_tools.settings, "UNSPLASH_ACCESS_KEY", "test-key")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "api.unsplash.com/search" in url:
            return httpx.Response(200, headers={"x-ratelimit-remaining": "42"}, json={"results": [{
                "id": "abc",
                "alt_description": "workers on scaffolding",
                "width": 4032, "height": 2268,
                "urls": {"regular": "https://images.unsplash.com/photo-1.jpg"},
                "user": {"name": "Jane Doe"},
                "links": {"html": "https://unsplash.com/photos/abc",
                          "download_location": "https://api.unsplash.com/photos/abc/download"},
            }]})
        if url.endswith("/download"):
            return httpx.Response(200, json={"url": "https://images.unsplash.com/photo-1.jpg"})
        return httpx.Response(200, content=b"IMG" * 10, headers={"content-type": "image/jpeg"})

    monkeypatch.setattr(ppt_tools, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    items = ppt_tools.search_images("scaffolding safety", 1, tmp_path)
    assert len(items) == 1
    item = items[0]
    assert item["provider"] == "unsplash"
    assert item["license"] == "unsplash license"
    assert "Jane Doe" in item["attribution"] and "Unsplash" in item["attribution"]
    assert item["source_url"] == "https://unsplash.com/photos/abc"
    assert Path(item["path"]).read_bytes() == b"IMG" * 10
    assert any(c.endswith("/download") for c in calls)      # download 事件已上报


def test_unsplash_rate_limited_degrades(tmp_path, monkeypatch):
    import httpx

    from src.ai import ppt_tools

    monkeypatch.setattr(ppt_tools.settings, "PPT_IMAGE_PROVIDER", "unsplash")
    monkeypatch.setattr(ppt_tools.settings, "UNSPLASH_ACCESS_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"errors": ["Rate Limit Exceeded"]})

    monkeypatch.setattr(ppt_tools, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    assert ppt_tools.search_images("tunnel", 2, tmp_path) == []


def test_provider_falls_back_when_unsplash_key_missing(monkeypatch):
    from src.ai import ppt_tools

    monkeypatch.setattr(ppt_tools.settings, "PPT_IMAGE_PROVIDER", "unsplash")
    monkeypatch.setattr(ppt_tools.settings, "UNSPLASH_ACCESS_KEY", "")
    assert ppt_tools.current_provider() == "openverse"
    monkeypatch.setattr(ppt_tools.settings, "UNSPLASH_ACCESS_KEY", "k")
    assert ppt_tools.current_provider() == "unsplash"


def test_unsplash_two_queries_do_not_overwrite(tmp_path, monkeypatch):
    """两次检索必须落成两个文件（早前 img01_0.jpg 会互相覆盖，来源页三条指向同一张图）。"""
    import httpx

    from src.ai import ppt_tools

    monkeypatch.setattr(ppt_tools.settings, "PPT_IMAGE_PROVIDER", "unsplash")
    monkeypatch.setattr(ppt_tools.settings, "UNSPLASH_ACCESS_KEY", "test-key")
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "api.unsplash.com/search" in str(request.url):
            counter["n"] += 1
            return httpx.Response(200, json={"results": [{
                "id": f"p{counter['n']}", "alt_description": "x",
                "width": 1600, "height": 900,
                "urls": {"regular": f"https://images.unsplash.com/p{counter['n']}.jpg"},
                "user": {"name": "A"}, "links": {"html": "https://unsplash.com/p"},
            }]})
        return httpx.Response(200, content=b"P" * 32, headers={"content-type": "image/jpeg"})

    monkeypatch.setattr(ppt_tools, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    a = ppt_tools.search_images("helmet", 1, tmp_path)
    b = ppt_tools.search_images("scaffold", 1, tmp_path)
    assert len(a) == 1 and len(b) == 1
    assert a[0]["file"] != b[0]["file"]
    assert len(list(tmp_path.glob("img*"))) == 2
    assert a[0]["width"] == 1600 and a[0]["height"] == 900


def test_layout_keeps_aspect_ratio():
    """16:9 的图不能被压进 4.5×3.4 的框里（validate 会报 picture_aspect_distorted）。"""
    wide = ppt_layout.build(3, {"type": "content", "title": "t", "bullets": ["a"]}, 3,
                            image={"path": "x.jpg", "width": 1600, "height": 900})
    props = [c for c in wide if c.get("type") == "picture"][0]["props"]
    assert props["width"] == "4.50in" and props["height"] == "2.53in"

    tall = ppt_layout.build(3, {"type": "content", "title": "t", "bullets": ["a"]}, 3,
                            image={"path": "y.jpg", "width": 900, "height": 1600})
    props2 = [c for c in tall if c.get("type") == "picture"][0]["props"]
    assert props2["height"] == "3.40in" and props2["width"] == "1.91in"
    # 居中：不能贴左边
    assert float(props2["x"].rstrip("in")) > 8.15


def test_layout_with_image_renders_picture_and_attribution():
    """图注必须"摄影师全名 + on Unsplash"，且带可点击链接（Unsplash 合规硬要求）。"""
    image = {"path": "img01.jpg", "file": "img01.jpg", "creator": "Alice",
             "attribution": "Photo by Alice on Unsplash",
             "photo_url": "https://unsplash.com/photos/x?utm_source=shudao_safety&utm_medium=referral"}
    cmds = ppt_layout.build(3, {"type": "content", "title": "配图页",
                                "bullets": ["a", "b", "c"]}, 3, image=image)
    pics = [c for c in cmds if c.get("type") == "picture"]
    assert len(pics) == 1 and pics[0]["props"]["src"] == "img01.jpg"

    caps = [c for c in cmds if c.get("_link")]
    assert len(caps) == 1
    assert "Photo by Alice" in caps[0]["props"]["text"]
    assert "on Unsplash" in caps[0]["props"]["text"]
    assert caps[0]["_link"].startswith("https://unsplash.com/photos/x")
    assert "utm_source=" in caps[0]["_link"]       # 回链必须带 utm


def test_refs_page_lists_licenses():
    cmds = ppt_layout.build(2, {"type": "refs", "sources": [
        {"creator": "Alice", "license": "by", "license_version": "4.0",
         "source_url": "https://example.com/a", "photo_url": "https://example.com/a"},
    ]}, 2)
    body = "".join(c["props"]["text"] for c in cmds if c.get("type") == "textbox")
    assert "Alice" in body and "by 4.0" in body
    # 每条署名都要可点击（合规）
    linked = [c for c in cmds if c.get("_link")]
    assert len(linked) == 1 and linked[0]["_link"] == "https://example.com/a"
    assert "on Unsplash" in linked[0]["props"]["text"]


def test_apply_commands_parses_paths(monkeypatch):
    """batch 回执要能解析出每个元素的真实路径（挂超链接靠它）。"""
    from src.ai import ppt_tools

    monkeypatch.setattr(ppt_tools, "_oci", lambda *a, **kw: (
        0,
        "[1] Added slide at /slide[1]\n"
        "[2] Added textbox at /slide[1]/shape[@id=100000]\n"
        "[3] ERROR: boom\n"
        "\nBatch complete: 2 succeeded, 1 failed, 3 total",
        "",
    ))
    receipts = ppt_tools.apply_commands("x.pptx", [{"command": "add"}, {"command": "add"}, {"command": "add"}])
    assert len(receipts) == 3
    assert receipts[0]["path"] == "/slide[1]" and receipts[0]["ok"] is True
    assert receipts[1]["path"] == "/slide[1]/shape[@id=100000]"
    assert receipts[2]["ok"] is False


def test_apply_commands_strips_meta_fields(monkeypatch):
    """`_link/_tooltip` 是本地元数据，officecli 会报 unknown field → 下发前必须剥掉。"""
    from src.ai import ppt_tools

    captured: dict = {}

    def fake_oci(*args, **kwargs):
        tmp = args[args.index("--input") + 1]
        captured["cmds"] = json.loads(Path(tmp).read_text(encoding="utf-8"))
        return (0, "[1] Added textbox at /slide[1]/shape[@id=1]", "")

    monkeypatch.setattr(ppt_tools, "_oci", fake_oci)
    ppt_tools.apply_commands("x.pptx", [
        {"command": "add", "type": "textbox", "props": {"text": "a"},
         "_link": "https://unsplash.com/x", "_tooltip": "原图"},
    ])
    assert captured["cmds"][0] == {"command": "add", "type": "textbox", "props": {"text": "a"}}


def test_apply_hyperlinks_builds_batch(monkeypatch):
    from src.ai import ppt_tools

    captured: list[list[dict]] = []

    def fake_apply(path, commands):
        captured.append(commands)
        return [{"index": i, "ok": True, "path": None, "line": ""} for i, _ in enumerate(commands)]

    monkeypatch.setattr(ppt_tools, "apply_commands", fake_apply)
    n = ppt_tools.apply_hyperlinks("x.pptx", [
        ("/slide[3]/shape[@id=1]", "https://unsplash.com/photos/a", "原图"),
        ("/slide[3]/shape[@id=2]", "https://unsplash.com/photos/b", "原图"),
    ])
    assert n == 2
    assert captured[0][0]["type"] == "hyperlink"
    assert captured[0][0]["props"]["link"].endswith("/photos/a")
    assert captured[0][0]["parent"] == "/slide[3]/shape[@id=1]"


# --------------------------------------------------------------------------- #
# 进度与锁
# --------------------------------------------------------------------------- #
class _FakeRedis:
    def __init__(self):
        self.h = {}
        self.kv = {}

    def hset(self, key, mapping):
        self.h.setdefault(key, {}).update(mapping)
        return 1

    def expire(self, key, ttl):
        return True

    def hgetall(self, key):
        return self.h.get(key, {})

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def delete(self, key):
        self.kv.pop(key, None)
        self.h.pop(key, None)
        return 1

    def exists(self, key):
        return int(key in self.kv)


def test_progress_roundtrip(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ppt_progress, "redis_client", fake)
    assert ppt_progress.set_progress(123, {"stage": "S6", "progress": 62, "page": 3,
                                           "page_total": 10, "msg": "第 3 页"})
    data = ppt_progress.get_progress(123)
    assert data["stage"] == "S6" and data["progress"] == 62 and data["page"] == 3
    assert data["status"] == "running"
    ppt_progress.set_progress(123, {"done": True, "progress": 100})
    assert ppt_progress.get_progress(123)["status"] == "done"
    ppt_progress.clear_progress(123)
    assert ppt_progress.get_progress(123) is None


def test_progress_and_lock_degrade_when_redis_down(monkeypatch):
    class _Down:
        def __getattr__(self, name):
            raise ConnectionError("redis down")

    monkeypatch.setattr(ppt_progress, "redis_client", _Down())
    assert ppt_progress.set_progress(9, {"stage": "S0"}) is False
    assert ppt_progress.get_progress(9) is None
    assert ppt_progress.acquire_lock(9) is True      # 降级放行，不阻塞生成
    assert ppt_progress.lock_held(9) is False


def test_lock_is_exclusive(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ppt_progress, "redis_client", fake)
    assert ppt_progress.acquire_lock(55) is True
    assert ppt_progress.acquire_lock(55) is False
    ppt_progress.release_lock(55)
    assert ppt_progress.acquire_lock(55) is True


# --------------------------------------------------------------------------- #
# 下载与服务
# --------------------------------------------------------------------------- #
def _task(uid, tid, topic, file_path, status="已完成"):
    return types.SimpleNamespace(
        id=tid, user_id=uid, topic=topic, status=status, file_path=file_path,
        template="builtin_simple", outline=None, created_at=datetime(2026, 10, 24, 9, 0, 0),
    )


class _FakeDB:
    def __init__(self, tasks):
        self._tasks = tasks

    def get(self, model, pk):  # noqa: ARG002
        return self._tasks.get(pk)


def test_download_prefers_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(ppt_paths, "SANDBOX_ROOT", tmp_path)
    out = ppt_paths.out_path(UID, 12, "高处作业")
    ppt_paths.ensure_dir(out.parent)
    out.write_bytes(b"pptx")
    db = _FakeDB({12: _task(UID, 12, "高处作业", str(out))})
    path, name = PptService.download_path(db, UID, 12)
    assert path == out.resolve() and name == "高处作业.pptx"


def test_download_rejects_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(ppt_paths, "SANDBOX_ROOT", tmp_path / "sandbox")
    secret = tmp_path / "secret.pptx"
    secret.write_bytes(b"nope")
    db = _FakeDB({13: _task(UID, 13, "越权", str(secret))})
    with pytest.raises(HTTPException) as exc:
        PptService.download_path(db, UID, 13)
    assert exc.value.status_code == 404


def test_download_other_account_forbidden():
    db = _FakeDB({14: _task(UID, 14, "x", "ppt_files/x.pptx")})
    with pytest.raises(HTTPException) as exc:
        PptService.download_path(db, OTHER, 14)
    assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_capabilities_requires_auth(client):
    assert client.get("/api/ppt/capabilities").status_code == 401


def test_capabilities_shape(client):
    r = client.get("/api/ppt/capabilities", headers=_auth())
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ("agent", "ppt_llm", "officecli", "vision", "images", "page_limit"):
        assert key in data
    assert data["ppt_llm"] is False          # 当前无 provider（百度已移除）


def test_assets_reject_traversal(client):
    r = client.get(f"/api/ppt/1/assets/shots/..%2F..%2F..%2F.env", headers=_auth())
    assert r.status_code in (400, 404), r.text


def test_assets_refuse_raw_images(client):
    """合规：不复用自家服务器托管 Unsplash 原图 → images 资产端点已关闭。"""
    r = client.get("/api/ppt/1/assets/images/img01_x.jpg", headers=_auth())
    assert r.status_code in (401, 403, 404), r.text


@pytest.mark.parametrize("path", [
    "/api/ppt/5/progress",
    "/api/ppt/5/workspace",
    "/api/ppt/5/sources",
    "/api/ppt/5/assets/shots/page-01.png",
])
def test_task_scoped_endpoints_are_owner_only(client, monkeypatch, path):
    """隔离回归：进度/工作区/来源/资源都必须是"只有本人"（这个 bug 的接口侧底线）。"""
    def _deny(db, user_id, task_id):
        raise HTTPException(status_code=403, detail="无权操作该任务")

    # 统一从归属校验入口拦：get_task / workspace / sources / assets / progress 都经过它
    monkeypatch.setattr(PptService, "_get_owned", staticmethod(_deny))
    r = client.get(path, headers=_auth(OTHER))
    assert r.status_code == 403, r.text


def test_sources_endpoint_requires_auth(client):
    assert client.get("/api/ppt/1/sources").status_code == 401


def test_templates_list_has_single_builtin(client):
    r = client.get("/api/ppt/templates", headers=_auth())
    assert r.status_code == 200
    builtin = [t for t in r.json()["items"] if t.get("builtin")]
    assert [t["id"] for t in builtin] == ["builtin_simple"]
