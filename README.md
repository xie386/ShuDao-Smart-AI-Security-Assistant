# 蜀道智能安全助手（ShuDao Smart AI Security Assistant）

> 一个面向施工安全场景的 **智能安全管理平台**，4 人团队基于 Vibe Coding 协作完成。
> 集成 **隐患闭环管理（H01–H08）**、**AI 助手（RAG 知识问答）**、**PPT 工坊**、**考试工坊**、**知识贡献**、**个人主页** 六大模块。
> 当前为**正式收官版本**：D1–D5 全部完成，数据库 10 张表，后端 13 个路由模块 + 24 个脚本 + 10 个测试文件，前端单页应用，覆盖「采集 → 治理 → 入库 → 检索 → 问答 → 评估」完整 RAG 链路。

---

## 一、项目简介

`蜀道智能安全助手` 是一个服务于施工企业（如蜀道集团）的**安全管理 SaaS 雏形**。它不是单点功能堆砌，而是一条完整的业务闭环：

- **员工上报隐患** → 管理员**派单** → 责任人**整改** → 管理员**验收** → 全流程**消息通知**联动；
- 沉淀的官方安全知识（法规 / 操作规程 / 应急预案 / 事故案例）经**采集 → 治理 → 向量化**进入知识库；
- 员工随时向 **AI 助手**提问，答案**引用知识库来源**；还能一键生成 **PPT 课件 / 试卷**；
- 全员可**贡献知识**，管理员**审核（AI 预筛 + 判定标准）**通过后**清洗治理 + 增量入库**；
- 每个人的**个人主页**聚合自己的资料与贡献记录。

它的核心理念是 **「业务闭环 + AI 增强」**：

- 🏗️ **隐患管理**是业务主轴：H01 上报 → H02 列表 → H03 详情 → H04 派单 → H05 整改 → H06 验收，外加 H07 统计 + H08 消息联动；
- 🤖 **RAG 问答**是 AI 主轴：官方文档 + 用户贡献 → Chroma 向量库 → 混合检索（BM25 + 向量 + RRF + 重排）→ 引用来源；
- 🧩 **工程化底座**：LangChain 1.x 架构（课程硬性要求）、增量入库不重建、Redis + MySQL 双层存储、子智能体辅助审批、用户上传文件在线清洗治理。

> 一句话总结：把「上报、派单、整改、验收」的业务流和「采集、治理、检索、问答」的知识流拧成一股绳。

---

## 二、功能特性

| 模块 | 特性 | 说明 |
| --- | --- | --- |
| 隐患管理 | 上报 / 列表 / 详情 / 派单 / 整改 / 验收 | H01–H06 全闭环，状态机强制流转（待处理 → 处理中 → 待验收 → 已闭环/已驳回），支持图片 + AI 识别自动填充 |
| 消息联动 | H08 通知中心 | 上报→管理员、派单→责任人、整改→上报人、验收→全员，Redis 队列 + 前端实时角标 |
| AI 助手 | RAG 知识问答 | LangChain 1.x：ChatOpenAI + with_fallbacks（DeepSeek 主 / agnes 降级），混合检索 + 引用来源，SSE 流式输出 |
| RAG 知识库 | 采集 / 治理 / 入库 / 评估 | 政务站采集 93+ 份 → HTML 治理 → 增量 upsert → 六指标评测（ALL@10 = 0.83 达标） |
| PPT 工坊 | AI 生成课件 | 百度云 PPT 专职 API（每日 10 次免费，用尽自动降级自研 python-pptx），支持用户模板上传 |
| 考试工坊 | AI 出卷 + 导出 | exam_agent 按 prompts.yml 生成试卷 + 答案，导出 MD/DOCX/PDF 三格式 |
| 知识贡献 | 上传文件 → 审核 → 入库 | 全员传文件（md/txt/docx/pdf ≤10MB）→ admin 审核（子智能体预筛 + 判定标准文档双保障）→ 在线清洗治理 → 增量 upsert |
| 个人主页 | 资料 + 贡献聚合 | 改用户名/密码/邮箱、上传头像（user_profiles 一对一表，users 零改动）；聚合「我的知识贡献」「我的隐患贡献」三类 tab |
| 视觉识别 | 隐患图片 AI 识别 | qwen 视觉模型识别隐患类型/等级/location 坐标，画框标注（utils/boxes.py） |
| 统计报表 | H07 | 隐患类型分布 / 整改率 / 7 天趋势，SQLAlchemy 聚合 |

---

## 三、项目结构

```
team/
├── README.md                    # 本文件（阅读顺序见第四节）
├── backend/                     # ★ 后端（FastAPI）
│   ├── src/
│   │   ├── main.py              #   应用入口：路由挂载 + 静态托管 + CORS
│   │   ├── core/                #   基础设施：config / db / redis_client / security（5 文件）
│   │   ├── ai/                  #   ★ AI 层：llm_client / rag / knowledge / embeddings / reranker
│   │   │                        #           vision / exam_agent / knowledge_review_agent / ppt_baidu / ppt_local / prompts（12 文件）
│   │   ├── models/              #   ORM 模型，10 张表严格对齐 P3 schema.sql（8 文件）
│   │   ├── routers/             #   ★ 路由层：13 个业务模块（14 文件）
│   │   ├── services/            #   ★ 业务层：状态机 / 通知 / 入库 / 治理（13 文件）
│   │   ├── schemas/             #   Pydantic 契约（11 文件）
│   │   └── utils/               #   files（上传校验）/ boxes（bbox 画框）（3 文件）
│   ├── scripts/                 #   ★ 24 个脚本：采集 / 治理 / 入库 / 验证 / 评测
│   ├── tests/                   #   ★ 10 个 pytest 测试文件
│   ├── docs/                    #   ★ 文档：里程碑技术书 / PRD / 架构 / D1-D5 快速读懂 / 计划书
│   ├── prompt/
│   │   └── prompts.yml          #   ★ 所有提示词配置（主场景 + 各子智能体）
│   ├── knowledge_src/           #   采集的原始知识（四类目录 + raw_manifest.jsonl）
│   └── uploads/                 #   上传文件（隐患图片 / 贡献文件 / 头像 / 导出的 PPT·试卷）
├── front/
│   └── safety/index.html        # ★ 前端单页应用（SPA，川蜀红色系）
├── database/
│   └── schema.sql               # ★ 数据库 DDL（10 张表，P3 产出，代码不 create_all）
├── deploy/                      # Docker Compose + nginx 部署配置
└── RAG_test.txt                 # RAG 冒烟测试样本
```

---

## 四、阅读指南（建议顺序 · 逐文件详解版）

> ⭐ **核心章节**。后端共 **61 个 .py 源文件 + 24 个脚本 + 10 个测试文件 + 1 个提示词配置**。不要按目录顺序读，按下面「**地基 → 基础设施 → AI 层 → 业务层 → 路由层 → 契约 → 工具 → 入口 → 脚本 → 测试 → 前端**」的难度递进，**每个文件都列了「看什么 + 思考题」**。
>
> 阅读节奏建议：分 4 天，每天 2 步。第 1-2 步打地基，第 3-5 步读核心，第 6-9 步扫全貌，第 10-12 步看工程化。每读完一步合上文件，能回答思考题再进下一步。

---

### 第 0 步：先看整体（30 分钟建立地图）

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 0️⃣-1 | `docs/里程碑技术书.md` | **项目的「宪法」**。每个接口、每张表、每条状态机的验收口径都在这里。思考：为什么业务验收要以它为准，而不是以代码为准？ |
| 0️⃣-2 | `docs/PRD.md` | 业务背景 + 用户故事。思考：哪些角色在用这个系统？他们的核心诉求分别是什么？ |
| 0️⃣-3 | `docs/ARCHITECTURE.md` | 系统架构图。思考：前端、后端、MySQL、Redis、Chroma、本地模型（bge）之间的依赖关系是怎样的？ |
| 0️⃣-4 | `docs/AI_SOLUTION.md` | **AI 层设计说明书**（§1 模型选型 / §3 会话 / §4 分块 / §5 检索 / §6 评估 / §8 上下文）。思考：AI 层的每条设计决策（分块策略、混合检索、评估口径）在代码里是怎么落地的？ |
| 0️⃣-5 | `docs/技术解析-Redis与MySQL双层数据库结构设计.md` | 为什么 MySQL 存业务数据、Redis 做消息通道。思考：哪些数据必须落 MySQL，哪些放 Redis 即可？ |

> 💡 读完这五篇，你应该能回答：「这个系统有几条主链路？」（答：隐患闭环 + RAG 问答，两条）「MySQL 和 Redis 各管什么？」（答：MySQL 持久化业务，Redis 消息队列）。

---

### 第 1 步：数据库层（地基 —— schema.sql + models/ 全部 8 个文件）

> 原则：**先有表，再有模型，再有接口**。代码严禁 `create_all`，所有表结构以 schema.sql 为准——这是 P3 线定的铁律，防「代码和库各说各话」。

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 1️⃣-1 | `database/schema.sql` | **10 张表**的完整 DDL。思考：①为什么隐患拆 `hazards` + `hazard_timeline` 两张表？（主表存状态，时间线表存流转历史，详情页展示进度）；②为什么 `knowledge_contributions.content` 用 `MEDIUMTEXT`？（存文件解析全文，可能超 64KB）；③`user_profiles` 为什么独立建表而不给 users 加列？（P3「勿改表」铁律） |
| 1️⃣-2 | `src/models/__init__.py` | 所有 ORM 模型的统一导出。注意 `__all__` 列表（10 个模型）。思考：为什么模型集中在 `__init__` 导出？（db_verify / 其他模块一行 import 全部） |
| 1️⃣-3 | `src/models/user.py` | 用户模型：`username / hashed_password / role / real_name / created_at`。注意 role 是普通 String 不是 Enum（代码层 `Literal` 限死）。思考：为什么密码字段叫 `hashed_password` 而不是 `password`？（明示存的是哈希，不是明文） |
| 1️⃣-4 | `src/models/user_profile.py` | **个人主页扩展表模型**（最新模块）。`user_id` 唯一约束 = 一对一。思考：为什么 email/avatar 要落独立表而不是 users 加列？（users 是核心表，P3 铁律禁改；新表零风险） |
| 1️⃣-5 | `src/models/hazard.py` | 隐患主模型 + `HazardTimeline` 时间线模型。注意 `status` 用 `Enum("待处理","处理中","待验收","已闭环","已驳回")`、`updated_at` 靠 SQLAlchemy `onupdate`（表无触发器）。思考：为什么状态合法值在**数据库层**限死，流转顺序在**service 层**限死？（双保险：DB 防非法值，service 防非法流转） |
| 1️⃣-6 | `src/models/conversation.py` | AI 会话模型：`Conversation` + `ChatMessage`。注意 `chat_messages.refs` 字段（A06 引用来源 JSON）。思考：为什么会话历史存 MySQL 而不是 Redis？（持久化，刷新不丢；多轮拼接 {history} 实现 ≥5 轮上下文） |
| 1️⃣-7 | `src/models/ppt_task.py` | PPT 任务模型：状态机（草稿→生成中→已完成/失败）+ `outline`（大纲 JSON）+ `file_path`。思考：这个模型是后面哪些模块的「模板」？（答：exam_papers 就是照它设计的） |
| 1️⃣-8 | `src/models/exam_paper.py` + `src/models/knowledge_contrib.py` | 考试工坊 / 知识贡献模型。注意：exam_content 存结构化 JSON；knowledge_contrib 有 `file_intro`（简介）/ `file_path`（文件路径）/ `content`（解析全文）+ `KnowledgeContribTimeline`。思考：为什么 knowledge_contrib 也要 timeline 表？（提交→审核→入库的过程要留痕，同 hazard_timeline 范式） |

> 💡 学习心法：8 个模型文件 = **10 张表的 ORM 映射**。读完你应该能画出「users → 各业务表」的外键关系图（reporter_id / assignee_id / contributor_id / user_id 都是指向 users.id 的 FK）。

---

### 第 2 步：核心基础设施（core/ 全部 5 个文件）

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 2️⃣-1 | `src/core/config.py` | `Settings(BaseSettings)` 从 `.env` 读全部配置。注意：模型地址是本机路径（`D:/LLM/model/bge-large-zh`）+ `cuda` 设备 + `BAIDU_PPT_DAILY_QUOTA=10`（每日免费次数）。思考：为什么嵌入模型用本地路径而不是 API？（课程要求本地部署 + 中文检索质量）为什么 PPT 要记录每日配额？（用尽降级的前提） |
| 2️⃣-2 | `src/core/db.py` | SQLAlchemy 2.x 引擎 + `get_db` 依赖。注意 docstring 强调「**不自动建表**」。思考：为什么 FastAPI 用 `Depends(get_db)` 而不是全局 Session？（请求级隔离，用完即关，防连接泄漏） |
| 2️⃣-3 | `src/core/redis_client.py` | Redis 连接池。注意端口 **6380**（非默认 6379）、密码 123456、`ping_redis()` 优雅探测（服务未启动不崩）。思考：Redis 挂了系统还能用吗？（能——通知 push 优雅降级，见 notification_service） |
| 2️⃣-4 | `src/core/security.py` | bcrypt 密码哈希 + JWT 签发/校验。注意 docstring 强调「**不引入 passlib**」（bcrypt>=5.0 与 passlib 兼容问题）。思考：为什么密码要哈希而不是加密？（哈希不可逆，库泄露不还原明文）JWT 的 SECRET_KEY 在哪配置？ |
| 2️⃣-5 | `src/core/__init__.py` | 空包标记。思考：为什么 Python 包都要有 `__init__.py`？（标记目录为包，可被 import） |

> 💡 学习心法：core 层是「横切能力」——任何模块都可能用到 config（settings）、db（Session）、security（get_current_user）。读完后你应该知道「**一个请求进来，依赖注入链是怎样的**」：路由参数 `user: dict = Depends(get_current_user)` → security 解析 JWT → 查 DB → 返回 {id, role}。

---

### 第 3 步：AI 层（ai/ 全部 12 个文件 —— 项目含金量最高）

> 课程硬性要求 **LangChain 架构**（已重构 LangChain 1.x）。按「**模型 → 嵌入 → 分块入库 → 检索 → 重排 → 问答 → 视觉 → 子智能体 → PPT → 提示词**」读。

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 3️⃣-1 | `src/ai/llm_client.py` | **模型初始化（核心中的核心）**。`ChatOpenAI`（OpenAI 兼容端点）+ `with_fallbacks` 原生降级：DeepSeek 主 → agnes 备。注意对外接口是 `chat()` / `chat_stream()`，业务层不感知降级。思考：为什么用 OpenAI 兼容协议而不是各厂商原生 SDK？（统一接口，换模型只改 .env，代码零改动）降级链在哪个方法里定义？ |
| 3️⃣-2 | `src/ai/embeddings.py` | 本地嵌入 `bge-large-zh`（HuggingFaceEmbeddings）。三个要点：**lazy load**（首次调用才加载 ~1.3GB，不拖慢启动）、`cuda` 设备、查询前缀「为这个句子生成表示以用于检索相关文章」。思考：为什么查询要加 bge 官方前缀？（对齐训练分布，检索更准）为什么 lazy load？（后端启动不加载 1.3GB 模型，几秒内可服务） |
| 3️⃣-3 | `src/ai/knowledge.py` | **入库（增量 upsert，不重建）**。`upsert_chunks(chunks, collection_name)`：结构感知分块（法规按条/规程按步/预案按章节）、单块 300-800 字、相邻块重叠 15%、元数据 {doc, doc_type, chapter, article_no, tags, source}。思考：为什么增量入库能保证已检索内容不丢？（Chroma HNSW 原地写入，按 doc id upsert，重复提交覆盖不重复）doc 参数 `contrib_{id}` 的命名空间隔离意义？ |
| 3️⃣-4 | `src/ai/rag.py` | **混合检索编排（LangChain 1.x）**。`hybrid_retrieve`：稠密（Chroma/bge）+ 稀疏（BM25/jieba）→ **RRF 融合** → bge-reranker 重排 → 父块注入（metadata.parent_text）→ 返回带来源上下文。注意：写入侧 chromadb 直连，检索侧稠密走 langchain_chroma，重排经 RunnableLambda 接入。思考：为什么混合检索比纯向量好？（BM25 抓关键词精确匹配，向量抓语义相似，RRF 融合互补，重排精排 Top-20→Top-4） |
| 3️⃣-5 | `src/ai/reranker.py` | 本地重排 `bge-reranker-v2_m3`（CrossEncoder）。思考：为什么重排是「检索后精排」而不是检索本身？（初召回 Top-20 宽进，重排窄出 Top-4 喂模型，质量/成本平衡）LangChain 无原生 reranker，怎么接的？（自研 CrossEncoder + RunnableLambda） |
| 3️⃣-6 | `src/ai/prompts.py` | 提示词加载器：`yaml.safe_load`（防注入，绝不 yaml.load）+ `lru_cache` 单次加载 + `render()` 用 str.format 填充 {placeholder}。思考：为什么提示词全放 YAML？（改提示词不动代码；参考 deep_search_pro 模式） |
| 3️⃣-7 | `src/ai/vision.py` | 视觉子智能体（qwen，LangChain ChatOpenAI 多模态）。复用 prompts.yml 的 `hazard_vision` 场景（四段式，强制 JSON，含 location 坐标）。思考：图片怎么传给多模态模型？（OpenAI 兼容 content 含 text + image_url data URL） |
| 3️⃣-8 | `src/ai/exam_agent.py` | **考试工坊出卷子智能体**。复用 llm_client + RAG **只读引用**（可选 hybrid_retrieve 增强知识依据）。注意 `_extract_json` 容错（LLM 输出可能带 markdown 围栏）。思考：为什么试卷生成要「结构化 JSON + 逐题校验」而不是自由文本？（前端要渲染题目/答案/解析，字段必须对齐） |
| 3️⃣-9 | `src/ai/knowledge_review_agent.py` | **知识贡献审核预筛子智能体**。依据：`docs/施工安全领域文件鉴别判定标准.md`（浓缩进 prompts.yml 的 knowledge_review 模板）。思考：为什么 AI 只「预筛辅助」不「终审」？（审核是管理职责，AI 意见仅供参考 + 判定标准文档双保障） |
| 3️⃣-10 | `src/ai/ppt_baidu.py` | 百度千帆 PPT 专职 API（主方案）。`generate_ppt_by_outline` SSE 流式，status 流转「PPT生成中×N → 生成结束 → 导出中 → 导出结束」。思考：为什么 PPT 用专职 API 而不是通用 LLM？（专用模型生成 PPT 质量远高于通用模型，每日 10 次免费对演示足够） |
| 3️⃣-11 | `src/ai/ppt_local.py` | PPT 本地兜底（百度额度用尽/故障时）。A 方案 python-pptx 商务模板渲染 + B 方案 HTML 封面截图（可选）。思考：为什么保留本地方案？（API 有配额和故障风险，离线兜底保证演示不翻车） |
| 3️⃣-12 | `src/ai/__init__.py` | 空包标记，注意注释「重依赖全部 lazy-import」。思考：lazy import 的价值？（核心后端轻量启动，模型加载推迟到首次调用） |

> 💡 学习心法：AI 层 12 个文件 = **1 个模型客户端（llm_client）+ 1 条 RAG 链（embeddings → knowledge → rag → reranker）+ 1 个视觉（vision）+ 2 个子智能体（exam / knowledge_review）+ 2 条 PPT 通道（baidu / local）+ 1 个提示词加载器（prompts）**。RAG 链是主线，其余都是「复用 llm_client + prompts.yml 的实例」。

---

### 第 4 步：业务服务层（services/ 全部 13 个文件）

> service 层是「**状态机 + 权限 + 通知 + 时间线**」四件套的集中地。先精读 hazard_service（范式源头），后面每个模块都是它的变体。

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 4️⃣-1 | `src/services/hazard_service.py` | **业务核心（最值得精读）**。H01 上报 / H02 列表 / H03 详情 / H04 派单 / H05 整改 / H06 验收 + `_ensure_transition` 状态机 + `_timeline` 写时间线 + H08 通知联动。注意：非法流转 400、权限不足 403（router 层 RBAC + service 层再校验双保险）；`list_hazards` 的 `rectified_by` / `reviewed_by` 可选过滤（个人主页加的）。思考：为什么整改权限要「责任人本人或 admin」？（H05 只有被派单人能提交整改）为什么列表过滤参数全部可选？（零影响现有调用方） |
| 4️⃣-2 | `src/services/notification_service.py` | H08 通知（Redis 队列）。`push()` 存 JSON 字符串 {message, hazard_id, type}，兼容旧纯字符串数据；`TYPE_REPORT` / `TYPE_KB_CONTRIB` 等消息类型。思考：为什么通知是「service 直接 push」而不是「接口返回后前端自己弹」？（后端是唯一事实来源，刷新也能拿到）Redis 挂了的优雅降级逻辑？ |
| 4️⃣-3 | `src/services/auth_service.py` | 认证业务：注册（**硬编码 employee**）/ 登录 / 令牌签发。思考：为什么注册强制 employee？（admin 只能 init_admin.py 创建，防越权） |
| 4️⃣-4 | `src/services/chat_service.py` | AI 问答业务：会话管理 / 历史拼接 / 消息落库。注意：历史**截断最近 5 轮（10 条）**注入 prompt，防上下文膨胀（AI_SOLUTION §8）。思考：为什么截断而不是全量注入？（token 成本 + 长上下文稀释注意力） |
| 4️⃣-5 | `src/services/ppt_service.py` | PPT 业务（P01–P07）：大纲生成（LLM + prompts.yml ppt_outline）→ 大纲编辑 → 生成（百度 API 主 → 本地兜底）→ 模板映射 → 文件落盘。思考：双通道降级在 service 层怎么编排？（try baidu → except 抛给本地，状态机同步更新） |
| 4️⃣-6 | `src/services/ppt_template_service.py` | PPT 模板管理。内置 3 套风格预设（business/simple/tech）+ 用户上传模板（存 `knowledge_src/ppt_model/`，与知识库平级**不会被 RAG 采集**）。上传校验：扩展名 .pptx + ≤20MB + python-pptx 可打开（防损坏文件入库）。思考：为什么模板存 knowledge_src 目录？（复用同一文件存储区，但 .pptx 不在 RAG 采集白名单，天然隔离） |
| 4️⃣-7 | `src/services/exam_service.py` | 考试工坊业务：试卷任务状态机（生成中→已完成/失败）+ 出卷 + 导出。流程：创建任务 → exam_agent.generate_paper → 成功写 exam_content / 失败写 error → export 落盘 uploads/exam/。思考：它和 ppt_service 的状态机设计有什么相似？（都是「异步任务 + 状态 + file_path」模式） |
| 4️⃣-8 | `src/services/exam_export.py` | 试卷导出三格式：md / docx（python-docx）/ pdf（reportlab）。思考：为什么导出要落盘返回相对路径而不是直接返回字节流？（落盘可复用、可下载、与上传文件统一管理） |
| 4️⃣-9 | `src/services/knowledge_contrib_service.py` | **知识贡献业务（K01–K06）**。K01 multipart 上传：校验扩展名/大小 → 存盘 uploads/kb_contrib/ → `_extract_file_text` 解析（md/txt 双编码兜底 / docx / pdf pymupdf）→ content=全文、file_intro=简介；审核通过 → **kb_govern 清洗 → quality_check 校验（不达标 422）→ upsert_chunks 增量入库**。注意 `_CONTENT_MIN` 最小字数门槛。思考：为什么 content 存文件全文而不是简介？（分块入库需要真实内容）为什么审核通过才入库？（防污染知识库）为什么清洗在入库前？（用户文件可能含 HTML 残留/乱码/噪声行） |
| 4️⃣-10 | `src/services/kb_govern.py` | **在线清洗治理（知识贡献独有链路）**：`clean_text`（HTML 剥离 / 控制字符 / 噪声行剔除 / 空白压缩 / 相邻重复行去重）+ `quality_check`（字数 ≥300 / 重复段落 ≤40% / 乱码 ≤5% / HTML 残留检测）。思考：它和 scripts/govern_html.py 的分工？（govern_html 治理**采集的网页**，kb_govern 治理**用户上传文件**，两条通道各自清洗） |
| 4️⃣-11 | `src/services/stats_service.py` | H07 统计：隐患类型分布 / 整改率 / 7 天趋势。SQLAlchemy `func.count` + group_by 聚合，**不加载全表**。思考：为什么统计要在 SQL 层聚合而不是 Python 层？（大数据量下性能差几个数量级） |
| 4️⃣-12 | `src/services/user_profile_service.py` | 个人主页业务：`get_profile`（合并 users + user_profiles）/ `_get_or_create`（首次访问自动建空 profile）/ 改用户名查重（409）/ 改密旧密码校验（401）/ 改邮箱格式校验（422）/ 绑头像（URL 必须以 /uploads/ 开头）。思考：为什么邮箱格式校验在 service 而不在 schema？（复用性 + 前端可自由调；schema 只做必填校验） |
| 4️⃣-13 | `src/services/__init__.py` | 空包标记。 |

> 💡 学习心法：13 个 service = **1 个范式源头（hazard_service）+ 3 个变体（knowledge_contrib / exam / ppt 都抄它的状态机 + 时间线）+ 3 个 AI 业务（chat / exam_export / kb_govern）+ 2 个基础（auth / notification）+ 2 个辅助（stats / user_profile）+ 1 个模板（ppt_template）**。

---

### 第 5 步：路由层（routers/ 全部 14 个文件）

> 路由层很薄——只做「参数接收 + 鉴权依赖 + 调 service」。**每个路由文件的 docstring 都列了端点清单**，这是最好的速查表。

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 5️⃣-1 | `src/routers/auth.py` | 注册 / 登录 / 当前用户（/api/auth/*）。注意注册时 role 从 body 读但 service 硬编码 employee（防篡改）。思考：登录返回的 TokenResponse 里 user 字段给前端存了哪些信息？ |
| 5️⃣-2 | `src/routers/users.py` | 用户列表（/api/users，**仅 admin**）：`?role=` 逗号分隔多角色、`?q=` 搜索（纯数字按 id，否则按 username/real_name 模糊）、默认排除当前用户。思考：为什么派单选择责任人要用这个接口而不是全量？（D5 A2：解决下拉 limit 截断，50 后用户搜不到的问题） |
| 5️⃣-3 | `src/routers/hazards.py` | 隐患路由（H01–H06）：POST /api/hazards（上报）、GET（列表，含 mine/status/level/rectified_by/reviewed_by 过滤）、GET /{id}（详情+timeline）、POST /{id}/assign（派单，仅 admin）、POST /{id}/rectify（整改）、POST /{id}/accept（验收，仅 admin）。思考：为什么列表接口一个 GET 顶所有筛选？（查询参数组合，前端一个页面多种视图） |
| 5️⃣-4 | `src/routers/chat.py` | AI 问答（A01–A04）：POST /api/chat **SSE 流式**（StreamingResponse, text/event-stream）。看 `data: {"delta": ...}\n\n` 逐块推送 + `[DONE]` 结束的格式。思考：为什么问答用 SSE 而不是普通 JSON？（打字机效果 + 首字延迟低；SSE 比 WebSocket 简单，单向够用） |
| 5️⃣-5 | `src/routers/kb.py` | 知识库只读接口（/api/kb/list + /api/kb/doc）：供前端「引用来源」跳转阅读治理后的 md 原文 + 首页政策文件浏览。**安全设计**：仅允许 knowledge_src 四类目录下的 .md/.txt、路径 `resolve()` + 前缀校验杜绝路径穿越。思考：为什么只读接口也要防路径穿越？（../ 可读服务器任意文件，必须白名单 + resolve 双保险） |
| 5️⃣-6 | `src/routers/ppt.py` | PPT 路由（P01–P07）：新建 / 列表 / 详情 / 编辑大纲 / 生成 / 模板。思考：P02 大纲编辑为什么是整篇替换？（简单可靠，避免部分更新的 diff 复杂度） |
| 5️⃣-7 | `src/routers/exam.py` | 考试工坊路由（6 端点）：模板列表 / 创建生成（同步 30-60s）/ 历史 / 详情 / 导出 / 删除。思考：为什么出卷是同步等待而不是后台任务？（30-60s 可接受，前端 loading 即可，省去轮询复杂度——与 PPT 的异步状态机不同，为什么？因为 PPT 生成更慢） |
| 5️⃣-8 | `src/routers/knowledge_contrib.py` | 知识贡献路由（K01–K06 + review-standard）：POST（multipart：title/doc_type/intro + file）/ 列表（mine + status_filter）/ 详情 / timeline / 审核（仅 admin）/ 判定标准文档。思考：为什么 K01 用 multipart 而不是 JSON？（带文件上传，JSON 传不了文件） |
| 5️⃣-9 | `src/routers/user_profile.py` | 个人主页路由（/api/me/*）：GET profile + PATCH username / password / email / avatar。思考：为什么用 PATCH 而不是 PUT？（部分更新语义）为什么 5 个接口而不是 1 个大接口？（每个字段独立校验、独立错误提示） |
| 5️⃣-10 | `src/routers/uploads.py` | 文件上传（/api/uploads/image + /avatar）：复用 `validate_and_save`，H01 上报图 / H05 整改照 / 头像三处复用。思考：为什么上传和业务字段解耦？（先传文件拿 URL，再随表单提交 URL——大文件不阻塞业务请求） |
| 5️⃣-11 | `src/routers/notifications.py` | H08 消息（/api/notifications）：列表 / 已读 / 未读数。思考：为什么前端要轮询未读数？（实时角标；Redis 通道 + 前端定时拉取的轻量方案） |
| 5️⃣-12 | `src/routers/stats.py` | H07 统计（/api/stats）：类型分布 / 整改率 / 趋势。 |
| 5️⃣-13 | `src/routers/vision.py` | 图片识别（POST /api/vision/hazard）：调 qwen 视觉，key 未配置返回 **503 + 清晰提示**；A 方案同步生成 boxes 标注图返回 boxes_url。思考：为什么 key 未配置返回 503 而不是 500？（503 是「当前不可用」语义，提示明确，500 是「内部错误」会误导） |
| 5️⃣-14 | `src/routers/__init__.py` + `src/main.py`（挂载处） | 空包 + 路由汇总。main.py 里 `app.include_router(...)` 逐个挂载。思考：为什么 main.py 不直接写路由函数？（模块化，每个业务一个文件，main 只做装配） |

> 💡 学习心法：14 个路由文件 = 13 个业务模块 + 空包。**读路由文件时先看 docstring 的端点清单，再找对应 service 方法**——路由层本身没有业务，只是「HTTP 翻译器」。

---

### 第 6 步：契约层（schemas/ 全部 11 个文件）+ 工具层（utils/ 全部 3 个文件）

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 6️⃣-1 | `src/schemas/auth.py` | RegisterRequest（role 用 `Literal["employee","safety_officer"]` 限死）/ LoginRequest / UserOut / TokenResponse。 |
| 6️⃣-2 | `src/schemas/hazard.py` | 隐患契约：HazardCreate / RectifyRequest / AcceptRequest / HazardOut（含 timeline）。 |
| 6️⃣-3 | `src/schemas/chat.py` | 问答契约：ChatRequest / 会话 / 消息 / 快捷提问。 |
| 6️⃣-4 | `src/schemas/ppt.py` + `src/schemas/exam.py` | PPT / 考试工坊契约。注意 exam 的 create 请求含 template / use_rag / export_format。 |
| 6️⃣-5 | `src/schemas/knowledge_contrib.py` | 知识贡献契约。注意：**K01 不走 pydantic body**（multipart 上传，见 docstring 说明）；K06 ReviewRequest（approved + note + review_by）。 |
| 6️⃣-6 | `src/schemas/user_profile.py` | 个人主页契约：ProfileOut / UsernameUpdate / PasswordUpdate / EmailUpdate / AvatarUpdate。 |
| 6️⃣-7 | `src/schemas/stats.py` + `src/schemas/upload.py` + `src/schemas/common.py` | 统计 / 上传 / 通用响应契约。 |
| 6️⃣-8 | `src/schemas/__init__.py` | 空包标记。 |
| 6️⃣-9 | `src/utils/files.py` | **上传安全工具**：类型白名单（jpg/jpeg/png/webp）+ MIME 双校验 + ≤10MB + uuid 重命名落盘。思考：为什么 uuid 重命名？（杜绝路径穿越与重名覆盖，不信任用户文件名） |
| 6️⃣-10 | `src/utils/boxes.py` | 隐患定位画框：根据视觉返回 location [x1,y1,x2,y2] 像素坐标在**原图**画框，生成 `_boxes.png`。思考：为什么坐标是「图片真实像素」而不是归一化？（前后端共用原始像素，避免缩放换算误差） |

> 💡 学习心法：schemas 层是「**接口的静态类型**」——FastAPI 用它们做请求校验 + 响应序列化 + OpenAPI 文档。**改接口先改 schema 再改 router**，顺序不能反。

---

### 第 7 步：入口与提示词（main.py + prompts.yml）

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 7️⃣-1 | `src/main.py` | **后端入口**。三要素：uvicorn 启动命令解析（`src.main:app` = 模块:实例）、13 个 router 挂载、静态文件托管（/uploads/ + /safety/）、CORS、`mimetypes.add_type("text/markdown",".md")`（修复 Windows .md 下载变 txt 的 bug）。思考：为什么 .md 要手动注册 MIME？（Windows mimetypes 把 .md 映射为 text/plain，浏览器下载变 txt） |
| 7️⃣-2 | `prompt/prompts.yml` | **全部提示词**：主安全问答场景（system_prompt + 引用格式）、hazard_vision（四段式）、ppt_outline、exam 出卷、knowledge_review（判定标准浓缩决策树）。思考：为什么主场景提示词要规定「引用来源格式」？（A06 引用可溯源，答案可验证） |

---

### 第 8 步：脚本层（scripts/ 全部 24 个文件 —— 知识库的「生产车间」）

> 分五组：**采集 6 → 治理 3 → 入库 1 → 验证 11 → 评测 2 → 工具 1**。这些脚本支撑了「93+ 份知识源 → 治理 → 入库 → 评测」的完整 RAG 数据链路。

**🕷️ 采集组（6 个）**

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 8️⃣-1 | `scripts/kb_crawl_common.py` | **采集公共库**：请求 / 解析 / 去重公共逻辑，四个 crawl_* 共用。与 fetch_kb 的分工：fetch_kb 是**清单驱动**（人工维护 kb_manifest.jsonl 92 条精选源），kb_crawl_common 是**列表页驱动**（自动遍历频道页）。思考：为什么两套采集？（清单驱动精而少，列表页驱动广而多，互补） |
| 8️⃣-2 | `scripts/crawl_laws.py` | ① laws 采集（法规）。docstring 有**踩坑记录**：最初用 Standard 频道，实测 254/315 是「附件下载页」（正文在 .doc/.pdf 附件里，治理后 md 仅十几个字），后换 Laws 频道。思考：这个坑说明了什么？（数据质量评估要在采集早期做，别等入库才发现） |
| 8️⃣-3 | `scripts/crawl_procedures.py` | ② procedures 采集（操作规程）。栏目选取对齐项目隐患模块语义（分行业安全管理 / 规章制度 / 隐患排查 / 风险管控）。 |
| 8️⃣-4 | `scripts/crawl_plans.py` | ③ plans 采集（应急预案）：安全管理网 Emergency 频道 + 应急管理部 www.mem.gov.cn（权威补充，D3 已验证可直连）。 |
| 8️⃣-5 | `scripts/crawl_cases.py` | ④ cases 采集（事故案例）。栏目选取对齐「五大伤害」（坍塌/高处坠落/起重伤害/物体打击/机械伤害/触电/车辆伤害）——正是公路桥隧工地高频事故，与隐患模块分类语义一致。 |
| 8️⃣-6 | `scripts/fetch_kb.py` | 清单驱动下载：按 kb_manifest.jsonl 下载到 knowledge_src/raw/<doc_type>/，支持按类型/ID 部分抓取。 |

**🧹 治理组（3 个）**

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 8️⃣-7 | `scripts/govern_html.py` | **HTML 治理（核心脚本）**：正文容器抽取 → 噪声词过滤（NOISE_WORDS）→ 去重行 → 标题清洗 → 长度阈值 2000 字标可疑 → 同目录同名 .md。思考：为什么治理要产出 .md 而不是直接入库？（治理是离线步骤，产出中间产物可审查可复用；.md 还被前端 kb.py 只读接口用来展示原文） |
| 8️⃣-8 | `scripts/clean_empty_kb.py` | **空壳文档清理**：删除治理后正文 <300 字的 md 及其源 htm。思考：为什么空壳必须清理？（附件下载页治理后只有标题，入库只产生噪声块，污染检索） |
| 8️⃣-9 | `scripts/kb_sample_check.py` | 抽样检查：四类目录各随机抽 3 份打印前几行，人工抽查治理质量。思考：为什么治理后要人工抽检？（自动化治理会误伤，抽样是质量门禁） |

**📥 入库组（1 个）**

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 8️⃣-10 | `scripts/collect_kb.py` | **知识库入库**：扫描四类目录 → 结构感知分块 → 嵌入 → 写入 Chroma safety_kb（支持全量 / 按类型 / 按文件）。思考：它和 src/ai/knowledge.py 的 upsert_chunks 什么关系？（collect_kb 是离线全量入库的 CLI 入口，knowledge.py 是它和在线贡献审核共用的入库内核） |

**🧪 验证组（11 个）**

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 8️⃣-11 | `scripts/db_verify.py` | **模型 vs 库一致性校验**（每次改表后必跑）：10 张表列名级对比，输出 [OK]/[DIFF]。思考：为什么以「模型查库」而非「库查模型」？（以库为准——库是 P3 建的，模型必须对齐库） |
| 8️⃣-12 | `scripts/ai_verify.py` | AI 基建冒烟：嵌入模型 / 重排模型 / Chroma safety_kb / LLM 客户端配置四项是否就绪。 |
| 8️⃣-13 | `scripts/llm_smoke.py` | LLM 链路冒烟：真实调用 DeepSeek（非流式 + 流式），验证 API Key / 网络 / 模型名。 |
| 8️⃣-14 | `scripts/prompt_smoke.py` | 提示词冒烟：加载 YAML → render 模板 → 真实 LLM 调用（安全问答场景）。 |
| 8️⃣-15 | `scripts/test_vision.py` | 视觉识别测试：调 qwen 分析指定图片（`test_vision.py "uploads/1.png"`）。 |
| 8️⃣-16 | `scripts/calib_vision.py` | **视觉坐标校准**：生成含已知位置红色矩形的测试图，调视觉模型对比 location 返回与真实位置，推断模型内部坐标系（缩放/固定尺寸/归一化偏差），确定画框换算策略。思考：为什么画框前必须校准？（视觉模型返回的坐标可能不是原图像素，直接画框会错位——这就是记忆里的 vision-bbox-calibration 坑） |
| 8️⃣-17 | `scripts/draw_boxes.py` | 画框 CLI：离线调用 utils/boxes.py 的 draw_hazard_boxes，供批量处理/离线验证。 |
| 8️⃣-18 | `scripts/test_ppt_api.py` | 百度 PPT API 验证：调 generate_ppt_by_outline（SSE），解析完整事件流，下载 .pptx 校验完整性。 |
| 8️⃣-19 | `scripts/verify_notify_flow.py` | **H08 通知链路端到端验证**（D5 A3）：注册上报人+责任人 → admin 上报 → 派单 → 验证责任人收到 → 整改 → 验证上报人收到 → 验收 → 验证全员。思考：为什么通知要端到端脚本验证？（消息联动跨 4 个角色 3 次流转，手测容易漏） |
| 8️⃣-20 | `scripts/verify_notify_types.py` | 消息类型丰富化验证：整改反馈→管理员 / 验收结果→安全员·管理员 / 最终处理结果→全员 三类新消息链路。 |
| 8️⃣-21 | `scripts/gen_ppt.py` | PPT 流水线演示（P01 大纲生成 + A/B 混合渲染）：prompts.yml ppt_outline → LLM 生成大纲 JSON → HTML 封面截图 + python-pptx 渲染。思考：它和 src/ai/ppt_local.py 的关系？（gen_ppt 是 D3 验证过的脚本，ppt_local 是提炼出的可调用模块） |

**📊 评测组（2 个）**

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 8️⃣-22 | `scripts/rag_metrics.py` | **六指标计算**（被 eval_qa 调用）：chunk_recall_at_k / chunk_precision_at_k / 命中率 / 生成侧相关率 / 引用准确率 / 最终答案分。注意各指标是否需要 LLM 裁判。 |
| 8️⃣-23 | `scripts/eval_qa.py` | **golden QA 评估**：100 题测试集，检索侧按跳数分组，生成侧 LLM 裁判。`--no-rewrite` / `--precision-at 4` 等参数。最终成绩 ALL@10 = 0.83（达标 P2 线 0.80）。思考：为什么评测要固定测试集 + 固定口径？（可复现、可对比迭代前后效果；RAG 好不好不能靠感觉，要指标说话） |

**🔧 工具组（1 个）**

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 8️⃣-24 | `scripts/init_admin.py` | 初始化 admin 账号（**唯一合法途径**）：默认 admin/admin123，已存在跳过不覆盖密码。思考：为什么 admin 不提供注册接口？（权限安全，管理员必须线下初始化） |

> 💡 学习心法：24 个脚本 = **数据生产线（采集→治理→入库）+ 质量门禁（verify/smoke/eval）+ 运维工具（init_admin）**。产品功能跑在 src/，数据资产生产在 scripts/——两条线互相独立又通过「knowledge_src/ + Chroma」衔接。

---

### 第 9 步：测试层（tests/ 全部 10 个文件）

> 测试分两类：**依赖真实 MySQL 的集成测试**（auth/users/hazards/uploads/stats_notifications）和 **纯函数单元测试**（exam/knowledge_contrib/user_profile/chat 用 monkeypatch）。

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 9️⃣-1 | `tests/test_health.py` | 冒烟：应用启动 + 健康检查。 |
| 9️⃣-2 | `tests/test_auth.py` | 认证闭环：注册（硬编码 employee）→ 登录 → /me 鉴权 → 无 token 拒绝 401。 |
| 9️⃣-3 | `tests/test_hazards.py` | **隐患全流程闭环**（最完整的集成测试）：上报→派单→整改→验收通过→已闭环；验收驳回→已驳回→重新派单→处理中；RBAC（employee 派单/验收 403、非责任人整改 403）；状态机（未派单直接整改 400、重复验收 400）；列表筛选。思考：为什么测试要覆盖非法流转？（状态机是业务核心，非法流转是最常见的 bug 源） |
| 9️⃣-4 | `tests/test_users.py` | 用户列表：admin 可调 / 排除自己 / 多角色过滤 / 非 admin 403。 |
| 9️⃣-5 | `tests/test_uploads.py` | 上传：白名单 / 大小限制 / 鉴权 / 静态访问。 |
| 9️⃣-6 | `tests/test_stats_notifications.py` | 统计空表返回完整结构（total=0 不崩）；通知在 **Redis 未启动时优雅返回空列表**（不 500）。思考：为什么专门测「Redis 挂了」？（通知模块的优雅降级是设计承诺，测试守护它） |
| 9️⃣-7 | `tests/test_chat.py` | 问答测试：**LLM 流式用 monkeypatch 模拟**（不依赖网络/费用）→ SSE 格式 / 会话创建 / 消息落库 / 多轮上下文 / 会话 CRUD / 归属校验 / 快捷提问。思考：为什么流式要 monkeypatch？（测试不能真调 LLM——慢、花钱、不稳定；mock 掉网络只测业务逻辑） |
| 9️⃣-8 | `tests/test_exam.py` | 考试工坊纯函数测试：exam_export 三格式渲染 + exam_agent 的 _extract_json 容错 / _validate_question 逐题校验 / 模板列表。 |
| 9️⃣-9 | `tests/test_knowledge_contrib.py` | 知识贡献纯函数测试：_extract_file_text（md/txt 双编码 / docx / pdf）、常量校验、预筛 JSON 容错、判定标准文档存在性。 |
| 9️⃣-10 | `tests/test_user_profile.py` | 个人主页测试：schemas 校验（用户名长度/密码最小/邮箱格式）+ service 邮箱/用户名校验（fake DB）+ 隐患过滤参数透传（fake Session 验证签名不报错）。 |

> 💡 学习心法：测试文件是「**验收口径的代码化**」——每个测试用例对应一个业务规则（RBAC、状态机、降级、格式校验）。跑 `pytest tests/` 全绿 = 核心业务没被改坏。

---

### 第 10 步：前端（front/safety/index.html）

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 🔟-1 | `front/safety/index.html` | **一个文件包含全部前端**（约 2300 行）：CSS 设计系统（--primary 川蜀红 / --gold 金 / uiverse 组件重着色）+ 登录/注册 + 工作台（欢迎横幅 + 9 张快捷入口大卡片）+ 隐患管理（列表/详情弹窗/时间线）+ AI 助手（会话侧栏 + SSE 流式渲染 + 引用来源）+ PPT 工坊 + 考试工坊 + 贡献知识（上传表单 + 审核弹窗 + 判定标准弹窗）+ 个人主页 + 通知中心。思考：为什么前端用单文件而不是多文件工程？（Vibe Coding 团队免构建直接静态托管，hash 路由实现 SPA；代价是文件大，需按区块阅读） |
| 🔟-2 | 前端关键函数 | `api()` 统一封装（token 注入 + 错误解析）、`route()` 哈希路由（`#/xxx` → show 对应 page）、`renderMsg`（SSE 流式渲染打字机效果）、`openModal/closeModal`（弹窗）、`applyUserAvatar()`（右上角头像）、`profileRenderKc/Hz`（个人主页贡献卡片）。思考：为什么前端所有 fetch 都走 api() 封装？（统一加 Authorization 头 + 统一错误处理，一处改全局生效） |

---

### 第 11 步：部署与收尾

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 🔢-1 | `deploy/docker-compose.yml` | MySQL / Redis / 后端容器编排（**尚未实测跑通，P4 部署线延后**）。 |
| 🔢-2 | `deploy/nginx/` + `deploy/README.md` | 反向代理配置 + 部署说明。 |
| 🔢-3 | `deploy/backend/` | 后端 Dockerfile 等。 |

---

> 💡 **全局学习心法**：这个项目**六条主线**——
> 1. **业务闭环**：hazard_service 状态机 + notification_service 通知 + hazard_timeline 时间线（knowledge_contrib / exam / ppt 都是它的变体）；
> 2. **RAG 链路**：crawl_* → govern_html → collect_kb → knowledge → rag → reranker → chat → eval_qa；
> 3. **子智能体**：exam_agent / knowledge_review_agent（复用 llm_client + prompts.yml → 调 LLM → 校验）；
> 4. **增量入库**：knowledge.upsert_chunks（不重建、doc 隔离、kb_govern 清洗治理前置）；
> 5. **双通道降级**：PPT（百度云 → ppt_local）、LLM（DeepSeek → agnes）、检索（BM25 + 向量 RRF）；
> 6. **表结构铁律**：schema.sql 唯一权威，db_verify 守护一致性，users 表零改动（user_profiles 一对一）。
> 把这六条主线记牢，这个项目你就读透了——**任何新模块都是「复用第 1 条的状态机范式 + 第 3 条的子智能体范式 + 第 4 条的入库范式」的组合**。

---

## 五、环境要求

- **Python 3.12**（全局 `C:\python`，3.11/3.12 均验证可跑；运行命令须加 `PYTHONPATH=` 前缀规避 hermes-agent venv 注入）
- **MySQL 8.x**（127.0.0.1:3306，库 `shudao_safety`，utf8mb4）
- **Redis 7.x**（127.0.0.1:6380，密码 123456，消息通道用；未启动时通知模块优雅降级）
- **NVIDIA GPU**（RTX 3050 + CUDA 12.9，嵌入/重排模型走 cuda；无 GPU 改 `EMBEDDING_DEVICE=cpu`）
- **本地模型**：`D:/LLM/model/bge-large-zh`（嵌入）、`bge-reranker-base`（重排）
- **Chroma 1.5.9**（集合 `safety_kb`）
- 操作系统：Windows（本项目在 Windows + git-bash 环境开发；前端静态文件 Linux 也可托管）

---

## 六、快速开始

### 1. 初始化数据库

```bash
cd team/database
mysql -uroot -p < schema.sql        # 建 10 张表
```

### 2. 配置环境变量

```bash
cd team/backend
# 编辑 .env（参考 src/core/config.py 的 Settings 字段）：
# SECRET_KEY / DATABASE_URL / REDIS_URL
# DEEPSEEK_API_KEY + DEEPSEEK_BASE_URL + DEEPSEEK_MODEL（主模型）
# AGNES_API_KEY + AGNES_BASE_URL + AGNES_MODEL（降级模型）
# QWEN_API_KEY（视觉识别） / BAIDU_PPT_*（PPT 专职 API）
# EMBEDDING_MODEL_PATH / RERANKER_MODEL_PATH / EMBEDDING_DEVICE=cuda
```

### 3. 启动 Redis（消息通道）

```bash
redis-server --port 6380 --requirepass 123456
```

### 4. 启动后端

```bash
cd team/backend
PYTHONPATH= /c/python/python.exe -m uvicorn src.main:app --reload --port 8000
```

### 5. 打开前端

浏览器访问 `http://127.0.0.1:8000/safety/`（静态文件由后端托管）。

### 6. 初始化管理员 + 冒烟验证

```bash
cd team/backend
PYTHONPATH= /c/python/python.exe scripts/init_admin.py      # 建 admin 账号
PYTHONPATH= /c/python/python.exe scripts/db_verify.py       # 10 表对齐校验
PYTHONPATH= /c/python/python.exe scripts/llm_smoke.py       # LLM 链路冒烟
PYTHONPATH= /c/python/python.exe scripts/ai_verify.py       # AI 能力验证
PYTHONPATH= /c/python/python.exe -m pytest tests/ -v        # 全量测试（可选）
```

### 7. 知识库（可选，含采集 + 评测）

```bash
cd team/backend
PYTHONPATH= /c/python/python.exe scripts/crawl_laws.py      # 采集（四类，各自独立跑）
PYTHONPATH= /c/python/python.exe scripts/govern_html.py     # 治理
PYTHONPATH= /c/python/python.exe scripts/clean_empty_kb.py  # 空壳清理
PYTHONPATH= /c/python/python.exe scripts/collect_kb.py      # 建库
PYTHONPATH= /c/python/python.exe scripts/eval_qa.py         # 100 题评测
PYTHONPATH= /c/python/python.exe scripts/rag_metrics.py     # 六指标
```

---

## 七、项目现状 & 下一步

### 已收官（D1–D5 + 优化丰富期全部完成）

- ✅ **D1**：项目骨架 + 认证 + 隐患上报雏形 + 数据库 10 表（P3 线）
- ✅ **D2**：隐患闭环 H01–H06 + 消息联动 H08 + 前端单页
- ✅ **D3**：RAG 知识库（采集 93 条 → 治理 → Chroma 入库）+ AI 问答
- ✅ **D4**：PPT 工坊（百度云 API + 自研降级）+ 视觉识别（bbox 校准）
- ✅ **D5**：知识库检索评估（ALL@10 = 0.83 达标）+ 前端 D5 美化 + 通知链路验证
- ✅ **优化丰富期**：考试工坊（exam_papers 单表）、知识贡献（上传文件 + AI 预筛 + 清洗治理 + 增量入库）、个人主页（user_profiles 一对一 + 贡献聚合 + 右上角头像 + 工作台快捷入口）
- ✅ **课程要求**：LangChain 1.x 重构（ChatOpenAI + with_fallbacks / HuggingFaceEmbeddings / langchain_chroma / RunnableLambda）

### 可选下一步

- 🚧 **Docker 部署**：`deploy/` 已备好 compose + nginx，尚未实测跑通（P4 部署线延后）
- 🚧 **Redis 消息实时化**：当前通知为「Redis 通道 + 前端轮询/角标」，可升级 WebSocket 实时推送
- 🚧 **多文件批量贡献**：知识贡献目前单文件上传，可扩展批量 + 拖拽
- 🚧 **头像裁剪 / 尺寸限制**：头像复用隐患图片 10MB 上限，可单独限 2MB + 前端裁剪
- 🚧 **评测集扩充**：golden QA 100 题可扩至 300+，覆盖更多事故类型

---

## 八、技术栈

**后端**：`fastapi` / `uvicorn` · `sqlalchemy` 2.x（Mapped ORM）· `mysql-connector-python` · `pydantic` · `bcrypt`（密码哈希，无 passlib）· `python-multipart`（文件上传）· `pydantic-settings`（配置）

**AI / RAG**：`langchain` 1.3.4 全套（`langchain-core` / `langchain-openai` / `langchain-chroma` / `langchain-community`）· `chromadb` 1.5.9 · `sentence-transformers`（bge-large-zh 嵌入）· `bge-reranker-base`（重排）· `jieba`（BM25 分词）· `pymupdf`（PDF 解析）· `python-docx`（DOCX 解析）· `beautifulsoup4`（HTML 治理）· `openai` SDK（模型调用）· `reportlab`（PDF 导出）

**模型**：DeepSeek（主，deepseek-v4-flash）· agnes（降级）· qwen（视觉）· 百度千帆 PPT 专职 API（每日 10 次免费 + 自研兜底）

**中间件**：`MySQL 8` · `Redis 7` · `Chroma 1.5.9` · 本地 bge 模型（GPU/cuda）

**前端**：原生 HTML/CSS/JS 单页应用（川蜀红色系设计系统，uiverse 组件重着色，hash 路由，SSE 流式问答）
