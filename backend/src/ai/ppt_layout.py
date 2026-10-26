"""PPT 版式渲染（v2.0 大点 02）：把"页面规格"翻译成 OfficeCLI batch 命令。

分工（裁定 3）：**Agent 决定这一页写什么**（标题/要点/用哪张图/用哪种版式），
**代码决定几何**（坐标、字号、配色）。这样版式稳定可复现，模型不需要吐坐标。

所有命令都是 officecli 的 `add`（`/slide[N]/shape|picture`），目标文件由调用方创建。
版面固定 16:9（13.333in × 7.5in）。配色/字体可由主题覆盖（用户上传模板时从
`ppt/theme/theme1.xml` 读出主色/强调色，见 `ppt_agent.read_template_theme`）。

修掉的老问题（裁定 11）：
- 要点不再 `points[:5]` 静默截断 —— 5 条以上自动双栏，超过 MAX_BULLETS 才截断（调用方记日志）；
- 目录页按容量分两栏，6 个以上章节不再溢出页外；
- 支持 `_fix`（自检修补）：缩字号 / 减要点 / 去配图，用于审图不通过时重建本页。
"""
from __future__ import annotations

from src.core.paths import safe_name

IN = 914400
W_IN, H_IN = 13.333, 7.5
WHITE = "#FFFFFF"
GRAY = "#595959"
LIGHT_BG = "#F5F7FA"
FONT = "微软雅黑"

#: 默认主题（深蓝 + 金）；用户模板提供主色时覆盖 primary/accent
DEFAULT_THEME: dict = {
    "primary": "#0E2A47",
    "text": "#1F3864",
    "accent": "#C9A227",
    "font": FONT,
}

MAX_BULLETS = 10        # 单页要点上限（超过才截断，调用方记日志）
TOC_PER_COLUMN = 5      # 目录每栏最多 5 个章节


def _theme(t: dict | None) -> dict:
    out = dict(DEFAULT_THEME)
    if t:
        out.update({k: v for k, v in t.items() if v})
    return out


def _tb(text: str, x: float, y: float, w: float, h: float, *, size: int = 20,
        color: str | None = None, bold: bool = False, align: str = "left",
        valign: str = "top", autofit: str | None = None, font: str = FONT) -> dict:
    props: dict = {
        "text": text, "x": f"{x}in", "y": f"{y}in", "width": f"{w}in", "height": f"{h}in",
        "size": max(8, int(size)), "bold": "true" if bold else "false",
        "color": color or "#000000", "align": align, "valign": valign, "font": font,
    }
    if autofit:
        props["autoFit"] = autofit
    return {"command": "add", "type": "textbox", "props": props}


def _rect(x: float, y: float, w: float, h: float, fill: str) -> dict:
    return {
        "command": "add", "type": "shape",
        "props": {"geometry": "rect", "fill": fill, "line": "none",
                  "x": f"{x}in", "y": f"{y}in", "width": f"{w}in", "height": f"{h}in"},
    }


def _bullets_text(bullets: list[str]) -> str:
    return "\n".join(f"▸ {b}" for b in bullets)


#: 兼容别名（content() 里用短名）
_bullets = _bullets_text


def _fix_of(spec: dict) -> dict:
    fix = spec.get("_fix") or {}
    return {
        "font_delta": int(fix.get("font_delta") or 0),
        "max_bullets": int(fix.get("max_bullets") or MAX_BULLETS),
        "drop_image": bool(fix.get("drop_image")),
    }


def _fit_box(bx: float, by: float, bw: float, bh: float,
             w: int | None, h: int | None) -> tuple[float, float, float, float]:
    """把图片按原始宽高等比缩放塞进 (bx, by, bw, bh) 并居中。

    早前固定 4.5×3.4in 会把 16:9 的照片压扁（officecli validate 会报 picture_aspect_distorted）。
    """
    if not w or not h:
        return bx, by, bw, bh
    ar = float(w) / float(h)
    if bw / bh > ar:              # 框比图更宽 → 以高为准
        nh, nw = bh, bh * ar
    else:
        nw, nh = bw, bw / ar
    return bx + (bw - nw) / 2, by + (bh - nh) / 2, nw, nh


def _header(title: str, t: dict) -> list[dict]:
    return [
        _rect(0, 0, W_IN, 1.0, t["primary"]),
        _rect(0, 1.0, W_IN, 0.06, t["accent"]),
        _tb(title[:40], 0.6, 0.16, 11.6, 0.7, size=26, color=WHITE, bold=True,
            valign="middle", font=t["font"]),
    ]


def _page_no(idx: int) -> dict:
    return _tb(str(idx), 12.0, 7.0, 0.9, 0.35, size=12, color=GRAY, align="right")


# --------------------------------------------------------------------------- #
# 各类页面
# --------------------------------------------------------------------------- #
def cover(slide: int, spec: dict, idx: int, t: dict) -> list[dict]:
    title = (spec.get("title") or "").strip()
    subtitle = (spec.get("subtitle") or "安全培训课件").strip()
    return [
        {"command": "add", "type": "slide", "parent": "/", "props": {"background": t["primary"]}},
        _rect(0, 6.95, W_IN, 0.55, t["accent"]),
        _rect(0.9, 2.05, 0.09, 1.6, t["accent"]),
        _tb(title[:30], 1.15, 2.0, 11.0, 1.5, size=44, color=WHITE, bold=True, font=t["font"]),
        _tb(f"—— {subtitle} ——", 1.15, 3.65, 11.0, 0.6, size=20, color=t["accent"], font=t["font"]),
        _tb("蜀道集团安全管理部 ｜ 安全培训课件", 1.15, 6.25, 8.5, 0.45, size=14,
            color=WHITE, font=t["font"]),
    ]


def toc(slide: int, spec: dict, idx: int, t: dict) -> list[dict]:
    items = [str(s.get("name") or "").strip() for s in (spec.get("sections") or []) if s.get("name")]
    items = items[: TOC_PER_COLUMN * 2]
    cmds = [{"command": "add", "type": "slide", "parent": "/", "props": {"background": WHITE}}]
    cmds += _header("目 录  CONTENTS", t)
    for i, name in enumerate(items):
        col, row = divmod(i, TOC_PER_COLUMN)
        x = 1.2 + col * 5.9
        y = 1.55 + row * 1.02
        cmds.append(_rect(x, y, 0.72, 0.72, t["primary"] if i % 2 == 0 else t["accent"]))
        cmds.append(_tb(f"{i + 1:02d}", x, y, 0.72, 0.72, size=22, color=WHITE, bold=True,
                        align="center", valign="middle", font=t["font"]))
        cmds.append(_tb(name[:22], x + 0.95, y, 4.3, 0.72, size=20, color=t["primary"],
                        valign="middle", font=t["font"]))
    cmds.append(_page_no(slide))
    return cmds


def section(slide: int, spec: dict, idx: int, t: dict) -> list[dict]:
    num = f"{int(spec.get('section_no') or idx):02d}"
    name = (spec.get("title") or "").strip()
    return [
        {"command": "add", "type": "slide", "parent": "/", "props": {"background": t["primary"]}},
        _rect(0, 6.95, W_IN, 0.55, t["accent"]),
        # 96pt 实测需要 115pt 行高（约 2.05in），框给 2.1in 否则 format 检查报 text overflow
        _tb(num, 1.0, 1.95, 3.0, 2.1, size=96, color=t["accent"], bold=True, font=t["font"]),
        _tb(name[:26], 1.0, 4.15, 11.0, 0.9, size=36, color=WHITE, bold=True, font=t["font"]),
    ]


def content(slide: int, spec: dict, idx: int, t: dict, image: dict | None = None) -> list[dict]:
    fix = _fix_of(spec)
    bullets = [str(b).strip() for b in (spec.get("bullets") or []) if str(b).strip()]
    bullets = bullets[: fix["max_bullets"]]
    if fix["drop_image"]:
        image = None

    cmds = [{"command": "add", "type": "slide", "parent": "/", "props": {"background": WHITE}}]
    cmds += _header((spec.get("title") or "").strip(), t)
    cmds.append(_rect(0.62, 1.45, 0.08, 5.2, t["accent"]))

    body_size = 20 + fix["font_delta"]

    if image:
        px, py, pw, ph = _fit_box(8.15, 1.6, 4.5, 3.4, image.get("width"), image.get("height"))
        cmds.append({
            "command": "add", "type": "picture",
            "props": {"src": image["path"], "x": f"{px:.2f}in", "y": f"{py:.2f}in",
                      "width": f"{pw:.2f}in", "height": f"{ph:.2f}in"},
        })
        if image.get("creator"):
            # 合规：摄影师全名 + Unsplash 都要署名且可点击（锚到原图页，页面内含摄影师主页链接）
            cap = _tb(f"图：Photo by {image['creator'][:40]} on Unsplash",
                      8.15, 5.15, 4.5, 0.5, size=9, color=GRAY, autofit="shrink", font=t["font"])
            if image.get("photo_url"):
                cap["_link"] = image["photo_url"]
                cap["_tooltip"] = "在 Unsplash 查看原图与摄影师"
            cmds.append(cap)
        cmds.append(_tb(_bullets(bullets), 0.95, 1.55, 7.0, 5.0, size=body_size,
                        color=t["text"], autofit="shrink", font=t["font"],
                        valign="middle" if len(bullets) <= 4 else "top"))
    elif len(bullets) > 5:            # 双栏（治"要点太多被静默截断"）
        half = (len(bullets) + 1) // 2
        cmds.append(_tb(_bullets(bullets[:half]), 0.95, 1.55, 5.6, 5.2,
                        size=body_size - 2, color=t["text"], autofit="shrink", font=t["font"]))
        cmds.append(_tb(_bullets(bullets[half:]), 6.85, 1.55, 5.6, 5.2,
                        size=body_size - 2, color=t["text"], autofit="shrink", font=t["font"]))
    else:
        cmds.append(_tb(_bullets(bullets), 0.95, 1.55, 11.3, 5.2, size=body_size,
                        color=t["text"], autofit="shrink", font=t["font"],
                        valign="middle" if len(bullets) <= 6 else "top"))

    cmds.append(_page_no(slide))
    return cmds


def refs(slide: int, spec: dict, idx: int, t: dict) -> list[dict]:
    """图片来源与授权页：每条都带摄影师全名 + Unsplash + 可点击的原图链接（合规必需）。"""
    sources = spec.get("sources") or []
    cmds = [{"command": "add", "type": "slide", "parent": "/", "props": {"background": LIGHT_BG}}]
    cmds += _header("图片来源与授权", t)
    if not sources:
        cmds.append(_tb("本课件未使用外部图片（版式为纯图形）。", 0.95, 1.55, 11.4, 1.0,
                        size=14, color=t["text"], font=t["font"]))
        cmds.append(_page_no(slide))
        return cmds

    for i, it in enumerate(sources[:10]):
        y = 1.55 + i * 0.62
        who = (it.get("creator") or "Unsplash 摄影师").strip()
        lic = f"{it.get('license', '')} {it.get('license_version', '')}".strip() or "Unsplash License"
        line = _tb(f"{i + 1}. Photo by {who[:36]} on Unsplash ｜ {lic}",
                   0.95, y, 11.4, 0.5, size=14, color=t["text"], autofit="shrink", font=t["font"])
        if it.get("photo_url"):
            line["_link"] = it["photo_url"]
            line["_tooltip"] = "在 Unsplash 查看原图与摄影师"
        cmds.append(line)
    cmds.append(_tb("以上照片来自 Unsplash，遵循 Unsplash License；摄影师与来源可点击查看。",
                    0.95, 7.0, 11.4, 0.4, size=10, color=GRAY, font=t["font"]))
    cmds.append(_page_no(slide))
    return cmds


def end(slide: int, spec: dict, idx: int, t: dict) -> list[dict]:
    title = (spec.get("title") or "").strip()
    return [
        {"command": "add", "type": "slide", "parent": "/", "props": {"background": t["primary"]}},
        _rect(0, 6.95, W_IN, 0.55, t["accent"]),
        _tb("高高兴兴上班来，平平安安回家去", 1.0, 2.5, 11.3, 1.1, size=38, color=WHITE,
            bold=True, align="center", font=t["font"]),
        _tb(f"《{safe_name(title)[:30]}》· 感谢聆听", 1.0, 3.85, 11.3, 0.6, size=20,
            color=t["accent"], align="center", font=t["font"]),
        _tb("蜀道集团安全管理部 ｜ 安全培训课件", 1.0, 6.3, 11.3, 0.45, size=13,
            color=WHITE, align="center", font=t["font"]),
    ]


def build(slide_no: int, spec: dict, idx: int, image: dict | None = None,
          theme: dict | None = None) -> list[dict]:
    """按页面类型分发；未知类型按 content 处理。

    所有元素命令统一补 `parent=/slide[N]`（officecli 的 `add` 必须带 parent），
    只有"加幻灯片"那一条的 parent 是 `/`。
    """
    t = _theme(theme)
    kind = (spec.get("type") or "content").lower()
    if kind == "cover":
        cmds = cover(slide_no, spec, idx, t)
    elif kind == "toc":
        cmds = toc(slide_no, spec, idx, t)
    elif kind == "section":
        cmds = section(slide_no, spec, idx, t)
    elif kind == "refs":
        cmds = refs(slide_no, spec, idx, t)
    elif kind == "end":
        cmds = end(slide_no, spec, idx, t)
    else:
        cmds = content(slide_no, spec, idx, t, image=image)

    for c in cmds:
        if c.get("type") != "slide" and not c.get("parent"):
            c["parent"] = f"/slide[{slide_no}]"
    return cmds
