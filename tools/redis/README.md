# 内置 Redis（Windows x64）

本目录是 v2.0 大点 05 的内置缓存服务器。后端启动时会先探测 `REDIS_URL`，探测不到才由
`src/core/redis_boot.py` 拉起这里的实例（**只 bind 127.0.0.1、不设密码、不落盘**）。

## 来源与版本

| 项 | 值 |
|---|---|
| 上游 | https://github.com/redis-windows/redis-windows （Redis 官方源码的社区 MSYS2 移植，**非 Redis 官方构建**） |
| 版本 | **7.4.3** |
| 发布页 | https://github.com/redis-windows/redis-windows/releases/tag/7.4.3 |
| 发布包 | `Redis-7.4.3-Windows-x64-msys2.zip`（10.4 MB） |
| 发布包 SHA256 | `9e94bdeddc25baa362caefbc2a3003debb8890d4bd239dabffa4200928d0658c` |
| 许可 | BSD-3-Clause（同 Redis 7.4） |

随仓库分发 **4 个 DLL/EXE**（合计 14.1 MB），`redis-benchmark` / `redis-sentinel` / 检查工具 / pdb 等均**未**收录：

| 文件 | 大小 | SHA256 |
|---|---|---|
| `redis-server.exe` | 4.2 MB | `60f0368a0dee8ae53df329a408e48ff2ad0599fa4e0a2feb7e29233988fcb55a` |
| `redis-cli.exe` | 1.0 MB | `823b80c22fa3885bf0984f4bc3ddebfc2e6b0227335a115ec2e6fdfe162a2b78` |
| `msys-2.0.dll` | 3.2 MB | `2813d00f34d60d17b5b192acd3450720966fa103fb0669c3f69c36c67204a66b` |
| `msys-crypto-3.dll` | 5.0 MB | `8fed7797a25bae81f4aa3a977e73ac9a9baea362c3257deb2b50330c925eb534` |
| `msys-ssl-3.dll` | 0.9 MB | `94edf59ef186e71a751dc085856b6b76fb751d42c268a78b9b621ba850bd1052` |

`msys-crypto-3.dll` / `msys-ssl-3.dll` 实际只在 TLS 时加载，但**一并随仓库分发**：
现场出现过 `redis-server.exe` 启动即退出（退出码 `0xC0000135` = 缺 DLL）的情况，带上这两个依赖可消除这一整类装载失败。

## 本地运行行为

启动参数由 `redis_boot._spawn()` 固定下发：

```
redis-server.exe --port <REDIS_URL 端口> --bind 127.0.0.1 --protected-mode yes \
  --save "" --appendonly no --dir ./data --logfile ./logs/redis.log \
  --maxmemory 256mb --maxmemory-policy allkeys-lru
```

- `data/`、`logs/` 为运行时目录，**已在 .gitignore 中忽略**；
- 不设密码（只监听内环）；如需密码，请改用自己的外部实例并把密码写进 `.env` 的 `REDIS_URL`；
- 关闭后端时由 `redis_boot.stop()` 结束本进程（外部实例不受影响）。

## 替换 / 升级

1. 从发布页下载新版 zip，核对发布页给出的 SHA256；
2. 解出 `redis-server.exe`、`redis-cli.exe`、`msys-2.0.dll`、`msys-crypto-3.dll`、`msys-ssl-3.dll` 覆盖本目录；
3. 更新本 README 的版本与四个 SHA256；
4. 跑一遍验收：`pytest tests/test_redis_boot.py` + 无 Redis 环境下启动后端看日志。

也可以不手工下载：`python scripts/get_redis.py`（会下载同一发布包、校验、只解 3 个文件）。

## 常见问题

- **启动报缺 DLL**：确认 `msys-2.0.dll` 与本 exe 同目录；
- **Windows Defender 误报**：社区移植版偶发误报，可对本目录加排除；介意的话改用外部 Redis（`.env` 指向别处）或不启用 `REDIS_AUTOSTART`；
- **端口被占**：`REDIS_URL` 的端口若被别的程序占用，内置实例会 `bind` 失败 → 后端降级并打印原因，不会强占端口。

## 启动失败怎么查

`redis_boot` 会把子进程的 stdout/stderr 追加到 `logs/redis.stderr.log`，并把退出码翻译成可读原因：

| 退出码 | 含义 |
|---|---|
| `-1073741515`（0xC0000135） | 缺 DLL：确认本目录 4 个文件齐全（尤其 `msys-2.0.dll` 与 exe 同目录） |
| `-1073741701`（0xC000007B） | DLL 位数不符 / 初始化失败：确认是 x64 包，且没有从别的软件目录混入同名 DLL |
| `-1073741502`（0xC0000142） | DLL 初始化失败：多为杀软拦截或文件损坏，重跑 `scripts/get_redis.py --force` |

装载类失败 `redis_boot` 会自动**重试一次**（首次执行被杀软扫描占用是常见诱因），仍失败才降级。
