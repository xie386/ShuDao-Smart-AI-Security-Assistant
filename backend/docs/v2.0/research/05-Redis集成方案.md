# 05 · 缓存服务器（Redis）集成 —— 前置调研

> 状态：**调研完成，待拍板**（2026-10-27）
> 目的：消除「必须先在本机另一处装好 Redis」这个隐形前置条件，做到 clone 即用；同时评估「不要 Redis」是否更划算。
> 结论先行：**推荐内置 Redis 7.4.3 最小集（8.2 MB）+ 一键下载脚本兜底**，不自研去 Redis 化。

---

## 1. 现状取证

### 1.1 依赖面（很小）

| 文件 | 用到的命令 | 用途 |
|---|---|---|
| `src/core/redis_client.py` | `Redis.from_url(..., protocol=2)` | 连接池（已为兼容 Redis<6 显式 RESP2） |
| `src/services/conv_rag_cache.py` | `setex` / `expire` / `get` | 会话级子块缓存（TTL 600s 滑动） |
| `src/services/notification_service.py` | `rpush` / `lrange` | 每用户通知队列 |
| `src/services/ppt_progress.py` | `hset` / `hgetall` / `expire` / `set(nx=True)` | PPT 进度 hash + 任务锁 |

只有 4 个文件、7 类命令，**没有** Stream / Pub-Sub / 事务 / Lua / 持久化要求；也就是说：候选方案只要覆盖 `SET/GET/TTL/LIST/HASH/SETNX` 就够。

### 1.2 降级行为（已实现，但等于功能缺失）

四处全部 `try/except`：Redis 不可用 → 通知不弹、PPT 进度条无、会话缓存不生效、锁放行（并发不受保护）。**服务不崩，但这些功能实际是哑的。**

### 1.3 真实痛点

- 配置默认 `redis://:123456@127.0.0.1:6380/0`，端口 6380（非默认 6379）—— 说明现在用的是本机另行安装/另配的实例；
- 本次取证时 `6379 / 6380 / 6381` **全部无监听**（也就是说：这台机器上 Redis 现在根本没开）；
- `PATH` 里没有 `redis-server` / `redis-cli`；项目目录内也没有任何 Redis 产物；
- 换机器 / 重装 / 换个组员，都要自己去装一个 Redis 并改 `.env` —— 与 02 大点「OfficeCLI 随仓库分发」解决的是同一类问题。

---

## 2. 候选方案实测（本轮真跑，不是查文档）

### 2.1 内置 Redis for Windows（两个候选）

| | tporadowski/redis 5.0.14.1 | redis-windows 7.4.3（msys2） |
|---|---|---|
| 发布 zip | 12.6 MB | 10.4 MB |
| **最小可运行集** | **2.4 MB**（`redis-server.exe` 1.77 + `redis-cli.exe` 0.61 + `EventLog.dll`） | **8.2 MB**（`redis-server.exe` 4.2 + `redis-cli.exe` 1.0 + `msys-2.0.dll` 3.2） |
| 依赖 | 无（原生 MSVC 构建） | 需 `msys-2.0.dll`（TLS 才需另两个 DLL，可省） |
| 启动耗时（本机实测） | 0.44s | 0.44s |
| 协议 | RESP2（redis-py 默认 RESP3 会发 `HELLO 3` → 报错；**项目已设 `protocol=2`，兼容**） | RESP2/RESP3 均可 |
| 命令覆盖（本次实测） | `SET EX / GET / TTL / RPUSH / LRANGE / HSET / HGETALL / SET NX` 全部通过 | 同上全部通过 |
| 维护状态 | Redis **5.0（2018，已停止维护）** | Redis 7.4（较新；构建方为社区 MSYS2 移植，非官方） |
| 许可 | BSD-3 | BSD-3 |

另：`microsoftarchive/redis` 3.2.100（zip 5.2 MB，2016 年）与 `redis-windows 8.10.2`（zip 13.9 MB）也测了可达性，前者太旧、后者体积更大，不推荐。

### 2.2 其它路线（未打包验证，按外部事实记录）

| 方案 | 体积 | 结论 |
|---|---|---|
| Memurai（Redis 官方 Windows 合作伙伴） | 安装包 20 MB+，装成 Windows 服务 | 商业产品，开发者版免费但**许可不允许随仓库分发**；还要装服务，违背「clone 即用」 |
| WSL2 / Docker Desktop | 0（用已有环境） | 要求机器预装 WSL2 / Docker，比装 Redis 更重；Windows 家庭版还常缺虚拟化 |
| Valkey | — | **官方没有 Windows 构建**，排除 |
| 去 Redis 化（`diskcache` / SQLite 自建） | 0 | 技术上可行（我们只需要 KV+TTL+List+Hash+SETNX），但要重写 4 个 service、丢掉「跨进程/跨机共享」，且 PPT 锁、通知队列的语义要自己保证 —— **改动大、收益小** |

---

## 3. 推荐方案：内置 7.4.3 最小集 + 下载脚本兜底

### 3.1 目录与体积

```
team/tools/redis/
├── redis-server.exe      (4.2 MB)
├── redis-cli.exe         (1.0 MB)
├── msys-2.0.dll          (3.2 MB)
├── README.md             (来源 / 版本 / sha256 / 许可 / 如何替换)
├── conf/redis.conf       (随项目：bind 127.0.0.1、save ""、appendonly no、dir 指 data/)
├── data/                 (空目录，gitignore，不落盘)
└── logs/                 (空目录，gitignore)
```

合计 **8.2 MB**（对照：`tools/officecli/officecli.exe` 31.96 MB 已在仓库里，A 方案可接受）。若你希望仓库零体积，则**只提交 `README.md` + `scripts/get_redis.py`**，首次启动时自动下载解压（见 3.3）。

### 3.2 自动启动（`src/core/redis_boot.py`）

启动顺序（`lifespan` 里，早于检索预热）：

1. `PING` `REDIS_URL`（超时 0.5s）→ 通：`复用外部 Redis 127.0.0.1:6380`，什么都不做；
2. 不通且 `REDIS_AUTOSTART=true` 且 `tools/redis/` 有可执行文件 → 拉起子进程：
   - 端口/密码**从 `REDIS_URL` 解析**（不新增第二套配置，避免 `.env` 两处漂移）；
   - 固定 `--bind 127.0.0.1 --save "" --appendonly no --dir tools/redis/data`（纯缓存语义，不落盘）；
   - 日志写 `tools/redis/logs/redis.log`；最多等 5s `PING` → 成功记日志「内置 Redis 已启动 pid=…」；
3. 仍不通 → 保持现有降级（通知/进度/缓存哑掉），日志明确写「Redis 不可用，已降级」；
4. 进程退出（`lifespan` 收尾 / Ctrl+C）→ `terminate()` 内置实例（外部实例**不动**）。

### 3.3 体积兜底：`scripts/get_redis.py`

- 下载 `Redis-7.4.3-Windows-x64-msys2.zip`（10.4 MB）→ 校验发布页 SHA256 → 只解出 3 个文件到 `tools/redis/`；
- 支持 `--proxy`（本机 127.0.0.1:7897 这类）与离线 `--zip <本地路径>`；
- 已存在则跳过；失败给中文提示（「可手动下载放到 tools/redis/」）。

### 3.4 安全与运维

- 只 `bind 127.0.0.1`；沿用 `.env` 里的密码（当前 `123456`，弱密码由部署方自行改强）；
- 不落盘：`save "" `+`appendonly no` —— 通知队列/进度的丢失窗口本来就可接受（这些是 UI 提示）；
- 端口冲突：`REDIS_URL` 指向的端口已被别的东西占用时，`PING` 失败 → 拉起会失败 → 记录原因并降级，**不抢端口**；
- 多实例：两个后端同时启动，第二个 `PING` 通 → 直接复用，不会双启。

### 3.5 配置项（草案）

| 配置 | 默认 | 说明 |
|---|---|---|
| `REDIS_URL` | 不变 | 唯一事实源（端口/密码/DB） |
| `REDIS_AUTOSTART` | `true` | 探测不通时是否拉起内置实例 |
| `REDIS_BIN_DIR` | `tools/redis` | 内置可执行文件目录（相对项目根/backend） |
| `REDIS_BOOT_TIMEOUT` | `5.0` | 启动等待秒数 |

---

## 4. 验收口径（草案，8 条）

1. **零外部依赖**：本机停掉所有 Redis、清空 6379/6380，启动后端 → 日志出现「内置 Redis 已启动」，`PING` 通；
2. **复用优先**：外部 Redis 已在跑 → 日志出现「复用外部 Redis」，不启动第二份，`tasklist` 只有一份 `redis-server.exe`；
3. **功能可用**：通知收发、PPT 进度写入/读取、会话缓存 `setex→get`、任务锁 `set nx` 四条链路实测通过；
4. **退出干净**：Ctrl+C 后 `redis-server.exe` 不残留，端口释放；
5. **降级不崩**：把 `REDIS_BIN_DIR` 指向空目录 + `REDIS_AUTOSTART=false` → 服务正常起，四链路静默降级，日志有明确提示；
6. **配置单一**：改 `REDIS_URL` 端口/密码，内置实例自动跟随，不需要第二处配置；
7. **体积可接受**：`tools/redis/` ≤ 9 MB；`git check-ignore` 确认二进制会入库、`data/` 与 `logs/` 不入库；
8. **文档同步**：README「环境要求」改为「无需预装 Redis」；`.env.example` 补 3 个新配置；交接文档记录。

---

## 5. 拍板结果（2026-10-27）

| 议题 | 决定 |
|---|---|
| 内置形态 | **B：内置 Redis 7.4.3（msys2）最小集，8.2 MB**（`redis-server.exe` + `redis-cli.exe` + `msys-2.0.dll`） |
| 自动启动 | **默认自动拉起**：`PING REDIS_URL` 不通即启动内置实例（clone 即用） |
| 内置实例密码 | **不设密码，只 `bind 127.0.0.1`** |

### 由这三条决定引出的两个技术点（进实现前必须落定）

1. **连接串的 auth 与实例状态要一致**：`.env` 现在是 `redis://:123456@127.0.0.1:6380/0`，
   而内置实例按本决定不设密码。若客户端仍带 `AUTH`，服务端会回
   `ERR Client sent AUTH, but no password is set`。
   处理：`redis_boot.ensure()` 在**拉起内置实例成功后**，把**运行时** `settings.REDIS_URL` 覆盖为
   `redis://127.0.0.1:<port>/0`（去掉 auth）并 `get_redis.cache_clear()`；**不改 `.env`**，
   下次若外部 Redis 恢复，探测逻辑仍按原 URL（带密码）复用。
   复用外部实例时保持原 URL 不动（含密码）。
2. **`redis_client` 目前是 import 时实例化的**（`redis_client = get_redis()`），
   而 `settings.REDIS_URL` 的覆盖发生在 `lifespan`（更晚）。若不动它，服务仍会用旧 URL 建池。
   处理（推荐 A）：把 `redis_client` 改成**惰性代理**——
   `class _LazyRedis: __getattr__ → get_redis()`，首次命令时才建池；
   这样四个 service 的 `redis_client.xxx(...)` 写法零改动。
   （备选 B：在 `redis_client` 模块内 import 期调用 `redis_boot.ensure()`，但会给测试引入启进程副作用，不推荐。）

### 仍需在实现期确认的小项

- 端口冲突（`REDIS_URL` 端口被非 Redis 进程占用）：拉起会 `bind` 失败 → 记录原因并降级，不抢端口；
- 内置实例的 `data/`、`logs/` 目录 gitignore；
- `tools/redis/README.md` 写清来源、版本、SHA256、许可与替换方法。

