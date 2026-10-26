# 05 缓存服务器（Redis）集成 · 裁定

> 裁定只写结论与依据；方案正文在 `../v2.0规划文档.md` §05，调研与实测在 `research/05-Redis集成方案.md`。
> 本轮裁定依据：2026-10-27 用户拍板（内置 7.4.3 / 默认自动拉起 / 内置实例不设密码）+ 同日两套 Windows 构建真机实测。

---

## 裁定 1（2026-10-27）：内置 **Redis 7.4.3（msys2）最小集 8.2 MB**，随仓库分发

| 项 | 内容 |
|---|---|
| **分叉** | ① 内置 tporadowski 5.0.14.1（2.4 MB，2018 停更）；② 内置 redis-windows 7.4.3（8.2 MB）；③ 仓库只放下载脚本（0 MB）；④ Memurai 服务；⑤ WSL2/Docker；⑥ 去 Redis 化（diskcache/SQLite）。 |
| **裁定** | **②**。`team/tools/redis/` 放 3 个文件：`redis-server.exe`（4.2 MB）、`redis-cli.exe`（1.0 MB）、`msys-2.0.dll`（3.2 MB），另加 `README.md`（来源/版本/SHA256/许可/替换方法）、`conf/`（不放也行，参数走命令行）、`data/`、`logs/`（均 gitignore）。 |
| **依据** | 实测两套都能在 **0.44s** 起来且命令全覆盖；7.4.3 是当前维护线（5.0 已停更、无安全补丁），体积 8.2 MB 相对已入库的 OfficeCLI 31.96 MB 可接受；Memurai 许可不许分发、Valkey 无官方 Windows 构建、WSL2/Docker 比装 Redis 更重；去 Redis 化要重写 4 个 service 且丢跨进程语义。 |
| **代价（已知）** | ① 非官方社区构建 → 固定 SHA256 并在 README 写明出处与校验方式；② 部分环境 Defender 可能误报，README 给出放行说明；③ msys2 构建依赖 `msys-2.0.dll`，**缺 DLL 时启动会失败**，必须由启动器捕获并给出明确报错。 |
| **兜底** | 保留 `scripts/get_redis.py`（下载 10.4 MB zip → 校验 SHA256 → 只解 3 文件；支持 `--proxy` / 离线 `--zip`）。仓库体积紧张时可删二进制只留脚本，行为不变。 |

---

## 裁定 2（2026-10-27）：启动三态 —— **复用外部 → 拉起内置 → 降级**，默认自动拉起

| 项 | 内容 |
|---|---|
| **裁定** | `lifespan` 启动阶段（**早于检索预热**）调用 `redis_boot.ensure()`，按序：① 探测成功 → `复用外部 Redis host:port`，不启第二份；② 探测失败且 `REDIS_AUTOSTART=true` 且 `REDIS_BIN_DIR` 有可执行文件 → 拉起内置实例，等 `REDIS_BOOT_TIMEOUT=5s`；③ 仍失败 → 保持既有四处 `try/except` 降级，日志写明原因。 |
| **依据** | 用户拍板「默认自动拉起（clone 即用）」；现有四处已有降级，缺的只是"能自己起来"。 |
| **顺序约束** | 必须在 RAG 预热线程之前（预热里 `conv_rag_cache` 会摸 Redis）；且必须在**任何 service 第一次用 redis 之前** —— 由裁定 5 的惰性客户端保证。 |
| **不抢端口** | 端口被非 Redis 进程占用时 `bind` 失败 → 记录失败原因并降级，**不换端口、不改配置**。 |

---

## 裁定 3（2026-10-27）：内置实例**不设密码**，只 `bind 127.0.0.1`；不落盘、限内存

| 项 | 内容 |
|---|---|
| **裁定** | 拉起参数固定为：`--port <REDIS_URL 端口> --bind 127.0.0.1 --protected-mode yes --save "" --appendonly no --dir <tools/redis/data> --logfile <tools/redis/logs/redis.log> --maxmemory 256mb --maxmemory-policy allkeys-lru`，**不带 `--requirepass`**。 |
| **依据** | 用户拍板「内置实例不设密码，只 bind 127.0.0.1」；本机自用、单用户、只监听内环，密码价值有限而配置成本高（`.env` 一处就够）。 |
| **代价** | 同机其它进程可无密码访问 6380 → 已由 `bind 127.0.0.1` 收窄到本机；`maxmemory + allkeys-lru` 防止缓存无界增长把机器拖垮（通知/进度都是短生命期数据，被 LRU 淘汰可接受）。 |
| **不落盘** | 全是缓存/队列语义：重启丢通知队列、丢进度、丢会话缓存都可接受；`data/` 只作为 Redis 工作目录存在。 |

---

## 裁定 4（2026-10-27）：**双探针**——带 auth 与去 auth 两种 URL 都要试

| 项 | 内容 |
|---|---|
| **问题** | `.env` 是 `redis://:123456@127.0.0.1:6380/0`（带密码），而内置实例按裁定 3 **无密码**。若只用带密码 URL 探测：① 客户端带 `AUTH` 连无密码实例 → `ERR Client sent AUTH, but no password is set`；② 第二个后端启动时会误判"外部不可用" → 试图再拉一份 → 端口占用失败 → 直接降级（**双实例场景会坏掉**）。 |
| **裁定** | `ensure()` 依次探测两个变体：`as_is`（原 URL，含密码）→ `no_auth`（同 host/port/db，去掉 user/password）。任一成功即复用；命中 `no_auth` 时把**运行时** `settings.REDIS_URL` 覆写为无 auth 版本并 `get_redis.cache_clear()`。**不改 `.env`**，下次若外部实例（带密码）恢复，`as_is` 先命中，行为回到原样。 |
| **依据** | 实测：Redis 7.4.3 无密码实例对带密码客户端返回上述 `ERR`；双实例共存是本项目真实场景（后端 + 探针脚本）。 |
| **日志** | 三态必须可区分：`复用外部（auth）` / `复用外部（无 auth）` / `内置已启动 pid=… port=…` / `降级：<原因>`。 |

---

## 裁定 5（2026-10-27）：`redis_client` 改**惰性代理**，四个 service 零改动

| 项 | 内容 |
|---|---|
| **问题** | `src/core/redis_client.py` 现在是 `redis_client = get_redis()`（**import 时**就按当时的 `REDIS_URL` 建池），而 URL 覆写发生在更晚的 `lifespan`。 |
| **裁定** | 改成 `class _LazyRedis: __getattr__ → get_redis()`，模块级 `redis_client = _LazyRedis()`；`get_redis()` 保留 `lru_cache`，`ensure()` 覆写 URL 后调用 `get_redis.cache_clear()`。四个 service（`conv_rag_cache` / `notification_service` / `ppt_progress` / `ping_redis` 调用方）写法不变。 |
| **依据** | 备选"import 期启动内置实例"会给测试与脚本引入启动进程的副作用；惰性代理零侵入。 |
| **兼容** | `redis_client.ping()` / `pipeline()` 等全部经 `__getattr__` 透传，现有代码无需感知。 |

---

## 裁定 6（2026-10-27）：只启自己拉起的实例，退出必清理

| 项 | 内容 |
|---|---|
| **裁定** | `ensure()` 记录 `self._proc`（`subprocess.Popen`）与 pid；`lifespan` 收尾 `redis_boot.stop()`：`terminate()` → 等 3s → `kill()`。**外部实例绝不触碰**（没有 pid 就不动）。`atexit` 注册同样的 stop 兜底（异常退出/`--reload` 子进程）。 |
| **依据** | 不误杀用户自己装的 Redis（本机历史上就有一份）；缓存语义下没有优雅关机的必要（不落盘）。 |
| **验收** | Ctrl+C 后 `tasklist | findstr redis-server` 为空、端口释放；外部实例场景下 pid 仍在。 |

---

## 裁定 7（2026-10-27）：配置只加 3 项，`REDIS_URL` 仍是唯一事实源

| 配置 | 默认 | 说明 |
|---|---|---|
| `REDIS_AUTOSTART` | `true` | 探测不到时是否拉起内置实例 |
| `REDIS_BIN_DIR` | `tools/redis` | 内置可执行文件目录（相对项目根；也接受绝对路径） |
| `REDIS_BOOT_TIMEOUT` | `5.0` | 拉起后等待 `PING` 的秒数 |

端口 / 密码 / db **只从 `REDIS_URL` 解析**，不新增第二套配置（避免 `.env` 两处漂移）。`.env.example` 同步补这三项。

---

## 裁定 8（2026-10-27）：失败一律**降级不阻塞**，错误日志必须能指导修复

| 情形 | 行为 |
|---|---|
| `tools/redis/` 不存在 / 缺 DLL | 不拉起，日志：`内置 Redis 缺失（缺 <文件>），可运行 scripts/get_redis.py 或手动放入 tools/redis/` |
| 端口被占（非 Redis） | 不抢端口，日志带 `bind` 失败原因 |
| 拉起后 5s 内 ping 不通 | 日志带子进程退出码与 `logs/redis.log` 尾部 20 行 |
| 外部实例返回 `AUTH` 错 | 日志提示「外部 Redis 无密码或密码不符（REDIS_URL 第 N 段）」并继续走 no-auth 探针 |

启动永远不因 Redis 失败而中断（与现有降级口径一致）。

---

## 裁定 9（2026-10-27）：测试只测"决策与生命周期"，不测真实二进制

| 项 | 内容 |
|---|---|
| **单元** | `backend/tests/test_redis_boot.py`（≥8 例）：URL 变体生成（带/不带 auth、db 保留）、探针命中 as_is/no_auth 的覆写行为、`REDIS_AUTOSTART=false` 不拉起、缺二进制不拉起且给原因、拉起失败降级、`stop()` 只杀自己 pid、端口解析、`_LazyRedis` 透传。全部用 monkeypatch + 假 Popen/假 socket，**不走真进程**。 |
| **真机** | 实现完成后单独做一次（见裁定 10 验收）。 |
| **回归** | 既有 104 用例必须全绿（Redis 缺失时四处降级已有覆盖）。 |

---

## 裁定 10（2026-10-27）：验收 10 条硬指标

1. 本机停掉全部 Redis（6379/6380 均无监听）→ 起后端 → 日志 `内置 Redis 已启动 pid=… port=6380`，`PING` 通；
2. 四链路实测：通知 `rpush/lrange`、PPT 进度 `hset/hgetall`、会话缓存 `setex→get`（TTL 正确）、任务锁 `set nx`；
3. 外部 Redis（带密码）已在跑 → 日志 `复用外部（auth）`，`tasklist` 只有一份 `redis-server.exe`；
4. 外部 Redis（无密码）在跑 → 日志 `复用外部（无 auth）`，不拉起、不覆写成有密码连接；
5. 内置实例已由上一轮拉起（无密码）→ 第二个后端启动命中 `no_auth` 探针 → `复用`，不双启；
6. Ctrl+C → 内置实例退出、端口释放；**外部实例场景下外部进程仍在**；
7. `REDIS_AUTOSTART=false` + 无外部 → 服务正常起、四链路降级、日志明确；
8. `tools/redis/` 指向空目录 → 不拉起、给获取提示、服务正常；
9. 端口 6380 被非 Redis 进程占用 → 不抢端口、日志带失败原因、降级；
10. `.gitignore` 正确：`tools/redis/*.exe`、`*.dll`、`README.md` 入库；`data/`、`logs/` 不入库；`git check-ignore` 逐条验证。

---

## 裁定 11（2026-10-27）：**不动**四处 service 与既有降级口径

四个使用点（`conv_rag_cache` / `notification_service` / `ppt_progress` / `ping_redis`）保持原样：命令不变、`try/except` 不变、TTL/键名不变。本次改的是"Redis 从哪来"，不是"怎么用"。

---

## 裁定 12（2026-10-27）：文档同步口径

- `README` 环境要求：由「需要本机 Redis」改为「**无需预装 Redis**（内置实例自动拉起，探测到外部实例则优先复用）」，并在阅读指南补 `src/core/redis_boot.py` 一步（含思考题：为什么先探测再拉起？为什么内置实例要无密码 + `bind 127.0.0.1`？）；
- `.env.example`：+3 配置；
- `交接文档`：05 段一句话 + 已收官/进行中状态；
- `Done/05-Redis集成·实现记录.md`：真机 10 条验收的实测数据。

---

## 实现期补充 13（2026-10-27）：加 Windows **Job Object**，硬杀父进程也不留孤儿

| 项 | 内容 |
|---|---|
| **发现** | 实现后真机验收：`Popen.terminate()`（等价 `taskkill /F`）在 Windows 是 `TerminateProcess`，**不会**走 uvicorn 的 lifespan 收尾 → `stop()` 不执行 → `redis-server.exe` 留在 6380 上占端口（实测第一次验收就复现）。 |
| **裁定** | 拉起后立刻 `CreateJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 并 `AssignProcessToJobObject`；Job 句柄模块级常驻，父进程一死句柄关闭 → 组内子进程被系统回收。`stop()` 仍是正常路径，Job 只是兜底。 |
| **依据** | 验收要求"退出干净"，而用户实际会直接关控制台/任务管理器结束；仅靠 atexit 不够。 |
| **代价** | 仅 Windows 生效（非 Windows 走 `stop()`）；绑定失败只 `debug` 日志，不影响运行。 |

---

## 实现期补充 14（2026-10-27）：`get_redis()` 首次连接**自愈**（服务端无密码时去 auth）

| 项 | 内容 |
|---|---|
| **发现** | 裁定 4 的双探针只在 `lifespan` 里跑。**不进 lifespan 的进程**（探针脚本、离线任务、单测）首次用 `redis_client` 仍按带密码 URL 连接 → 对内置实例报 `AUTH ... without any password configured`，四条链路全部降级。 |
| **裁定** | `get_redis()` 首次 `PING` 若命中"服务端未设密码"这一种错误（`without any password` / `no password is set`），自动去掉 auth 重建连接并记一行日志；`WRONGPASS`（密码真错）**不吞**，照常抛错暴露配置问题。 |
| **依据** | 让所有进程行为一致；同时保留"配置错就要报错"的可观测性。 |
| **代价** | 每进程首次连接多一次 `PING`（本机 ~1ms）。 |

---

## 实现期补充 15（2026-10-27）：现场 `0xC0000135` 装载失败 → 依赖补齐 + 错误可见 + 重试

| 项 | 内容 |
|---|---|
| **现象** | 用户实机启动报「内置 Redis 启动即退出（code=3221225781）」并降级；`3221225781`=`0xC0000135`（缺 DLL）。 |
| **根因** | 装载阶段失败，而当时子进程输出去向是 `DEVNULL`、失败信息里读到的是上一轮旧日志 → 无从定位；本机三种 shell 复跑均正常，属偶发（杀软扫描/依赖缺失）而非代码逻辑错误。 |
| **裁定** | ① 依赖补齐：`msys-crypto-3.dll` + `msys-ssl-3.dll` 一并分发（目录 8.2 MB → **14.1 MB**）；② 子进程 stdout/stderr 落 `logs/redis.stderr.log` 并在失败信息里带尾部；③ 退出码中文化（缺 DLL / 位数不符 / 初始化失败）；④ **装载类失败自动重试一次**（0.8s），普通失败不重试。 |
| **代价** | 仓库多 5.9 MB；每次启动多写一个小日志文件（已在 .gitignore）。 |
