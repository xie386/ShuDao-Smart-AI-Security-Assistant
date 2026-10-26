# 05 缓存服务器（Redis）集成 · 实现记录

> 方案：`../Verdict/05-Redis集成方案.md`（裁定 1–12）｜调研：`../research/05-Redis集成方案.md`
> 实现：2026-10-27｜状态：**已完成（真机 12 项验收通过）**

---

## 一、做了什么

把「Redis 必须先在本机别处装好」这个隐形前置条件消掉：项目自带一份 Redis 7.4.3 最小集，
后端启动时**先探测、探测不到就拉起**，退出时只收回自己拉起的那份。

### 1.1 新增 / 改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/core/redis_boot.py` | 新增 268 行 | 启动三态（复用 → 拉起 → 降级）、双探针、Job Object 绑定、`stop()`、`status()` |
| `src/core/redis_client.py` | 改 | `redis_client` 改 **惰性代理** `_LazyRedis`；新增 `reset()`；首次连接**自愈**（服务端无密码时自动去 auth） |
| `src/core/config.py` | 改 | `REDIS_AUTOSTART=True` / `REDIS_BIN_DIR="tools/redis"` / `REDIS_BOOT_TIMEOUT=5.0` |
| `src/main.py` | 改 | `lifespan` 开头 `ensure()`（早于 RAG 预热），`finally` 里 `stop()` |
| `team/tools/redis/` | 新增 | `redis-server.exe`(4.2 MB) + `redis-cli.exe`(1.0 MB) + `msys-2.0.dll`(3.2 MB) + `README.md`（来源/版本/SHA256/许可/替换） |
| `team/.gitignore` | 改 | 忽略 `tools/redis/data/`、`tools/redis/logs/`；二进制与 README 入库 |
| `scripts/get_redis.py` | 新增 150 行 | 下载 10.4 MB 发布包 → 校验 SHA256 → 只解 3 个文件；`--proxy` / `--zip` / `--force` |
| `tests/test_redis_boot.py` | 新增 16 例 | 假 probe / 假 Popen，不启真进程 |

### 1.2 关键实现点

1. **双探针**（裁定 4）：`.env` 带密码、内置实例无密码，探测顺序 `as_is` → `no_auth`；
   命中 `no_auth` 时**只覆写运行时** `settings.REDIS_URL` 并 `redis_client.reset()`，不改 `.env`。
2. **惰性客户端**（裁定 5）：`redis_client = _LazyRedis()`，首次命令才建池，四个 service 零改动。
3. **客户端自愈**（实现期补充，见 §四偏差 2）：`get_redis()` 首次 `PING` 若报
   `AUTH ... without any password`，自动去掉 auth 重建——这样**不进 lifespan 的脚本**
   （探针、离线任务）也能用内置实例；密码真的错（`WRONGPASS`）则原样报错，不掩盖配置问题。
4. **Job Object**（实现期补充，见 §四偏差 1）：`Popen.terminate()` 在 Windows 是硬杀，
   不走 uvicorn 收尾 → 会留孤儿 Redis。拉起后立刻把子进程加入
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 Job，父进程无论怎么死（含 `taskkill /F`）子进程一起回收。
5. **只杀自己的 pid**（裁定 6）：`stop()` 只看 `_proc`；外部实例没有 pid，绝不触碰。

---

## 二、真机验收（12 项，全过）

环境：本机 6380 无任何 Redis；`.env` 为 `redis://:123456@127.0.0.1:6380/0`；后端用 `--port 802x` 起。

| # | 用例 | 结果 |
|---|---|---|
| 1 | 无 Redis 启动 → 自动拉起内置实例 | `内置 Redis 已启动 pid=16040 port=6380`，`netstat` 6380 LISTENING |
| 2 | 运行时连接串覆写 | `redis://***:***@127.0.0.1:6380/0 → redis://127.0.0.1:6380/0` |
| 3 | 通知链路 | `push→True`、读回 `['测试通知：内置 Redis']`、`count=1` |
| 4 | PPT 进度 + 锁 | `set_progress=True`、`get_progress` 回 8 字段、`acquire_lock=True` 且**重复抢锁=False**、`lock_held=True` |
| 5 | 会话缓存 | `available()=True` |
| 6 | 独立进程自愈 | 不带 lifespan 的脚本直接调 service，自动改用无 auth 连接串，四条链路同上全通过 |
| 7 | 外部带密码实例复用 | 起一份 `--requirepass secret9`（6392）→ 日志 `复用外部 Redis（带密码）`，**未拉起第二份**，杀后端后外部实例仍在 |
| 8 | 双后端复用内置实例 | 第二个后端日志 `复用外部 Redis（无密码） redis://127.0.0.1:6380/0`，`tasklist` 仅 1 个 `redis-server.exe` |
| 9 | `taskkill /F` 硬杀后端 | 2.5s 后 `redis-server` 清零、6380 释放（Job Object 生效） |
| 10 | `REDIS_AUTOSTART=false` + 无 Redis | 服务正常起，日志 `Redis 不可用，且 REDIS_AUTOSTART=false（不拉起内置实例）` |
| 11 | `REDIS_BIN_DIR` 指空目录 | 服务正常起，日志 `内置 Redis 缺失（缺 redis-server.exe/msys-2.0.dll）：…可运行 python scripts/get_redis.py` |
| 12 | `.gitignore` | `redis-server.exe`/`README.md` 入库；`data/dump.rdb`、`logs/redis.log` 被忽略 |

补充：`scripts/get_redis.py --zip <本地包>` 实测可校验并跳过已存在的正确文件（3/3 命中），下载分支同源同 URL。

---

## 三、测试

```
tests/test_redis_boot.py                     16 passed
+ 04/03 相关 8 个文件合并                     120 passed / 55.65s
```

单元测试覆盖：URL 变体（带/不带 auth、端口默认值、密码脱敏）、复用 as_is / no_auth（含运行时覆写与连接池清理）、AUTH 错误识别、
`REDIS_AUTOSTART=false`、缺二进制提示、拉起成功 / 超时降级、`stop()` 只杀自己且跳过已死进程、`_LazyRedis` 透传与 `reset()`。

---

## 四、实现期偏差与补充（相对裁定）

| # | 裁定原文 | 实际做法 | 原因 |
|---|---|---|---|
| 1 | 裁定 6「`lifespan` 收尾 + `atexit` 兜底」 | **增加 Job Object 绑定** | 实测：`Popen.terminate()`（含 `taskkill /F`）在 Windows 是硬杀，`lifespan` 不执行 → 留下孤儿 Redis 占着 6380。Job Object 让子进程随父进程句柄关闭一起结束，硬杀也干净 |
| 2 | 裁定 4「`ensure()` 双探针」 | **客户端侧也加一次自愈** | 探针脚本/离线任务不经过 `lifespan`，`get_redis()` 首次连接仍会用带密码 URL → `AUTH` 报错。加到客户端后所有进程一致；仅对"服务端没设密码"生效，`WRONGPASS` 不吞 |
| 3 | 裁定 9「单测不启真进程」 | 遵守；Job Object 只做真机验证 | 它依赖 Windows 句柄语义，无 mock 价值 |

---

## 五、遗留 / 后续可选

1. `redis-cli.exe` 只作排障用（2.4 MB 里的 1 MB），若嫌大可只留 server + dll（7.2 MB）。
2. 内置实例不落盘：重启丢通知队列/PPT 进度/会话缓存 —— 与"缓存语义"一致，已写入 README。
3. 端口被非 Redis 进程占用时目前只降级并记原因；如需自动换端口，要动 `REDIS_URL` 语义，本期不做。
4. `tools/redis/data`、`logs` 会在首次运行时创建；clone 后为空目录属正常。

---

## 六、现场故障与加固（2026-10-27，用户实机）

**现象**：用户在自己 PowerShell 里 `python.exe -m uvicorn src.main:app --port 8000`，日志
`Redis 不可用，已降级：内置 Redis 启动即退出（code=3221225781）…`，服务能起但四条链路全哑。

**定位**：`3221225781` = `0xC0000135` = `STATUS_DLL_NOT_FOUND`（装载阶段就失败）。
当时 `redis-server.exe` 的 stdout/stderr 被丢进 `DEVNULL`，`_log_tail` 读到的只是**上一轮**（02:48）的旧日志，
所以看不出缺哪个库；本机复跑（bash / cmd / PowerShell 三种 shell、全新目录）都正常，属**偶发/环境相关**的装载失败。

**加固（4 项）**：

| 项 | 内容 |
|---|---|
| 依赖补齐 | 追加 `msys-crypto-3.dll`（5.0 MB）+ `msys-ssl-3.dll`（0.9 MB）→ 目录 14.1 MB，消除"TLS 依赖缺库"这一整类装载失败 |
| 错误可见 | 子进程 stdout/stderr 追加到 `logs/redis.stderr.log`；失败时把该文件尾部塞进日志（缺哪个 DLL 会直接写在这里） |
| 退出码翻译 | `0xC0000135` 缺 DLL / `0xC000007B` 位数不符 / `0xC0000142` DLL 初始化失败，中文化提示 |
| 自动重试 | **装载类失败**（进程 1s 内退出且属于上述退出码）自动重试 1 次（首次执行被杀软扫描占用是常见诱因），仍失败才降级 |

**验证**：`ensure()` 实机拉起成功（4 个依赖齐全，`ping=True`）；单测加到 19 例（新增重试 1 次 / 不重试普通失败 / 退出码提示齐全）。
