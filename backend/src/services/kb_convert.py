"""贡献文件 → Markdown 转换（v2.0 大点 04 · 裁定 4/5）。

入口：`to_markdown(path, doc_type="laws") -> {md, converted_by, warnings, title}`

路径选择（裁定 4）：
- `.md/.markdown`：直通（去 front-matter、补首行 `# 标题`）
- `.txt`         ：内置（补首行标题）
- `.html/.htm`   ：`markdownify`（先删 script/style/title/nav/footer）
- `.docx/.pdf`   ：**MinerU CLI 优先**（docx 走 flash 原生解析、pdf 带 `--pages all`），失败/超时/未安装
                   → 内置兜底（docx：段落+表格交错；pdf：字号分级 + find_tables）

约束（裁定 5）：
- MinerU 只以**子进程**调用（`uv tool install mineru` 独立环境），绝不 import；
- `KB_MINERU_CMD` / `KB_MINERU_TIMEOUT` / `KB_MINERU_TIER` / `KB_MINERU_ARGS` 可配；
- 不产生副作用：只读输入文件、写调用方给的临时目录。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_EXTS = {".md", ".markdown", ".txt", ".docx", ".pdf", ".html", ".htm"}
_FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
_IMG_LINK = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HELP_CACHE: str | None = None


# --------------------------------------------------------------------------- #
# 公共入口
# --------------------------------------------------------------------------- #
def to_markdown(path: str | Path, doc_type: str = "laws", title: str | None = None) -> dict:
    """把贡献文件转成 markdown。

    返回 {md, converted_by, warnings, title}
    converted_by ∈ {direct, builtin, mineru, fallback}
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"不支持的格式: {ext or '无扩展名'}")

    warnings: list[str] = []
    if ext in (".md", ".markdown"):
        md, by = _from_md(p), "direct"
    elif ext == ".txt":
        md, by = _from_text(p), "builtin"
    elif ext in (".html", ".htm"):
        md, by = _from_html(p), "builtin"
    else:  # docx / pdf
        md = _from_mineru(p, warnings)
        by = "mineru"
        if not md:
            md = _from_docx(p) if ext == ".docx" else _from_pdf(p)
            by = "fallback"
            warnings.append("MinerU 不可用或转换失败，已使用内置兜底（表格可能不完整）")
        elif len(md.strip()) < 200:
            # MinerU 偶发只吐标题/乱码（如字体未嵌入的 PDF）→ 用兜底结果对比，取更完整的
            alt = _from_docx(p) if ext == ".docx" else _from_pdf(p)
            if len(alt.strip()) > len(md.strip()) * 2:
                warnings.append("MinerU 结果异常偏短，已改用内置兜底")
                md, by = alt, "fallback"

    md = _normalize(md, title or _guess_title(p))
    if not md.strip():
        raise ValueError("转换结果为空（可能是扫描件/加密文件）")
    return {"md": md, "converted_by": by, "warnings": warnings, "title": title or _guess_title(p)}


def _guess_title(p: Path) -> str:
    return p.stem.replace("_", " ").strip()[:120] or "未命名文档"


def _normalize(md: str, title: str) -> str:
    """去 front-matter、去图片引用、保证首行是 `# 标题`、压缩多余空行。"""
    text = _FRONT_MATTER.sub("", (md or "").lstrip("\ufeff")).strip()
    text = _IMG_LINK.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text.startswith("#"):
        text = f"# {title}\n\n{text}" if text else f"# {title}"
    return text + "\n"


def _title_from_md(md: str) -> str | None:
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:120]
        if s:
            return None
    return None


# --------------------------------------------------------------------------- #
# 各格式内置实现
# --------------------------------------------------------------------------- #
def _read_text(p: Path) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="utf-8", errors="ignore")


def _from_md(p: Path) -> str:
    return _read_text(p)


def _from_text(p: Path) -> str:
    body = _read_text(p)
    return f"# {_guess_title(p)}\n\n{body}"


def _from_html(p: Path) -> str:
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    soup = BeautifulSoup(_read_text(p), "html.parser")
    for tag in soup(["script", "style", "title", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    html = str(soup.body or soup)
    return markdownify(html, heading_style="ATX", strip=["a"])


def _table_to_md(rows: list[list[str]]) -> str:
    rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in rows if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _from_docx(p: Path) -> str:
    """兜底：按 body 顺序遍历段落与表格（保留标题层级与表格）。"""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(p))
    out: list[str] = []
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            text = (para.text or "").strip()
            if not text:
                continue
            style = (para.style.name or "").lower()
            if style.startswith("heading"):
                level = "".join(ch for ch in style if ch.isdigit()) or "1"
                out.append("#" * min(int(level or 1), 4) + " " + text)
            elif style in ("title", "subtitle"):
                out.append("# " + text)
            else:
                out.append(text)
        elif tag == "tbl":
            table = Table(child, doc)
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            md = _table_to_md(rows)
            if md:
                out.append(md)
    return "\n\n".join(out)


def _from_pdf(p: Path) -> str:
    """兜底：文本层 + 字号分级标题 + find_tables 表格；无文本层则报错。"""
    import pymupdf

    doc = pymupdf.open(str(p))
    try:
        pages = []
        for page in doc:
            pages.append(_pdf_page_blocks(page))
        text_len = sum(len(t) for t, _ in pages)
        if text_len < 50:
            raise ValueError("PDF 无可复制文本（扫描件），需要 OCR/MinerU")
        body_size = _dominant_size(doc)
        header_footer = _repeated_edge_lines(pages)
        out: list[str] = []
        for page_text, blocks in pages:
            for kind, value, size in blocks:
                if kind == "table":
                    md = _table_to_md(value)
                    if md:
                        out.append(md)
                    continue
                line = value.strip()
                if not line or line in header_footer:
                    continue
                if size and body_size and size >= body_size * 1.6:
                    out.append("## " + line)
                elif size and body_size and size >= body_size * 1.18:
                    out.append("### " + line)
                else:
                    out.append(line)
        return "\n\n".join(out)
    finally:
        doc.close()


def _pdf_page_blocks(page) -> tuple[str, list[tuple]]:
    """返回 (整页文本, [(kind, value, size)])，按 y 排序，表格按位置插入。"""
    items: list[tuple[float, str, object, float]] = []
    for blk in page.get_text("dict")["blocks"]:
        for line in blk.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            size = max((s.get("size", 0) for s in spans), default=0)
            items.append((line["bbox"][1], "text", text, size))
    try:
        for table in page.find_tables().tables:
            rows = table.extract()
            if rows:
                items.append((table.bbox[1], "table", rows, 0))
    except Exception as exc:  # noqa: BLE001 —— 表格抽取失败不影响文字
        logger.debug("find_tables 失败: %s", exc)
    items.sort(key=lambda x: x[0])
    blocks = [(kind, value, size) for _, kind, value, size in items]
    return page.get_text(), blocks


def _dominant_size(doc) -> float:
    sizes: dict[float, int] = {}
    for page in list(doc)[:5]:
        for blk in page.get_text("dict")["blocks"]:
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    size = round(float(span.get("size", 0)), 1)
                    sizes[size] = sizes.get(size, 0) + len(span.get("text", ""))
    if not sizes:
        return 0.0
    return max(sizes.items(), key=lambda kv: kv[1])[0]


def _repeated_edge_lines(pages: list[tuple[str, list]]) -> set[str]:
    """页眉/页脚：出现在超过半数页面首/尾行的文本。"""
    if len(pages) < 3:
        return set()
    firsts: dict[str, int] = {}
    lasts: dict[str, int] = {}
    for text, _ in pages:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue
        firsts[lines[0]] = firsts.get(lines[0], 0) + 1
        lasts[lines[-1]] = lasts.get(lines[-1], 0) + 1
    limit = len(pages) / 2
    return {k for k, v in firsts.items() if v > limit} | {k for k, v in lasts.items() if v > limit}


# --------------------------------------------------------------------------- #
# MinerU（子进程）
# --------------------------------------------------------------------------- #
def _resolve_exe() -> str | None:
    """优先用配置的命令；`mineru`（需要本地服务）自动让位给 `mineru-kit`（免服务）。"""
    cmd = settings.KB_MINERU_CMD
    exe = shutil.which(cmd) or (cmd if Path(cmd).is_file() else None)
    if exe and Path(exe).name.lower() in {"mineru", "mineru.exe"}:
        kit = shutil.which("mineru-kit") or shutil.which("mineru-kit.exe")
        if kit:
            return kit
    return exe


def _mineru_help() -> str:
    global _HELP_CACHE
    if _HELP_CACHE is not None:
        return _HELP_CACHE
    exe = _resolve_exe()
    if not exe:
        _HELP_CACHE = ""
        return ""
    try:
        out = subprocess.run([exe, "--help"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
        _HELP_CACHE = (out.stdout or "") + (out.stderr or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("mineru --help 失败: %s", exc)
        _HELP_CACHE = ""
    return _HELP_CACHE


def mineru_available() -> bool:
    help_text = _mineru_help()
    return bool(help_text.strip())


def _build_mineru_args(exe: str, src: Path, outdir: Path, is_pdf: bool) -> list[str]:
    """按 MinerU 版本自适应参数（4.x: `parse <in> -o <out> --tier`；老版: `-p <in> -o <out>`）。"""
    tier = settings.KB_MINERU_TIER if is_pdf else "flash"
    if settings.KB_MINERU_ARGS.strip():
        tmpl = settings.KB_MINERU_ARGS.strip().split()
        return [exe] + [t.format(input=str(src), outdir=str(outdir), tier=tier) for t in tmpl]
    help_text = _mineru_help()
    if re.search(r"\bparse\b", help_text):
        args = [exe, "parse", str(src), "-o", str(outdir)]
        if "--tier" in help_text:
            args += ["--tier", tier]
        if is_pdf and "--pages" in help_text:
            args += ["--pages", "all"]
        if "--wait" in help_text:
            args += ["--wait", str(max(30, int(settings.KB_MINERU_TIMEOUT) - 10))]
        return args
    # 老版 magic-pdf / mineru 2.x
    return [exe, "-p", str(src), "-o", str(outdir), "-m", "auto"]


def _from_mineru(p: Path, warnings: list[str]) -> str | None:
    if not settings.KB_MINERU_ENABLED:
        return None
    if not mineru_available():
        logger.info("MinerU 未安装（KB_MINERU_CMD=%s），走内置兜底", settings.KB_MINERU_CMD)
        return None
    exe = _resolve_exe() or settings.KB_MINERU_CMD
    with tempfile.TemporaryDirectory(prefix="kb_mineru_") as td:
        outdir = Path(td) / "out"
        outdir.mkdir(parents=True, exist_ok=True)
        args = _build_mineru_args(exe, p, outdir, p.suffix.lower() == ".pdf")
        logger.info("调用 MinerU: %s", " ".join(args[:4]))
        try:
            res = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=settings.KB_MINERU_TIMEOUT)
        except subprocess.TimeoutExpired:
            warnings.append(f"MinerU 超时（>{settings.KB_MINERU_TIMEOUT:.0f}s）")
            return None
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"MinerU 调用异常：{type(exc).__name__}")
            return None
        if res.returncode != 0:
            warnings.append(f"MinerU 退出码 {res.returncode}")
            logger.warning("MinerU 失败: %s", (res.stderr or res.stdout or "")[:300])
            return None
        md_files = sorted(outdir.rglob("*.md"), key=lambda f: f.stat().st_size, reverse=True)
        if not md_files:
            warnings.append("MinerU 未产出 md")
            return None
        return md_files[0].read_text(encoding="utf-8", errors="ignore")
