"""下载并安装内置 Redis（v2.0 大点 05）。

默认行为：下载 Redis-7.4.3-Windows-x64-msys2.zip → 校验发布包 SHA256 → 只解出
`redis-server.exe` / `redis-cli.exe` / `msys-2.0.dll` / `msys-crypto-3.dll` / `msys-ssl-3.dll` 到 `team/tools/redis/`。

用法：
    python scripts/get_redis.py                     # 直连下载
    python scripts/get_redis.py --proxy 127.0.0.1:7897   # 走本机代理
    python scripts/get_redis.py --zip D:/tmp/redis.zip   # 离线：用已下好的包
    python scripts/get_redis.py --force             # 已存在也覆盖

为什么要有它：仓库体积紧张时可以不提交那 8.2 MB 二进制，clone 后跑一次本脚本即可；
也可以完全不用内置实例（`.env` 里关掉 REDIS_AUTOSTART，指向自己的 Redis）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

VERSION = "7.4.3"
RELEASE_URL = (
    f"https://github.com/redis-windows/redis-windows/releases/download/{VERSION}/"
    f"Redis-{VERSION}-Windows-x64-msys2.zip"
)
ZIP_SHA256 = "9e94bdeddc25baa362caefbc2a3003debb8890d4bd239dabffa4200928d0658c"
EXPECTED = {
    "redis-server.exe": "60f0368a0dee8ae53df329a408e48ff2ad0599fa4e0a2feb7e29233988fcb55a",
    "redis-cli.exe": "823b80c22fa3885bf0984f4bc3ddebfc2e6b0227335a115ec2e6fdfe162a2b78",
    "msys-2.0.dll": "2813d00f34d60d17b5b192acd3450720966fa103fb0669c3f69c36c67204a66b",
    "msys-crypto-3.dll": "8fed7797a25bae81f4aa3a977e73ac9a9baea362c3257deb2b50330c925eb534",
    "msys-ssl-3.dll": "94edf59ef186e71a751dc085856b6b76fb751d42c268a78b9b621ba850bd1052",
}
# 发布包里这三类文件可能位于一级子目录下
INNER_DIR = f"Redis-{VERSION}-Windows-x64-msys2"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, proxy: str | None, timeout: int = 300) -> bytes:
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": f"http://{proxy}", "https": f"http://{proxy}"}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "shudao-get-redis/1.0"})
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310 —— 固定上游地址
        total = int(resp.headers.get("Content-Length") or 0)
        chunks, got = [], 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
            if total:
                print(f"\r  下载中 {got * 100 // total:3d}%  ({got // 1024} KB / {total // 1024} KB)", end="", flush=True)
        print()
        return b"".join(chunks)


def install(data: bytes, dest: Path, force: bool) -> int:
    digest = sha256_bytes(data)
    if digest != ZIP_SHA256:
        print(f"发布包 SHA256 不符：\n  期望 {ZIP_SHA256}\n  实际 {digest}")
        print("拒绝安装（可能是上游重新打包或下载被篡改）。")
        return 2

    dest.mkdir(parents=True, exist_ok=True)
    done = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = {Path(n).name: n for n in zf.namelist() if not n.endswith("/")}
        for name, want in EXPECTED.items():
            target = dest / name
            if target.exists() and not force:
                actual = sha256_bytes(target.read_bytes())
                if actual == want:
                    print(f"  已存在且校验通过：{name}")
                    continue
            src = names.get(name)
            if not src:
                print(f"发布包里没有 {name}（包结构可能变了），中止。")
                return 3
            payload = zf.read(src)
            actual = sha256_bytes(payload)
            if actual != want:
                print(f"  {name} SHA256 不符（期望 {want[:16]}… 实际 {actual[:16]}…），中止。")
                return 3
            target.write_bytes(payload)
            print(f"  已安装 {name}  {len(payload) // 1024} KB")
            done += 1

    print(f"\n完成：{done} 个文件写入 {dest}")
    print("下一步：直接启动后端即可（redis_boot 会自动拉起）；或用 .env 的 REDIS_URL 指向自己的实例。")
    return 0


def main(argv: list[str] | None = None) -> int:
    backend = Path(__file__).resolve().parent.parent
    default_dest = backend.parent / "tools" / "redis"
    ap = argparse.ArgumentParser(description="下载并安装内置 Redis（Windows x64）")
    ap.add_argument("--dest", default=str(default_dest), help=f"安装目录（默认 {default_dest}）")
    ap.add_argument("--proxy", default=None, help="HTTP 代理，如 127.0.0.1:7897")
    ap.add_argument("--zip", default=None, help="离线：使用已下载的 zip 路径")
    ap.add_argument("--force", action="store_true", help="已存在也覆盖")
    args = ap.parse_args(argv)

    dest = Path(args.dest)
    if args.zip:
        path = Path(args.zip)
        if not path.is_file():
            print(f"找不到离线包：{path}")
            return 1
        data = path.read_bytes()
        print(f"使用离线包 {path}（{len(data) // 1024} KB）")
    else:
        print(f"下载 Redis {VERSION}：{urllib.parse.urlsplit(RELEASE_URL).hostname} …")
        try:
            data = fetch(RELEASE_URL, args.proxy)
        except Exception as exc:  # noqa: BLE001
            print(f"下载失败：{type(exc).__name__}: {exc}")
            print("可换用 --proxy 127.0.0.1:7897，或手动下载后 --zip <路径>，或直接关闭 REDIS_AUTOSTART。")
            return 1
    return install(data, dest, args.force)


if __name__ == "__main__":
    sys.exit(main())
