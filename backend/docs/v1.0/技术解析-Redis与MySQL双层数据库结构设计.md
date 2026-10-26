# 蜀道智能安全助手 — 技术解析：Redis + MySQL 双层数据库结构设计

> 技术分享专题文档。围绕项目实际代码，解析「**MySQL 持久层 + Redis 加速/消息层**」的双层数据库架构，
> 重心放在 Redis 的技术实现上（连接管理、数据结构选型、JSON 序列化、优雅降级），MySQL 侧讲清与 Redis 的分工边界。
> 本文所有代码均来自 `backend/src/` 真实实现，标注了文件与行号，可直接对照阅读。

---

## 一、为什么需要双层数据库

### 1.1 单一数据库的困境

项目核心业务是**隐患闭环管理**：上报 → 派单 → 整改 → 验收，每一步都要写入状态、记录时间线、通知相关人员。如果全部塞进 MySQL：

| 问题 | 说明 |
|---|---|
| **高频写放大** | 每个状态流转要写 hazards + hazard_timeline 两条记录，还要给 N 个相关人员各写一条通知 |
| **临时数据污染** | 通知是「看了就完」的短期数据，放 MySQL 要专门建表 + 写清理任务（DELETE 定时任务） |
| **读延迟** | 通知列表每次查库 + 关联 users 表，用户体验差 |
| **耦合** | 业务状态机（强一致）与通知（可丢失）混在一个事务里，一个失败全部回滚 |

### 1.2 双层分工的设计原则

```
┌─────────────────────────────────────────────────┐
│                   业务请求                        │
│     （上报 / 派单 / 整改 / 验收 / 问答 / PPT）      │
└──────────────────────┬──────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
┌──────────────────┐        ┌──────────────────┐
│  MySQL（持久层）   │        │  Redis（消息+缓存层）│
│                  │        │                  │
│  6 张表           │        │  List 队列        │
│  状态机强一致      │        │  通知消息         │
│  事务 / 外键       │        │  String 缓存（问答）│
│  永久保存          │        │  临时 / 可丢失 / 可重建│
│  历史 / 统计       │        │  按用户隔离        │
└──────────────────┘        │  实时 / 高吞吐      │
   ↑ 业务数据唯一真相        └──────────────────┘
                                ↑ 业务数据的「通知投影 + 热点缓存」
```

**核心原则：MySQL 管「事实」，Redis 管「通知 + 缓存」。**

- **MySQL**：业务数据（用户/隐患/时间线/会话/PPT 任务）的唯一真相源，事务保证强一致；
- **Redis**：① 业务事件触发的**通知消息**（List 队列，天然按用户隔离）；② **RAG 检索分块缓存**（String + TTL 10 分钟，热点问答免检索、省 LLM 调用开销）。两者都是可重建/可丢失的加速层。

> 一句话：**状态机走 MySQL 事务，通知与热点问答走 Redis** —— 通知在 hazard_service 联动（见第四节），问答缓存挂在 chat 路由（见 3.6）。

---

## 二、MySQL 层设计（持久层）

### 2.1 六张表总览（`database/schema.sql`）

| 表 | 职责 | 核心字段（要点） |
|---|---|---|
| `users` | 用户 | `role` enum('admin','safety_officer','employee') 三角色 RBAC |
| `hazards` | 隐患主表 | `status` enum 五态状态机；`reporter_id` / `assignee_id` 外键 |
| `hazard_timeline` | 状态流转时间线 | `action` + `operator_id` + `note`，每步流转留痕 |
| `conversations` | AI 问答会话 | `user_id` 外键 |
| `chat_messages` | 聊天消息 | `role` + `content` + `references`（回答引用来源）+ `rating`（点赞/踩） |
| `ppt_tasks` | PPT 生成任务 | `status` enum('草稿','生成中','已完成','失败') 四态 |

设计要点：

```sql
-- 状态机字段用 enum 硬约束（schema.sql:30）：非法状态写不进库
`status` enum('待处理','处理中','待验收','已闭环','已驳回') NOT NULL,

-- 外键保证引用完整性（schema.sql:42-43）：上报人/责任人不可能是幽灵用户
CONSTRAINT `hazards_ibfk_1` FOREIGN KEY (`reporter_id`) REFERENCES `users` (`id`),

-- 常用查询列建索引：按上报人/责任人筛隐患是高频路径
KEY `ix_hazards_reporter_id` (`reporter_id`),
KEY `ix_hazards_assignee_id` (`assignee_id`),
```

> 字符集 `utf8mb4` + 排序规则 `utf8mb4_0900_ai_ci`：支持 emoji（前端消息里的 📋✅ 图标）与中文全文检索。

### 2.2 状态机由 service 强制流转（不是前端说了算）

`src/services/hazard_service.py` 定义了合法流转表，非法流转一律 400：

```python
# hazard_service.py:36（合法状态流转表：当前状态 -> 允许的下一个状态集合）
VALID_TRANSITIONS: dict[str, set[str]] = {
    "待处理": {"处理中"},          # H04 派单
    "处理中": {"待验收"},          # H05 整改反馈
    "待验收": {"已闭环", "已驳回"},  # H06 验收通过/驳回
    "已驳回": {"处理中"},          # 重新派单/处理
}
```

每次流转 `_ensure_transition` 校验 + 写时间线（`hazard_timeline`），**MySQL 事务保证「改状态 + 写时间线」原子性**：

```python
# hazard_service.py:163-168（派单示例：改状态 + 写时间线同事务）
hazard.assignee_id = data.assignee_id
hazard.status = "处理中"
HazardService._timeline(db, hazard.id, "派单", operator["id"], note=...)
db.commit()
```

### 2.3 SQLAlchemy 2.x 会话管理（`src/core/db.py`）

```python
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,      # 取连接前先 ping：断线自动剔除，避免拿到死连接
    pool_recycle=3600,       # 1 小时回收连接：防 MySQL wait_timeout 断连
)
SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,  # commit 后对象属性仍可读（否则要重新查）
)

def get_db():                # FastAPI 依赖：请求级会话，自动关闭
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

> 注意：**应用不自动建表**——表结构以 `database/schema.sql` 为准（Docker 部署时由 MySQL 容器首次启动自动执行），ORM 只负责读写，结构一致性靠 `scripts/db_verify.py` 校验。

---

## 三、Redis 层设计（消息层，本文重心）

### 3.1 连接管理（`src/core/redis_client.py`）

```python
@lru_cache
def get_redis() -> redis.Redis:
    # decode_responses=True：直接拿 str，省去手动 .decode()
    # protocol=2：redis-py 8.x 默认 RESP3 会发 HELLO 命令，
    #            老版本 Redis(<6.0) 不支持 → 报 unknown command HELLO，强制降级 RESP2
    client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, protocol=2)
    return client

def ping_redis() -> bool:
    """探测 Redis 可用性：服务未启动返回 False（不抛异常）。"""
    try:
        return bool(get_redis().ping())
    except Exception:
        return False

redis_client = get_redis()   # 模块级单例，全项目共享一个连接池
```

技术点：

| 点 | 说明 |
|---|---|
| **`from_url`** | 从配置串解析连接：`redis://:123456@127.0.0.1:6380/0`（密码 :123456，库 0） |
| **lazy 连接** | redis-py 是懒连接——`from_url` 不真正建连，首次执行命令才连；`ping_redis()` 可优雅探测 |
| **RESP2 兼容** | 老 Redis 不支持 RESP3 的 `HELLO` 命令，`protocol=2` 强制兼容（实测踩坑，见 D2） |
| **命名避免冲突** | 文件叫 `redis_client.py` 而非 `redis.py`：避免直接运行时脚本目录遮蔽第三方库 |

### 3.2 Key 设计：命名空间化 + 按用户隔离

```python
_KEY_PREFIX = "shudao:notify:"   # notification_service.py:16

def _key(user_id: int) -> str:
    return f"{_KEY_PREFIX}{user_id}"   # → shudao:notify:17
```

```
shudao:notify:17   → 用户 17 的通知队列
shudao:notify:18   → 用户 18 的通知队列（互不可见）
```

设计要点：

- **命名空间三段式** `项目:业务:维度`（`shudao:notify:user_id`）——同类 key 前缀统一，`KEYS shudao:notify:*` 可批量管理，未来加缓存/限流业务只需新增前缀；
- **按用户分 key = 天然信息隔离**：每个用户一个 List，`GET /api/notifications` 只读自己的 key，**数据层就杜绝了越权读取**（无需在业务层判断「这条消息是不是我的」）——这正是本项目「非管理员只能看与自己相关消息」的实现基础。

### 3.3 数据结构选型：List 而非 Set / Hash

| 结构 | 为什么不用 | List 的优势 |
|---|---|---|
| **String** | 只能存一条，无法表达「多条通知」 | — |
| **Set** | 无序 + 去重；通知要**保序**（时间序）且可能重复（同隐患多次整改） | — |
| **Hash** | 字段是固定结构；通知是**追加日志**语义 | — |
| **List ✅** | — | 天然**队列**：`RPUSH` 尾部追加、`LRANGE` 顺序读取、`LLEN` 计数、`DEL` 清空；一条命令一个语义 |

四个操作映射到通知业务（`notification_service.py`）：

```python
# ① 推送：RPUSH 尾部入队（push，line 52）
redis_client.rpush(_key(user_id), _dump(message, hazard_id, msg_type))

# ② 读取最近 N 条：LRANGE 负索引取尾部（list_for，line 61）
raws = redis_client.lrange(_key(user_id), -limit, -1)

# ③ 未读数：LLEN 队列长度（count，line 70）
return int(redis_client.llen(_key(user_id)) or 0)

# ④ 清空：DEL 删 key（clear，line 77）
return bool(redis_client.delete(_key(user_id)))
```

> List 的负索引 `-limit, -1` 是点睛之笔：通知列表只关心**最近 50 条**，不用先取全量再截断——Redis 原生支持尾部切片，O(limit) 复杂度。

### 3.4 消息序列化：JSON 字符串承载结构化数据

Redis List 的元素是**字符串**，要存「消息文本 + 关联隐患 id + 类型」三个字段，选择 JSON 序列化：

```python
def _dump(message: str, hazard_id: int | None, msg_type: str) -> str:
    # ensure_ascii=False：中文直接存，可读且省一半空间
    return json.dumps({"message": message, "hazard_id": hazard_id, "type": msg_type}, ensure_ascii=False)

def _parse(raw: str) -> dict:
    """解析通知条目：新格式 dict / 旧格式纯字符串 / 损坏数据 三态兼容。"""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "message" in obj:
            obj.setdefault("type", TYPE_GENERAL)
            return obj
        return {"message": raw, "hazard_id": None, "type": TYPE_GENERAL}   # 旧版纯字符串
    except (ValueError, TypeError):
        return {"message": raw, "hazard_id": None, "type": TYPE_GENERAL}   # 损坏兜底
```

为什么存 JSON 而不是 Redis Hash：

| 方案 | 优点 | 缺点 |
|---|---|---|
| **JSON 字符串** ✅ | 一条消息一个原子值；升级加字段（如 type）天然兼容旧数据；跨语言通用 | 读一条要反序列化（成本可忽略） |
| Hash | 字段级读写 | 旧数据（纯字符串）无法兼容；命令更多；类型结构变更要迁移 |

**消息类型枚举**（前端按 type 分类渲染，`notification_service.py:19-24`）：

```python
TYPE_REPORT = "report"      # 接收上报 → 管理员
TYPE_ASSIGN = "assign"      # 派发整改任务 → 责任人
TYPE_RECTIFY = "rectify"    # 整改反馈 → 上报人 + 管理员
TYPE_ACCEPT = "accept"      # 验收结果 → 责任人/上报人 + 安全员/管理员
TYPE_FINAL = "final"        # 最终处理结果 → 全员
TYPE_GENERAL = "general"    # 兜底
```

### 3.5 优雅降级：Redis 挂了，业务不挂

这是双层架构最关键的一行设计——**Redis 的所有操作都被 try/except 包裹**：

```python
@staticmethod
def push(user_id: int, message: str, hazard_id: int | None = None, msg_type: str = TYPE_GENERAL) -> bool:
    try:
        redis_client.rpush(_key(user_id), _dump(message, hazard_id, msg_type))
        return True
    except Exception as exc:   # Redis 未启动等
        logger.warning("通知推送失败(user=%s): %s", user_id, exc)
        return False            # 返回 False，调用方不感知
```

list_for / count / clear 同理。**效果**：

- Redis 未启动时，隐患上报/派单/整改/验收**照常完成**（MySQL 事务不受影响），只是通知静默丢失 + 打 warning 日志；
- 前端拿到的通知列表为空、未读数 0——系统降级为「无消息通知版」，而非 500 崩溃；
- 这与 MySQL 的「强一致」形成互补：**业务核心（状态机）不可降级，通知类（可丢失）允许降级**。

### 3.6 Redis 业务扩展：RAG 检索分块缓存（领导优化）

> 背景（2026-08-17 领导建议两轮迭代）：① 初版缓存**完整回答**（TTL 24h，命中秒回）——但用户反馈两个问题：回答千篇一律（无多样性）、且要求命中也要带引用；② 迭代为**缓存答案依据的检索分块**（context + sources，TTL **10 分钟**）——相同问题命中后跳过 RAG 检索，但 **LLM 每次都重新生成**（回答多样性），引用文件始终保留。

**数据结构选型：String + TTL**（与通知的 List 形成互补）：

| 维度 | 通知（List） | RAG 分块缓存（String） |
|---|---|---|
| 数据形态 | 追加日志（多条） | 单值（问题 → 检索结果 JSON） |
| 读模式 | 取最近 N 条 | 按 key 精确取整条 |
| 过期 | 无（手动清空） | **`SETEX` TTL 10 分钟自动过期**（领导要求：分块缓存时限） |
| 命令 | RPUSH/LRANGE/LLEN/DEL | GET / SETEX |

**缓存内容**（JSON 承载两份数据）：

```json
// shudao:rag_cache:<md5(question)> 的值
{"context": "[来源: 文档#章节]\n父块文本...", "sources": [{"doc": "...", "doc_type": "法规/制度", "chapter": "...", "source": "knowledge_src/laws/..."}]}
```

- `context`：RAG 检索命中的**父块拼接文本**（注入 LLM 的上下文）——**不是最终答案**，所以 LLM 每次基于同一分块重新组织语言，回答天然多样；
- `sources`：引用元数据（A05/A06）——命中缓存时**照样落库 refs**，前端彩色引用标签始终显示。

**Key 设计**：`shudao:rag_cache:<md5(question)>`——问题 md5 做 key，精确命中，定长无中文：

```python
# chat.py（领导优化：RAG 分块缓存，Redis 优雅降级）
_QA_CACHE_PREFIX = "shudao:rag_cache:"
_QA_CACHE_TTL = 10 * 60  # 10 分钟（领导要求）

def _qa_cache_key(question: str) -> str:
    import hashlib
    return f"{_QA_CACHE_PREFIX}{hashlib.md5(question.strip().encode('utf-8')).hexdigest()}"
```

**读写时机**（挂在 SSE 流式问答路由 `chat.py` 的 chat()，与初版的关键差异：**命中不短路，LLM 照常生成**）：

```python
def chat(data, user):
    cache_key = _qa_cache_key(data.question)
    cached = _qa_cache_get(cache_key)          # ① 查缓存：(context, sources) | None

    # ② RAG 检索：命中 → 跳过检索；未命中 → 检索 + 写缓存（TTL 10 分钟）
    if cached is not None:
        context, sources = cached
    else:
        context, sources = hybrid_retrieve(data.question)
        _qa_cache_set(cache_key, context, sources)

    # ③ 无论命中与否：渲染 safety_qa + LLM 流式生成（多样性）+ refs 落库
    refs_json = json.dumps(sources, ensure_ascii=False) if sources else None
    def event_stream():
        for chunk in llm_client.chat_stream(messages, temperature=0.3): ...
        # finally: assistant 消息落库（含 refs）
```

**优雅降级**（与通知同款）：`_qa_cache_get/set` 内部 try/except，Redis 未启动时**静默跳过**——缓存只是加速层，命中失败走完整检索链路，回答照常返回。旧格式（纯字符串答案）的 key 会被 `_qa_cache_get` 判为格式不符 → 视为未命中重新检索（平滑升级）。

**⚠️ 踩坑修复（2026-08-17 用户反馈，初版）**：缓存命中若跳过 LLM 直接返回缓存答案，前端 `send()` 的 finally 会 `loadConvs() + openConv()` 重拉消息列表，若 assistant 消息不落库则回答消失。迭代后的方案**天然规避**：命中只跳过检索、LLM 照常生成、消息统一在 finally 落库——不存在「缓存命中不落库」的路径。教训：**缓存只加速「检索」环节，消息落库是业务事实，任何回答路径都必须留痕**。

**实测数据**（真实 DeepSeek + Redis，2026-08-17，同题两次）：

| 场景 | 耗时 | 说明 |
|---|---|---|
| 第 1 次「高处作业有哪些安全要求？」 | **154.8s** | 未命中：完整 RAG 检索 + LLM 生成 |
| 第 2 次同问题（10 分钟内） | **13.4s** | 命中分块缓存：跳过检索，LLM 重新生成 |
| 回答多样性 | 1716 字 vs 1832 字，开头措辞不同 | **两次回答不一致** = LLM 重新生成 ✅ |
| 引用文件 | assistant 消息 refs=4 个 | 【案例】绿 ×2 + 【法规】蓝 ×2，命中也有引用 ✅ |
| Redis key 状态 | TTL 556s（≈10 分钟） | `shudao:rag_cache:e0973...` 存 context+sources，非答案 |

> 缓存口径说明：按问题**精确匹配**（md5），适合推荐问题/固定问法；换一个字的问法会 miss 走完整链路——「命中优先、绝不误答」的保守设计。TTL 10 分钟是「知识库更新后 10 分钟内旧分块自动失效」与「高频推荐问题免检索」的折中。

---

## 四、双层协作：一次隐患派单的完整数据流

以「管理员派单」为例，看 MySQL 与 Redis 如何协同（`hazard_service.py:157-175`）：

```python
def assign(db, hazard_id, data, operator):
    # ===== MySQL：业务事实写入（同一事务）=====
    hazard = HazardService.detail(db, hazard_id)
    HazardService._ensure_transition(hazard, "处理中", "派单")   # 状态机校验
    hazard.assignee_id = data.assignee_id
    hazard.due_date = data.due_date
    hazard.status = "处理中"                       # 状态机流转
    HazardService._timeline(db, hazard.id, "派单", operator["id"], note=...)  # 时间线留痕
    db.commit()                                    # ★ 事务提交成功 = 业务事实落库

    # ===== Redis：通知投影推送（独立于事务）=====
    NotificationService.push(
        data.assignee_id,                          # 只推给责任人
        f"【隐患整改】您有一条整改任务：{hazard.title}（隐患 #{hazard.id}）",
        hazard_id=hazard.id,                       # 前端点击可跳转隐患详情
    )
    return hazard
```

时序图：

```
管理员点「派单」──▶ MySQL: update hazards set status='处理中'
                          └ update hazard_timeline（时间线）
                          └ commit  ✔ 业务事实确定
                 ──▶ Redis: RPUSH shudao:notify:18  {message, hazard_id, type}
                 ──▶ 前端轮询 /api/notifications → LRANGE 取出 → 未读 +1 → 点击跳转 /hazard/{id}
```

**分层边界清晰**：

| 维度 | MySQL | Redis |
|---|---|---|
| 写入时机 | 事务内（与业务原子） | 事务提交后（独立） |
| 失败影响 | 业务失败，整个操作回滚 | 仅通知丢失，业务不受影响 |
| 数据生命周期 | 永久（历史/统计/审计） | 短期（读了就完，可清空） |
| 一致性 | 强一致（状态机） | 最终一致（通知允许丢失） |
| 读取路径 | SQL 查询 + JOIN | LRANGE 一条命令 |

其他联动点（同类模式，全部「MySQL 改状态 → Redis 推通知」）：

| 业务动作 | MySQL 写入 | Redis 推送对象 |
|---|---|---|
| 隐患上报（`create`） | hazards 新记录 | 全部管理员（report） |
| 派单（`assign`） | status→处理中 + timeline | 责任人（assign） |
| 整改反馈（`rectify`） | status→待验收 + timeline | 上报人 + 管理员（rectify） |
| 验收（`accept`） | status→已闭环/已驳回 + timeline | 当事人 + 安全员/管理员（accept + final） |

---

## 五、为什么消息历史不用 Redis（双层边界的反面验证）

AI 问答的**会话与消息历史**走的是 MySQL（`conversations` / `chat_messages` 表），而非 Redis——对比之下双层边界更清晰：

| 需求 | 通知（Redis） | 会话历史（MySQL） |
|---|---|---|
| 持久性 | 丢了可接受 | **必须永久保存**（用户回看历史问答） |
| 结构查询 | 只看最近 50 条 | 按会话分页翻历史、带引用来源（references JSON）、点赞/踩落库 |
| 数据量 | 短生命周期 | 随使用持续增长 |
| 跨进程共享 | 进程内可用 | 后端多 worker 都要读 |

```python
# chat_messages 存回答引用（chat_service.py）：references 是 RAG 来源的 JSON
# rating 字段：点赞/踩反馈（A07），落 MySQL 保证不丢
class ChatMessage(Base):
    __tablename__ = "chat_messages"
    role: str          # user / assistant
    content: str       # 消息文本
    references: str    # RAG 引用来源（JSON）
    rating: int | None # 点赞=1 / 踩=-1
```

> 判断依据：**数据能重建/能丢 → Redis；必须永久且要查 → MySQL。** 通知是可丢投影，会话历史是用户资产，各归其位。

---

## 六、踩坑记录（真实项目中验证过的坑）

### 6.1 RESP3 HELLO 命令兼容（redis_client.py:25-26）

- **现象**：redis-py 8.x 默认 RESP3 协议，连接老版本 Redis 报 `unknown command 'HELLO'`；
- **修复**：`protocol=2` 强制 RESP2；
- **启示**：第三方库版本升级会悄悄改变协议行为，依赖升级后要回归连通性测试。

### 6.2 List 负索引读最近 N 条

- **初版**可能想 `LRANGE key 0 -1` 全量取出再在 Python 切片——O(N) 且浪费带宽；
- **优化**：`LRANGE key -50 -1` 直接取尾部 50 条，O(limit)。Redis 原生的负索引切片是「读最近」的最优解。

### 6.3 通知 JSON 兼容旧数据

- 早期版本通知是**纯字符串**入队；升级为 `{message, hazard_id, type}` 后，Redis 里残留的旧字符串会解析失败；
- `_parse()` 三态兼容（dict / 旧字符串 / 损坏）保证**线上数据平滑升级**，无需清库。

### 6.4 优雅降级必须「调用方无感」

- 初版可能在 push 失败时向上抛异常 → 导致「Redis 挂了，隐患派单也失败」——违背分层初衷；
- **降级必须调用方无感**：push 返回 bool + warning 日志，**业务事务绝不因 Redis 失败而回滚**。

---

## 七、📖 阅读指南（建议顺序）

按「动机 → MySQL → Redis 连接 → Redis 数据模型 → Redis 序列化 → 降级 → 双层协作 → 边界验证」递进，**每步都有对应思考题**，读完即建立双层架构全貌。

### 第 1 组：设计动机（一，先立全局）

**Step 1｜第一节「为什么需要双层数据库」** — 架构决策依据
- 看什么：单一数据库的四个困境（写放大/临时数据污染/读延迟/耦合）；双层分工图（MySQL 管事实，Redis 管通知投影）；「可丢失 vs 强一致」的判断原则。
- 思考题：为什么通知类数据不适合放 MySQL？（答：高频写放大 + 需要清理任务 + 读延迟；通知本质是可重建的业务投影）

### 第 2 组：MySQL 层（二，持久层基础）

**Step 2｜第二节「MySQL 层设计」** — 六表结构 + 状态机
- 看什么：6 张表职责总览；`status` enum 硬约束；`VALID_TRANSITIONS` service 层强校验（`hazard_service.py:36`）；SQLAlchemy 连接池与会话管理（`db.py`）。
- 思考题：enum 硬约束 + service 流转表是「双重校验」，各自防什么？（答：enum 防脏数据入库；流转表防业务逻辑跳步——如「待处理」直接变「已闭环」）

**Step 3｜`database/schema.sql`** — 表结构契约
- 看什么：外键约束（`reporter_id`/`assignee_id`/`operator_id`）；高频查询索引；`utf8mb4` 字符集；「应用不自动建表」的设计。
- 思考题：为什么应用启动不自动建表，而要 schema.sql 统一管理？（答：多人并行时表结构以单一文件为契约，避免 ORM 隐式建表与手工 SQL 漂移）

### 第 3 组：Redis 连接层（三.1，地基）

**Step 4｜`src/core/redis_client.py`** — 连接管理
- 看什么：`Redis.from_url` 解析连接串；`decode_responses=True`；`protocol=2` 的 RESP2 兼容；`@lru_cache` 单例；`ping_redis()` 优雅探测。
- 思考题：redis-py 8.x 默认 RESP3，连老版本 Redis 会报什么错？为什么？（答：`unknown command 'HELLO'`——RESP3 握手发 HELLO，老 Redis 不认识；`protocol=2` 降级解决）

### 第 4 组：Redis 数据模型（三.2–3.3，核心）

**Step 5｜`src/services/notification_service.py`** — Key 设计 + List 选型
- 看什么：key 命名空间 `shudao:notify:{user_id}` 按用户隔离；List 四操作（RPUSH/LRANGE/LLEN/DEL）与业务的映射；负索引 `-limit, -1` 读最近 N 条。
- 思考题：为什么选 List 而不是 Set 或 Hash？（答：通知是「有序 + 可重复 + 追加日志」语义，List 天然队列；Set 无序去重、Hash 定结构，都不匹配）

### 第 5 组：Redis 序列化与降级（三.4–3.5，工程细节）

**Step 6｜`_dump` / `_parse`** — JSON 序列化
- 看什么：JSON 承载 message/hazard_id/type 三字段；`ensure_ascii=False` 中文直存；三态兼容（新 dict / 旧字符串 / 损坏）。
- 思考题：为什么用 JSON 字符串而不是 Redis Hash？（答：一条消息一个原子值、升级加字段天然兼容旧数据；Hash 无法兼容旧纯字符串数据）

**Step 7｜降级设计** — 双层可靠性
- 看什么：push/list_for/count/clear 全部 try/except；失败返回 bool + warning 日志；业务事务不受影响；问答缓存 `_qa_cache_get/set` 同款降级。
- 思考题：如果 push 失败直接抛异常会怎样？（答：Redis 挂 → 派单也失败，违背「MySQL 管事实」的初衷；必须调用方无感）

**Step 7.5｜`chat.py` 问答缓存** — Redis 业务扩展（领导优化）
- 看什么：`shudao:rag_cache:<md5(question)>` String 存 {context, sources} + TTL 10 分钟；SSE 路由内「查缓存→命中跳过检索但 LLM 照常生成→未命中检索并写缓存」；实测 154.8s → 13.4s，回答多样且有引用。
- 思考题：为什么缓存 key 用问题 md5 而不是直接存问题文本？（答：key 定长、无中文/特殊字符、天然定长索引；精确匹配保证不误答）

### 第 6 组：双层协作与边界（四–五，收口）

**Step 8｜第四节「双层协作」** — 一次派单的完整数据流
- 看什么：MySQL 事务提交 → Redis 通知投影的时序；四个业务联动点（上报/派单/整改/验收）的推送对象表。
- 思考题：为什么通知在 `db.commit()` 之后推，而不是同一个事务里？（答：通知是可丢投影，不参与业务原子性；事务失败不该产生幽灵通知）

**Step 9｜第五节「边界反面验证」** — 会话历史为什么用 MySQL
- 看什么：通知（Redis）vs 会话历史（MySQL）的四维对比（持久性/结构查询/数据量/跨进程）；判断依据「能重建 → Redis，必须永久 → MySQL」。
- 思考题：如果把 chat_messages 也放 Redis 会有什么问题？（答：重启丢用户资产；分页翻历史难；references/rating 结构查询低效）

---

## 八、技术要点速查表

| 层 | 技术点 | 代码位置 |
|---|---|---|
| MySQL | 状态机 enum 硬约束 + service 合法流转表 | `schema.sql:30` + `hazard_service.py:36` |
| MySQL | 外键 + 高频索引 | `schema.sql:39-43` |
| MySQL | `pool_pre_ping` / `pool_recycle` 连接池 | `db.py:15-20` |
| MySQL | 请求级会话 `get_db()` 自动关闭 | `db.py:30-36` |
| Redis | `from_url` + RESP2 兼容 + 单例连接 | `redis_client.py:24-27` |
| Redis | key 命名空间 `shudao:notify:{user_id}` 按用户隔离 | `notification_service.py:16,25` |
| Redis | List 队列四操作（RPUSH/LRANGE/LLEN/DEL） | `notification_service.py:52,61,70,77` |
| Redis | JSON 序列化 + 旧数据兼容 | `notification_service.py:34-44` |
| Redis | RAG 分块缓存 String + TTL 10min（{context, sources}） | `chat.py:_qa_cache_key/_get/_set` |
| 双层联动 | 事务提交后推通知（MySQL 事实 → Redis 投影） | `hazard_service.py:157-175` |
| 降级策略 | Redis 全部操作 try/except，业务不受影响 | `notification_service.py:48-78` + `chat.py:_qa_cache_*` |

---

## 九、延伸思考（可扩展方向）

1. **通知过期策略**：当前 List 无 TTL，用户长期不用会积累。可加 `EXPIRE shudao:notify:{id} 30d` 定时刷新，或读后 `LTRIM` 截断；
2. **未读/已读分离**：现在未读数 = 队列长度（全量）。可引入第二个 key（`shudao:notify_read:{id}`）记录已读游标，未读 = `LLEN - 游标`；
3. **频道订阅**：管理员「接收上报」目前靠轮询 `/api/notifications`，可升级为 `PUBLISH shudao:channel:admin` + WebSocket 实时推送（前端即时红点）；
4. **限流复用**：`redis_client.py` 注释提到「接口限流」预留——可用 `INCR + EXPIRE` 实现滑动窗口限流，复用同一连接池；
5. **MySQL 分页优化**：隐患列表深分页（offset 大）可换 `WHERE id < 游标` 键集分页，避免大 offset 扫描；
6. **问答缓存升级**：当前 md5 精确匹配（保守）。可扩展为「问题规范化」——去标点/空白后 md5（兼容"高处作业有哪些安全要求？"与"高处作业有哪些安全要求"），或按相似度（如 `SET` + 编辑距离）做近义命中，需权衡误命中风险；
7. **缓存失效联动**：知识库重入库后（D3 collect_kb），10 分钟内的旧分块缓存可能基于旧知识——可加「知识库版本号」进缓存 key（`shudao:rag_cache:<kb_version>:<md5>`），重建知识库即整体失效，或把 TTL 再缩短。

---

## 十、思考问题（理解自测）

1. 为什么通知选 Redis List 而不是 MySQL 表？（答：通知是可重建的业务投影，MySQL 管事实不存投影；List 一条命令完成入队/读尾部/计数/清空，写入路径比「INSERT + 定时清理」简单得多）
2. `LRANGE key -50 -1` 的负索引解决了什么问题？（答：只取最近 N 条，O(limit) 且不浪费带宽；若先 `LRANGE 0 -1` 全量再 Python 切片，是 O(N) 全量传输）
3. key 为什么要设计成 `shudao:notify:{user_id}` 三段式？（答：前缀即命名空间，同类 key 可批量管理、避免与未来业务冲突；按用户分 key 还天然实现信息隔离——`GET /api/notifications` 只读自己的 key，数据层杜绝越权）
4. JSON 三态兼容（dict / 旧字符串 / 损坏）解决什么问题？（答：线上 Redis 里残留升级前的纯字符串旧数据，`json.loads` 失败必须兜底为 general 类型，保证平滑升级不清库）
5. 降级设计为什么把 try/except 放在 `NotificationService` 内部而不是调用方？（答：让「业务事务不感知 Redis 失败」成为 service 的契约；若在 hazard_service 里逐个 try，每个调用点都要重复且容易漏）
6. 为什么通知在 `db.commit()` 之后推，而不是和 MySQL 同一个事务？（答：通知不参与业务原子性；若事务回滚但通知已推，会产生指向不存在状态的幽灵通知；commit 成功后推，通知语义 = 「已落库的事实」）
7. redis-py 8.x 连老版本 Redis 报 `unknown command 'HELLO'`，根因是什么？（答：RESP3 协议握手阶段发 HELLO 命令，Redis <6.0 不认识；`protocol=2` 强制 RESP2 兼容——提醒：第三方库升级会悄悄改变协议行为，升级后要回归连通性测试）
8. `decode_responses=True` 有什么好处？（答：直接拿 str 而非 bytes，省去每个字段手动 `.decode()`；代价是二进制数据（如图片字节）需另存，本项目通知是纯文本所以无副作用）
9. 双层架构里「该放 MySQL 还是 Redis」的判断依据是什么？（答：① 能否重建/是否可丢——能重建放 Redis；② 是否要结构查询/长期保留——要则放 MySQL；③ 写入频率——高频临时写优先 Redis。通知=可丢+尾部切片读；会话历史=永久+按会话翻页+引用落库，故各归其位）
10. 如果把 `chat_messages` 的 `references`（RAG 引用来源 JSON）和 `rating`（点赞/踩）也挪到 Redis，会失去什么？（答：references 随消息永久保留、A06 前端要随时回读——Redis 重启即丢；rating 是用户反馈数据要统计（H07）——Redis 难做聚合查询。它们都是「必须持久 + 要查」的数据，属于 MySQL）
11. RAG 分块缓存为什么用 `SETEX`（TTL 10 分钟）而通知 List 不用？（答：缓存分块会因知识库更新而过期，10 分钟 TTL 自动失效（领导要求）；通知是「已读即止」的短期数据，由用户清空或积累，不需要自动过期）
12. 缓存命中时为什么还要 LLM 重新生成、而不是直接返回缓存分块？（答：① **回答多样性**——LLM 每次基于同一分块重新组织语言，避免千篇一律；② 分块只是「依据」，不是「答案」，必须经 LLM 组织成自然语言；③ 命中时消息落库路径与未命中完全一致，天然规避「缓存命中不落库」的坑）

---

> 本文档与 `快速读懂D1-D5.md` 同源（同一项目、同一代码），但视角不同：快速读懂按**天**记录「做了什么」，本文按**技术专题**解析「为什么这么设计」。适合技术分享 / 答辩讲解使用。
