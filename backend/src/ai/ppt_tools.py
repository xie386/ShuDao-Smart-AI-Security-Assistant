"""PPT Agent 的工具实现（v2.0 大点 02）。

四类工具，全部受沙箱与上限约束（`src/core/paths.py` 负责路径安全）：

1. `officecli_*`  —— 调用 OfficeCLI 编辑 pptx / 截图 / 检查问题 / 校验（本机 33MB exe，实测可用）
2. `search_images` —— Openverse（无需 key，CC 授权），失败返回空并降级
3. `kb_search`     —— 复用 01 大点的 RAG（预处理 → 多子查询检索 → 预算裁剪）
4. `fs_*`          —— 沙箱内的基础文件操作（列目录 / 读文本）

设计约束（来自裁定 8）：**OfficeCLI 是目标 pptx 的唯一写者**，本模块不引入第二个写入方。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import uuid
import time
from pathlib import Path

import httpx

from src.core.config import settings
from src.core.paths import safe_join, safe_name

logger = logging.getLogger(__name__)

OPENVERSE_API = settings.PPT_IMAGE_API_BASE or "https://api.openverse.org/v1/images/"
#: 只接受可商用/可再分发的授权；CC BY / BY-SA 需要署名（会写进 sources.json 与末页）
PHOTO_LICENSES = "cc0,pdm,by,by-sa"


# --------------------------------------------------------------------------- #
# OfficeCLI
# --------------------------------------------------------------------------- #
#: 项目根（team/）：backend/src/ai/ppt_tools.py → parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
#: 随项目分发的编辑器位置（clone 即用，不需要每人配 .env）
VENDORED_OFFICECLI = PROJECT_ROOT / "tools" / "officecli" / "officecli.exe"
#: 本机历史安装位置（仅兜底，换机器不需要）
LEGACY_OFFICECLI = Path(r"D:\appadte\OfficeCLI\officecli.exe")


def officecli_path() -> Path | None:
    """解析 OfficeCLI 可执行文件（三级，先到先得）。

    1. `.env` 里显式配置的 `PPT_OFFICECLI_PATH`（相对路径按项目根解析）；
    2. **随项目分发**的 `tools/officecli/officecli.exe`（默认走这里）；
    3. 项目根直接放的 `officecli.exe` / 本机旧安装路径（兼容老环境）。

    都找不到就返回 None —— 调用方据此降级到 python-pptx 兜底渲染（验证项 7）。
    """
    configured = (settings.PPT_OFFICECLI_PATH or "").strip()
    candidates: list[Path] = []
    if configured:
        cand = Path(configured).expanduser()
        candidates.append(cand if cand.is_absolute() else PROJECT_ROOT / cand)
    candidates += [VENDORED_OFFICECLI, PROJECT_ROOT / "officecli.exe", LEGACY_OFFICECLI]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def officecli_available() -> bool:
    return officecli_path() is not None


def _oci(*args: str, timeout: float | None = None) -> tuple[int, str, str]:
    """调用 officecli，返回 (returncode, stdout, stderr)。"""
    exe = officecli_path()
    if exe is None:
        raise RuntimeError(
            "OfficeCLI 不可用：未找到 tools/officecli/officecli.exe（可用 PPT_OFFICECLI_PATH 指定）"
        )
    proc = subprocess.run(
        [str(exe), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout or settings.PPT_OFFICECLI_TIMEOUT,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def create_deck(path: str | Path) -> Path:
    """新建空白 pptx（已存在则先释放常驻进程再删，避免 Windows 文件占用）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        release_file(p)
    code, out, err = _oci("create", str(p))
    if code != 0 or not p.is_file():
        raise RuntimeError(f"officecli create 失败: {err or out}")
    return p


def release_file(path: str | Path, attempts: int = 8) -> bool:
    """关掉常驻进程并等文件句柄释放（Windows 下 close 后仍有几百毫秒锁窗）。"""
    p = Path(path)
    close_doc(p)
    for _ in range(attempts):
        try:
            if p.exists():
                with p.open("rb"):
                    pass
            return True
        except OSError:
            time.sleep(0.3)
    try:
        if p.exists():
            p.unlink()
        return True
    except OSError:
        return False


def open_doc(path: str | Path) -> None:
    """启动常驻进程（反复编辑同一文件更快；失败不致命）。"""
    try:
        _oci("open", str(path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("officecli open 失败(忽略): %s", exc)


def save_doc(path: str | Path) -> None:
    try:
        _oci("save", str(path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("officecli save 失败(忽略): %s", exc)


def close_doc(path: str | Path) -> None:
    """关闭常驻进程并落盘（进程不在时忽略）。"""
    try:
        _oci("close", str(path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("officecli close 失败(忽略): %s", exc)


def apply_commands(path: str | Path, commands: list[dict]) -> list[dict]:
    """批量执行编辑命令，返回每条的回执：[{"index": i, "ok": bool, "path": 路径或 None, "line": 原文}]。

    为什么要解析路径：挂超链接需要目标元素的真实路径（`/slide[3]/shape[@id=100019]`），
    而 id 是 officecli 运行时分配的，只能从 batch 回执里取。
    """
    if not commands:
        return []
    # 下划线开头的键是本地元数据（_link/_tooltip 等），officecli 会报 unknown field → 下发前剥掉
    clean = [
        {k: v for k, v in cmd.items() if not str(k).startswith("_")}
        for cmd in commands
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(clean, fh, ensure_ascii=False)
        tmp = fh.name
    try:
        code, out, err = _oci("batch", str(path), "--input", tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    if code != 0:
        raise RuntimeError(f"officecli batch 失败({len(commands)} 条): {err or out}")
    receipts: list[dict] = []
    for line in out.splitlines():
        m = re.match(r"\[(\d+)\]\s+(Added|Set|Removed|Moved|Swapped|ERROR)\b(.*)", line.strip())
        if not m:
            continue
        idx = int(m.group(1)) - 1          # officecli 用 1 起编号
        rest = m.group(3)
        path = None
        pm = re.search(r"\bat\s+(/\S+)", rest)
        if pm:
            path = pm.group(1)
        receipts.append({"index": idx, "ok": m.group(2) != "ERROR", "path": path, "line": line.strip()})
    return receipts


def apply_hyperlinks(path: str | Path, pairs: list[tuple[str, str, str]]) -> int:
    """给元素挂超链接。pairs = [(元素路径, URL, tooltip)]，返回成功条数。

    合规要求（Unsplash）：摄影师全名与 Unsplash 必须"署名 + 可点击"，
    所以署名文字框都带 link；纯文本署名不算合规。
    """
    if not pairs:
        return 0
    cmds = [
        {"command": "add", "parent": elem, "type": "hyperlink",
         "props": {"link": url, "tooltip": (tip or "")[:80]}}
        for elem, url, tip in pairs if elem and url
    ]
    if not cmds:
        return 0
    receipts = apply_commands(path, cmds)
    ok = sum(1 for r in receipts if r["ok"])
    if ok != len(cmds):
        logger.warning("超链接附加部分失败: %s/%s", ok, len(cmds))
    return ok


def render_page(path: str | Path, page: int, out_png: str | Path, width: int = 1600) -> Path:
    """把第 N 页渲染成 PNG（native 渲染走本机 PowerPoint）。"""
    png = Path(out_png)
    png.parent.mkdir(parents=True, exist_ok=True)
    code, out, err = _oci(
        "view", str(path), "screenshot", "--page", str(page), "-o", str(png),
        "--screenshot-width", str(width),
    )
    if code != 0 or not png.is_file():
        raise RuntimeError(f"officecli screenshot 失败(page={page}): {err or out}")
    return png


def inspect_issues(path: str | Path, kinds: str = "format,content") -> list[dict]:
    """取结构化问题清单（含 pptx 专有的 low_contrast / picture_aspect_distorted 等）。

    注意：officecli 的 `--type` **不接受逗号分隔**（实测报 invalid_issue_type 并返回空），
    必须一个桶查一次再合并 —— 早前默认传 "content,format" 导致这项检查一直静默失效。
    """
    merged: list[dict] = []
    for kind in [k.strip() for k in kinds.split(",") if k.strip()]:
        code, out, err = _oci("view", str(path), "issues", "--type", kind, "--json")
        if code != 0:
            logger.debug("officecli issues(%s) 失败: %s", kind, err or out)
            continue
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            continue
        bucket = ((data.get("data") or {}).get("issues")
                  or data.get("issues") or data.get("results") or [])
        if isinstance(bucket, list):
            merged.extend(bucket)
    return merged


def validate(path: str | Path) -> str:
    code, out, err = _oci("validate", str(path))
    return out or err


# --------------------------------------------------------------------------- #
# 图片源（v2.0 大点 02：默认 Unsplash，Openverse 保留为可配备选）
# --------------------------------------------------------------------------- #
UNSPLASH_API = "https://api.unsplash.com/search/photos"
#: Unsplash API 指引要求所有回链带 utm（来源应用名 + referral）
UNSPLASH_UTM = "utm_source=shudao_safety&utm_medium=referral"


def _with_utm(url: str) -> str:
    """给 unsplash.com 链接补 utm 参数（外部链接原样返回）。"""
    u = (url or "").strip()
    if not u:
        return u
    # 只给 unsplash.com 的**页面**加 utm；图片 CDN（images.unsplash.com）不能加参数
    if not u.startswith("https://unsplash.com/"):
        return u
    return u + ("&" if "?" in u else "?") + UNSPLASH_UTM


def _http() -> httpx.Client:
    # trust_env=True：让本机/生产环境的 HTTP_PROXY 生效（国外站点多数要走代理）
    return httpx.Client(timeout=settings.PPT_IMAGE_TIMEOUT, follow_redirects=True, trust_env=True)


def current_provider() -> str:
    """当前图源。配置成 unsplash 但没 key 时回退 openverse（否则会 401 一片）。"""
    want = (settings.PPT_IMAGE_PROVIDER or "unsplash").strip().lower()
    if want not in ("unsplash", "openverse"):
        want = "openverse"
    if want == "unsplash" and not (settings.UNSPLASH_ACCESS_KEY or "").strip():
        logger.warning("PPT_IMAGE_PROVIDER=unsplash 但 UNSPLASH_ACCESS_KEY 为空，回退 openverse")
        return "openverse"
    return want


def _norm(title: str, url: str, license_: str, license_version: str, creator: str,
          attribution: str, source_url: str, track_url: str | None = None,
          width: int | None = None, height: int | None = None,
          creator_url: str = "", photo_url: str = "") -> dict:
    """统一的图片候选结构。

    creator_url / photo_url 是合规必需项（Unsplash 要求"摄影师全名 + Unsplash 均署名且可点击"），
    Openverse 侧用 landing page 兜底。
    """
    return {
        "url": url, "title": (title or "")[:90],
        "license": license_, "license_version": license_version,
        "creator": (creator or "")[:60], "attribution": (attribution or "")[:300],
        "source_url": source_url or url, "track_url": track_url,
        "width": int(width) if width else None, "height": int(height) if height else None,
        "creator_url": creator_url or source_url or "",
        "photo_url": photo_url or source_url or "",
    }


def _unsplash_candidates(query: str, n: int) -> list[dict]:
    """Unsplash 检索。实测（2026-10-24）：4/5 命中、约 1.8s、图片可下载；Demo key 限 50 次/小时。"""
    key = (settings.UNSPLASH_ACCESS_KEY or "").strip()
    if not key:
        return []
    for attempt in range(2):
        try:
            with _http() as client:
                resp = client.get(
                    UNSPLASH_API,
                    params={"query": query.strip(), "per_page": max(1, min(n * 2, 8)),
                            "orientation": "landscape", "content_filter": "high"},
                    headers={"Authorization": "Client-ID " + key, "Accept-Version": "v1"},
                )
            left = resp.headers.get("x-ratelimit-remaining")
            if left is not None and left.isdigit() and int(left) <= 3:
                logger.warning("Unsplash 配额将尽：每小时剩余 %s 次（Demo 上限 50）", left)
            if resp.status_code in (401, 403, 429):
                logger.warning("Unsplash 拒绝请求: HTTP %s（%s）", resp.status_code,
                               (resp.text or "")[:120])
                return []
            resp.raise_for_status()
            out = []
            for r in ((resp.json() or {}).get("results") or []):
                urls = r.get("urls") or {}
                user = r.get("user") or {}
                name = (user.get("name") or "").strip() or "Unsplash 摄影师"
                out.append(_norm(
                    r.get("alt_description") or r.get("description") or "配图",
                    urls.get("regular") or urls.get("small") or "",
                    "unsplash license", "", name,
                    "Photo by " + name + " on Unsplash",
                    (r.get("links") or {}).get("html") or "",
                    (r.get("links") or {}).get("download_location"),
                    r.get("width"), r.get("height"),
                    _with_utm((user.get("links") or {}).get("html") or ""),
                    _with_utm((r.get("links") or {}).get("html") or ""),
                ))
            return out
        except Exception as exc:  # noqa: BLE001
            if attempt == 0:
                logger.info("Unsplash 首次检索失败，重试一次: %s", exc)
            else:
                logger.warning("Unsplash 检索失败(降级为无图): %s", exc)
    return []


def _openverse_candidates(query: str, n: int) -> list[dict]:
    """Openverse 检索（备选）。本机实测常超时，保留以支持换源对比。"""
    for attempt in range(2):
        try:
            with _http() as client:
                resp = client.get(
                    OPENVERSE_API,
                    params={"q": query.strip(), "page_size": max(n * 2, 4),
                            "license": PHOTO_LICENSES, "mature": "false"},
                )
            resp.raise_for_status()
            out = []
            for r in ((resp.json() or {}).get("results") or []):
                lic = (r.get("license") or "").strip()
                ver = (r.get("license_version") or "").strip()
                creator = (r.get("creator") or "").strip()
                out.append(_norm(
                    r.get("title") or "配图",
                    r.get("url") or r.get("thumbnail") or "",
                    lic, ver, creator,
                    (r.get("attribution") or (creator + " / CC " + lic.upper() + " " + ver)).strip(),
                    r.get("foreign_landing_url") or r.get("url") or "",
                    None,                        # track_url（Openverse 无下载事件）
                    r.get("width"), r.get("height"),
                    r.get("creator_url") or r.get("foreign_landing_url") or "",
                    r.get("foreign_landing_url") or "",
                ))
            return out
        except Exception as exc:  # noqa: BLE001
            if attempt == 0:
                logger.info("Openverse 首次检索失败，重试一次: %s", exc)
            else:
                logger.warning("Openverse 检索失败(降级为无图): %s", exc)
    return []


def _track_download(track_url: str | None) -> None:
    """Unsplash API 要求：经 API 下载图片时打一次 download 事件（best-effort，失败忽略）。"""
    key = (settings.UNSPLASH_ACCESS_KEY or "").strip()
    if not track_url or not key:
        return
    try:
        with _http() as client:
            client.get(track_url, headers={"Authorization": "Client-ID " + key})
    except Exception as exc:  # noqa: BLE001
        logger.debug("Unsplash download 事件上报失败(忽略): %s", exc)


def search_images(query_en: str, n: int, dest_dir: str | Path) -> list[dict]:
    """按当前图源检索并下载图片，返回 [{path, license, creator, attribution, source_url}]。

    任何失败（超时/空结果/超限/配额用尽）都返回已拿到的部分，不抛异常 —— 裁定 6：
    图片失败必须降级，不能让整单失败。
    """
    if not settings.PPT_IMAGES_ENABLED or not (query_en or "").strip():
        return []
    n = max(0, min(int(n), 8))
    if n == 0:
        return []
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    provider = current_provider()
    picker = _unsplash_candidates if provider == "unsplash" else _openverse_candidates
    candidates = picker(query_en, n)

    items: list[dict] = []
    for idx, cand in enumerate(candidates):
        if len(items) >= n:
            break
        url = (cand.get("url") or "").strip()
        if not url:
            continue
        ext = ".jpg"
        for cand_ext in (".png", ".jpeg", ".jpg", ".webp"):
            if url.lower().split("?")[0].endswith(cand_ext):
                ext = cand_ext
                break
        # 文件名必须跨查询唯一：早前用 img{序号}_{idx} 命名，三张图互相覆盖成一张（实测踩到）
        slug = safe_name(query_en)[:10] or "img"
        target = safe_join(dest, "img%02d_%s_%s%s" % (len(items) + 1, slug, uuid.uuid4().hex[:6], ext))
        try:
            with _http() as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if not ctype.startswith("image/"):
                        continue
                    written = 0
                    with target.open("wb") as fh:
                        for chunk in resp.iter_bytes(65536):
                            written += len(chunk)
                            if written > settings.PPT_IMAGE_MAX_BYTES:
                                raise ValueError("image too large")
                            fh.write(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.debug("图片下载失败 %s: %s", url[:80], exc)
            target.unlink(missing_ok=True)
            continue
        _track_download(cand.get("track_url"))
        # 记录真实像素尺寸：版式按它等比缩放，避免"图片被拉扁"
        w, h = cand.get("width"), cand.get("height")
        try:
            from PIL import Image  # lazy：仅在成功下载后量一次

            with Image.open(target) as im:
                w, h = im.size
        except Exception:  # noqa: BLE001
            pass
        items.append({
            "path": str(target), "file": target.name,
            "title": cand.get("title", ""),
            "license": cand.get("license", ""),
            "license_version": cand.get("license_version", ""),
            "creator": cand.get("creator", ""),
            "attribution": cand.get("attribution", ""),
            "source_url": cand.get("source_url", ""),
            "creator_url": cand.get("creator_url", ""),
            "photo_url": cand.get("photo_url", ""),
            "width": w, "height": h,
            "provider": provider,
        })
    return items


def write_sources(dest_dir: str | Path, items: list[dict]) -> Path:
    """把图片来源/授权写进 sources.json（末页"图片来源"与审计都用它）。"""
    path = Path(dest_dir) / "sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "file": it.get("file"),
            "title": it.get("title"),
            "license": it.get("license"),
            "license_version": it.get("license_version"),
            "creator": it.get("creator"),
            "attribution": it.get("attribution"),
            "source_url": it.get("source_url"),
            "creator_url": it.get("creator_url"),
            "photo_url": it.get("photo_url"),
            "provider": it.get("provider"),
            "width": it.get("width"),
            "height": it.get("height"),
        }
        for it in items
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 知识库检索（复用 01 大点）
# --------------------------------------------------------------------------- #
def kb_search(query: str, k: int | None = None) -> str:
    """检索企业知识库，返回带 [来源] 标注的文本（供 LLM 写稿引用）。"""
    from src.ai.rag import format_context, retrieve_queries  # lazy：避免启动即加载模型

    blocks = retrieve_queries([query], k=k or settings.RAG_TOP_K_RERANK * 2)
    if not blocks:
        return ""
    text, _sources = format_context(blocks)
    return text


# --------------------------------------------------------------------------- #
# 沙箱内文件操作
# --------------------------------------------------------------------------- #
def fs_list(root: str | Path) -> list[str]:
    base = Path(root)
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir())


def fs_read(root: str | Path, rel: str, limit: int = 20000) -> str:
    path = safe_join(root, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    return path.read_text(encoding="utf-8", errors="replace")[:limit]
