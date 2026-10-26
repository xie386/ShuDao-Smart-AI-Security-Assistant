# 05 缓存服务器（Redis）集成 · 逐文件带读

> 配套：`05-Redis集成·实现记录.md`（做了什么/验收）、`../Verdict/05-Redis集成方案.md`（裁定）
> 读法建议：先看 `redis_boot.ensure()` 的三态，再看 `redis_client` 为什么必须惰性，最后看 `main.py` 的两行接入。

---

## 1. `src/core/redis_boot.py`（新增，268 行）

| 区块 | 作用 | 关键点 |
|---|---|---|
| `bin_dir()` | 定位内置可执行文件目录 | 相对路径先在 `team/`（`tools/` 所在层）找，再退 `backend/`；绝对路径直用 |
| `_url_variants(url)` | 生成探测候选 | `as_is` 永远第一；只有 URL 带 auth 时才追加 `no_auth`（同 host/port/path） |
| `_probe(url)` | 单次 `PING` | 区分 `auth`（服务端无密码 / 密码不符）与 `conn:xxx`（连不上），供日志与决策使用 |
| `_override_url(url)` | **运行时**改连接串 | 只改内存里的 `settings.REDIS_URL` + `redis_client.reset()`，**不动 `.env`**；日志脱敏 |
| `_spawn(url)` | 拉起内置实例 | 参数固定（`bind 127.0.0.1`、`save ""`、`appendonly no`、`maxmemory 256mb allkeys-lru`）；先检查 `redis-server.exe` + `msys-2.0.dll`；等 `REDIS_BOOT_TIMEOUT`，失败带日志尾部 |
| `_bind_kill_on_close(proc)` | Windows Job Object | `KILL_ON_JOB_CLOSE`：父进程被硬杀时子进程一起回收（`terminate()` 是硬杀，lifespan 不会执行） |
| `stop()` | 只收自己拉起的实例 | 无 `_proc` 就返回；已退出就不动；`terminate → wait 3s → kill` |
| `ensure()` | 对外唯一入口 | 复用（as_is/no_auth）→ 自动拉起 → 降级；返回 `{state, kind/pid, message}` |
| `status()` | 调试/健康检查 | `state + bin_dir + pid` |

**为什么双探针是必需的**：`.env` 带密码、内置实例无密码。只探 `as_is` 会有两个坑 —— ① 客户端带 `AUTH` 打无密码实例直接报错；
② 第二个后端因此误判"外部不可用"，尝试再拉一份 → 端口占用 → 直接降级（双实例场景整段功能哑掉）。

---

## 2. `src/core/redis_client.py`（改）

```python
redis_client = _LazyRedis()          # 之前是 get_redis()，import 时就建池
```

- `_LazyRedis.__getattr__` 透传到 `get_redis()`，所以四处 service 的 `redis_client.push(...)` 写法一行没改；
- `reset()` = `get_redis.cache_clear()`，在 URL 被覆写后清池；
- `get_redis()` 首次 `PING` 的**自愈**：只在"服务端没设密码"时报错才去掉 auth 重建；`WRONGPASS` 照常抛。
  这一条是为**不进 lifespan 的进程**（探针脚本/离线任务/单测）准备的，否则它们永远连不上内置实例。

---

## 3. `src/core/config.py`（改）

```python
REDIS_AUTOSTART: bool = True        # 探测不到是否拉起
REDIS_BIN_DIR: str = "tools/redis"  # 相对 team/ 或绝对路径
REDIS_BOOT_TIMEOUT: float = 5.0     # 等 PING 的秒数
```

端口/密码/DB **仍然只从 `REDIS_URL` 解析**，避免 `.env` 两处配置漂移。

---

## 4. `src/main.py`（改，两处）

```python
# lifespan 开头：早于 RAG 预热（预热里的会话缓存会摸 Redis）
from src.core import redis_boot
_redis_state = redis_boot.ensure()

try:
    yield
finally:
    redis_boot.stop()      # 只收自己拉起的
```

`ensure()` 外面包了 `try/except`：启动器自身异常也不能拖垮服务（降级即可）。

---

## 5. `scripts/get_redis.py`（新增，150 行）

`--zip` 离线安装 / `--proxy` 走代理 / `--force` 覆盖；流程是"下载 → 校验整包 SHA256 → 只解 3 个文件 → 逐文件再校验 → 写盘"。
上游包结构变了（找不到 `redis-server.exe`）会中止并提示，不会写半个文件。

---

## 6. `tests/test_redis_boot.py`（新增，16 例）

覆盖：URL 变体生成（带/不带 auth、默认端口、密码脱敏）、复用 `as_is` 不改 URL、复用 `no_auth` 覆写 + 清池、
`AUTH` 错误日志、`REDIS_AUTOSTART=false` 不拉起、缺二进制给 `get_redis.py` 提示、拉起成功标 `internal`、
拉起超时降级、`stop()` 只杀自己 / 跳过已死 / 无实例不报错、`_LazyRedis` 透传 + `reset()`、`status()` 带 bin_dir。
全部用假 probe/假 Popen，**不启真进程**。

---

## 7. `team/tools/redis/`（新增）

3 个文件 + `README.md`：版本、发布页、整包与逐文件 SHA256、许可、启动参数、替换与排障（缺 DLL / Defender 误报 / 端口被占）。
`data/`、`logs/` 由运行期创建，已在 `.gitignore` 忽略；二进制与 README 入库。

---

## 8. 排查手册（真出问题时按这个顺序看）

1. 启动日志 `Redis` 那几行 —— 三态是哪一种；
2. 内置实例起来了吗：`tasklist /FI "IMAGENAME eq redis-server.exe"`、`netstat -ano | findstr 6380`；
3. 起不来：看 `team/tools/redis/logs/redis.log` 尾部（`ensure()` 失败时会把尾部 20 行塞进日志）；
4. 端口被占：先 `netstat` 找占用进程；本项目**不会**换端口强启；
5. 连接报 `AUTH`：确认 `REDIS_URL` 的密码与实例是否匹配（无密码实例会被自愈成去 auth 连接，但 `WRONGPASS` 不会）；
6. 想完全不用内置：`.env` 里 `REDIS_AUTOSTART=false` + 自己的 `REDIS_URL`。
