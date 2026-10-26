"""内置 Redis 启动器（v2.0 大点 05 · 裁定 2–8）。

职责只有一件：**保证 `REDIS_URL` 所指的 Redis 可用**。

启动三态（裁定 2）：
1. 探针命中 → 复用外部实例（不启第二份）；
2. 探针失败且 `REDIS_AUTOSTART` 且 `tools/redis/` 有可执行文件 → 拉起内置实例；
3. 都不行 → 返回 degraded，交给四个 service 既有的 try/except 降级。

两个容易踩的点（裁定 4/5）：
- **双探针**：`.env` 的 URL 可能带密码，而内置实例不设密码（裁定 3）。只探"带密码"版本时，
  带 `AUTH` 打无密码实例会报 `ERR Client sent AUTH, but no password is set`；
  更隐蔽的是第二个后端会把"内置实例正在跑"误判成"外部不可用"，再拉一份 → 端口占用 → 降级。
  所以探测顺序是 `as_is`（原样）→ `no_auth`（同址去 auth）。
- **运行时覆写**：命中 `no_auth` 时只改内存里的 `settings.REDIS_URL` 并清掉连接池缓存，
  **不动 `.env`**；外部实例（带密码）恢复时 `as_is` 先命中，行为自动回到原样。

安全边界（裁定 3/6）：内置实例 `bind 127.0.0.1` + `protected-mode yes` + 无密码 + 不落盘 +
`maxmemory 256mb`；`stop()` 只结束**自己拉起的** pid，外部实例绝不触碰。
"""
from __future__ import annotations

import atexit
import logging
import subprocess
import time
import urllib.parse
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)

_EXE = "redis-server.exe"
_NEEDED = ("redis-server.exe", "msys-2.0.dll")  # CLI 只是方便排查，启动必需前两个
_MAXMEM = "256mb"
_proc: subprocess.Popen | None = None
_pid: int | None = None
_state: dict = {"state": "unknown", "message": "未初始化"}


# --------------------------------------------------------------------------- #
# 路径 / URL
# --------------------------------------------------------------------------- #
def bin_dir() -> Path:
    """内置 Redis 目录：相对路径优先在 `team/`（tools/ 所在层）解析，其次 backend/。"""
    raw = Path(getattr(settings, "REDIS_BIN_DIR", "tools/redis") or "tools/redis")
    if raw.is_absolute():
        return raw
    backend = Path(__file__).resolve().parents[2]  # …/team/backend
    for base in (backend.parent, backend):
        cand = base / raw
        if cand.is_dir():
            return cand
    return backend.parent / raw


def _url_variants(url: str) -> list[tuple[str, str]]:
    """探测候选：`as_is`（原样）→ `no_auth`（同 host/port/db，去掉 user/password）。"""
    parts = urllib.parse.urlsplit(url)
    out: list[tuple[str, str]] = [("as_is", url)]
    if parts.username or parts.password:
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        out.append(
            ("no_auth", urllib.parse.urlunsplit((parts.scheme or "redis", netloc, parts.path or "/0", "", "")))
        )
    return out


def _port_of(url: str) -> int:
    return urllib.parse.urlsplit(url).port or 6379


# --------------------------------------------------------------------------- #
# 探测 / 覆写
# --------------------------------------------------------------------------- #
def _probe(url: str, timeout: float = 0.6) -> tuple[bool, str]:
    """PING 一次。返回 (ok, 失败类型)。不抛异常。"""
    import redis

    try:
        client = redis.Redis.from_url(
            url, decode_responses=True, protocol=2,
            socket_connect_timeout=timeout, socket_timeout=timeout,
        )
        return bool(client.ping()), ""
    except redis.exceptions.AuthenticationError:
        return False, "auth"
    except redis.exceptions.ResponseError as exc:
        if "AUTH" in str(exc).upper():
            return False, "auth"
        return False, f"response:{str(exc)[:80]}"
    except Exception as exc:  # noqa: BLE001 —— 连不上就是连不上
        return False, f"conn:{type(exc).__name__}"


def _override_url(url: str) -> None:
    """运行时把连接串改成实际可用的版本（不动 .env），并清掉已建好的连接池。"""
    if url == settings.REDIS_URL:
        return
    logger.info("Redis 运行时连接串覆写：%s → %s", _mask(settings.REDIS_URL), _mask(url))
    settings.REDIS_URL = url
    try:
        from src.core import redis_client

        redis_client.reset()
    except Exception:  # noqa: BLE001 —— 清理失败不影响后续惰性建池
        logger.debug("清理 Redis 连接池失败", exc_info=True)


def _mask(url: str) -> str:
    """日志里不露密码。"""
    parts = urllib.parse.urlsplit(url)
    if not parts.password:
        return url
    netloc = f"***:***@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# --------------------------------------------------------------------------- #
# 拉起 / 停止
# --------------------------------------------------------------------------- #
#: 子进程退出码 → 可读原因（Windows 常见装载失败）
_EXIT_HINTS = {
    -1073741515: "缺 DLL（0xC0000135：系统找不到依赖的动态库）",
    -1073741701: "DLL 位数不匹配或无法初始化（0xC000007B）",
    -1073741502: "DLL 初始化失败（0xC0000142）",
}
_ERR_LOG = "logs/redis.stderr.log"


def _err_tail(d: Path, lines: int = 20) -> str:
    try:
        text = (d / _ERR_LOG).read_text(encoding="utf-8", errors="replace").splitlines()
        return " | ".join(text[-lines:])[:800]
    except Exception:  # noqa: BLE001
        return ""


def _spawn_once(url: str) -> dict:
    """真正拉起一次（不含重试）。返回 {ok, message, retry?}。"""
    global _proc, _pid

    d = bin_dir()
    missing = [n for n in _NEEDED if not (d / n).is_file()]
    if missing:
        return {
            "ok": False,
            "message": f"内置 Redis 缺失（缺 {'/'.join(missing)}）：{d}；"
                       f"可运行 `python scripts/get_redis.py` 或手动放入该目录",
        }

    port = _port_of(url)
    data_dir, log_dir = d / "data", d / "logs"
    for p in (data_dir, log_dir):
        p.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(d / _EXE),
        "--port", str(port),
        "--bind", "127.0.0.1",
        "--protected-mode", "yes",
        "--save", "",
        "--appendonly", "no",
        "--dir", str(data_dir),
        "--logfile", str(log_dir / "redis.log"),
        "--maxmemory", _MAXMEM,
        "--maxmemory-policy", "allkeys-lru",
    ]
    try:
        # 子进程的 stdout/stderr 落文件：装载失败时 Windows 把"缺哪个 DLL"写在这里，
        # 之前丢进 DEVNULL，出问题只能看到一个 0xC0000135 的退出码，没法定位。
        err_fh = (log_dir / _ERR_LOG.split("/")[-1]).open("a", encoding="utf-8", errors="replace")
        _proc = subprocess.Popen(  # noqa: S603 —— 固定参数、固定二进制
            cmd, cwd=str(d), stdin=subprocess.DEVNULL, stdout=err_fh, stderr=subprocess.STDOUT,
        )
        err_fh.close()
    except Exception as exc:  # noqa: BLE001
        _proc = None
        return {"ok": False, "message": f"拉起内置 Redis 失败：{type(exc).__name__}: {exc}"}
    _pid = _proc.pid
    _bind_kill_on_close(_proc)

    # 内置实例无密码（裁定 3）→ 用 no_auth 版本探测
    probe_url = next((u for k, u in _url_variants(url) if k == "no_auth"), url)
    started = time.time()
    deadline = started + float(getattr(settings, "REDIS_BOOT_TIMEOUT", 5.0))
    while time.time() < deadline:
        code = _proc.poll()
        if code is not None:
            hint = _EXIT_HINTS.get(code, "")
            detail = _err_tail(d) or _log_tail(log_dir / "redis.log")
            fast = time.time() - started < 1.0
            _proc, _pid = None, None
            return {
                "ok": False,
                "retry": fast and code in _EXIT_HINTS,   # 装载类失败：等半秒重试一次（可能被杀软占用）
                "message": f"内置 Redis 启动即退出（code={code}{'，' + hint if hint else ''}）：{detail}",
            }
        ok, _why = _probe(probe_url, timeout=0.4)
        if ok:
            _override_url(probe_url)
            return {"ok": True, "pid": _pid, "port": port, "message": f"内置 Redis 已启动 pid={_pid} port={port}"}
        time.sleep(0.15)

    tail = _err_tail(d) or _log_tail(log_dir / "redis.log")
    return {"ok": False, "message": f"内置 Redis {settings.REDIS_BOOT_TIMEOUT}s 内未就绪（端口 {port}）：{tail}"}


def _spawn(url: str) -> dict:
    """拉起内置实例；装载类失败（缺 DLL/位数不符）自动重试一次。"""
    res: dict = {"ok": False, "message": "未执行"}
    for attempt in (1, 2):
        res = _spawn_once(url)
        if res.get("ok") or not res.get("retry") or attempt == 2:
            if res.get("retry") and attempt == 2:
                res["message"] += "（已重试 1 次）"
            return res
        logger.warning("内置 Redis 首次启动失败（%s），0.8s 后重试", res.get("message", "")[:120])
        time.sleep(0.8)
    return res


_JOB = None  # 保持 Job 句柄存活，句柄关闭即杀组内进程


def _bind_kill_on_close(proc: "subprocess.Popen") -> bool:
    """Windows Job Object：父进程无论怎么死（含 taskkill /F），子进程一起结束。

    `Popen.terminate()` 在 Windows 是 TerminateProcess（硬杀），不会走 uvicorn 的
    lifespan 收尾，因此只靠 `stop()` 会留下孤儿 Redis（实测 6380 仍被占用）。
    """
    global _JOB
    if _JOB is not None or not hasattr(proc, "_handle"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return False

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _EXT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return False
        if not k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle))):
            return False
        _JOB = job
        logger.debug("内置 Redis 已加入 Job Object（父进程退出即回收）")
        return True
    except Exception:  # noqa: BLE001 —— 拿不到就当没有，stop() 仍是主路径
        logger.debug("Job Object 绑定失败（不影响正常运行）", exc_info=True)
        return False


def _log_tail(path: Path, lines: int = 20) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return " | ".join(text[-lines:])[:600]
    except Exception:  # noqa: BLE001
        return "（无日志）"


def stop() -> None:
    """只结束自己拉起的实例（裁定 6）。"""
    global _proc, _pid
    proc = _proc
    if proc is None or proc.poll() is not None:
        _proc, _pid = None, None
        return
    logger.info("关闭内置 Redis pid=%s", _pid)
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    _proc, _pid = None, None


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def ensure() -> dict:
    """启动期调用：保证 Redis 可用。返回 {state, message, ...}。

    state: `reuse`（复用外部）/ `internal`（拉起内置）/ `degraded`（降级）
    """
    url = settings.REDIS_URL
    for kind, candidate in _url_variants(url):
        ok, why = _probe(candidate)
        if ok:
            if kind == "no_auth":
                _override_url(candidate)
            state = {
                "state": "reuse",
                "kind": kind,
                "url": _mask(candidate),
                "message": f"复用外部 Redis（{'带密码' if kind == 'as_is' else '无密码'}）{_mask(candidate)}",
            }
            _finish(state)
            return state
        if why == "auth":
            logger.warning(
                "Redis 探测：%s 返回 AUTH 错误（外部实例无密码或 REDIS_URL 密码不符），继续尝试无 auth 变体",
                _mask(candidate),
            )

    if not getattr(settings, "REDIS_AUTOSTART", True):
        state = {"state": "degraded", "message": "Redis 不可用，且 REDIS_AUTOSTART=false（不拉起内置实例）"}
        _finish(state)
        return state

    spawned = _spawn(url)
    if spawned.get("ok"):
        state = {"state": "internal", "pid": spawned.get("pid"), "port": spawned.get("port"),
                 "message": spawned["message"]}
    else:
        state = {"state": "degraded", "message": f"Redis 不可用，已降级：{spawned['message']}"}
    _finish(state)
    return state


def _finish(state: dict) -> None:
    global _state
    _state = state
    if state["state"] == "degraded":
        logger.warning(state["message"])
    else:
        logger.info(state["message"])


def status() -> dict:
    """当前状态（供调试/健康检查）。"""
    return dict(_state, bin_dir=str(bin_dir()), pid=_pid)


atexit.register(stop)
