# v2.0 · 02 PPT 工坊升级 · 裁定

> 本文件只放**选型 / 取舍 / 为什么这样定**。方案正文在 [`../v2.0规划文档.md`](../v2.0规划文档.md) §02；事实与实测证据在 [`../research/02-PPT工坊升级.md`](../research/02-PPT工坊升级.md)。
> 裁定 1–8 直接来自用户 2026-10-23 的口述（逐字见 research 话题 6.1）；裁定 9–12 是实现级取舍，依据为 research 话题 1–6 的实测。

---

## 裁定 1（2026-10-23）：**整体移除** 百度 PPT LLM 链路（13 处落点），不做"保留为降级兜底"

| 项 | 内容 |
|---|---|
| **分叉点** | 百度接口"每日 10 次免费"政策是否还在；不在的话，是"保留但降级为可选"还是"彻底移除"。 |
| **可选方案** | **A** 原样保留（额度共享导致体验崩塌）；**B** 保留实现但默认关闭（代码里继续维护一份无用的百度客户端）；**C** 整体移除（配置键、客户端、分支、脚本、前端文案一并删）。 |
| **裁定结果** | **C** |
| **依据** | ① 用户核实该接口已转为付费（原话：「调研过了,就是变成付费接口了,直接移除」）；② 额度是项目级共享（单个 `.env` key，请求体无用户标识，全仓无计数/无密钥池），保留也无法解决"一个人用完全公司没得用"；③ 保留实现要持续跟随百度接口演进（`style_id/tpl_id`、`query_id/chat_id` 上下文依赖已出现串台），维护成本大于收益。 |
| **影响面** | 规划文档 §02 §3.12 列了 13 处（`ppt_baidu.py` 整文件 186 行、`ppt_service.py` import 与分支、`is_custom_template()`、`config.py` 4 键、`.env` 4 行、`schemas/ppt.py` 三内置模板、`scripts/test_ppt_api.py`、前端 3 处文案）；验收项 1 要求 `grep -i baidu_ppt` = 0。 |

---

## 裁定 2（2026-10-23）：保留"可选直连 PPT LLM"的**抽象位**，但当前无实现、默认关闭

| 项 | 内容 |
|---|---|
| **分叉点** | 用户要"两种工作方式"，但唯一实现（百度）已移除 —— 是连"方式 A"这个入口一起删掉，还是留接口位？ |
| **可选方案** | **A** 不留入口（以后要接得重做架构）；**B** 留一个空接口 + 配置位（当前无 provider，前端不显示该选项）；**C** 现在就接一家第三方付费 PPT LLM。 |
| **裁定结果** | **B** |
| **依据** | ① 用户明确"把 ppt llm 变成可选链路项"；② C 需要先确定厂商与预算，调研期没有任何验证；③ B 的成本只有 `ppt_llm.py` 一个接口文件 + 3 个配置键，不会进入热路径。 |
| **影响面** | 规划文档 §02 §3.1/§3.13；`GET /api/ppt/capabilities` 回报当前可用链路，前端按此隐藏"直连 PPT LLM"选项。 |

---

## 裁定 3（2026-10-23）：默认链路 = 常驻 PPT 生成 Agent，编排用**"代码驱动阶段 + Agent 决策单页"**混合式

| 项 | 内容 |
|---|---|
| **分叉点** | 草案 (2)→(7) 是一条多阶段流水线。是交给一个"自由 Agent"自己决定走哪些步，还是由代码固定阶段、只在阶段内让模型决策？ |
| **可选方案** | **A** 纯自由 Agent（所有阶段都是工具，模型自行规划）；**B** 混合式（代码按 S0–S8 推进，Agent 只负责每页的内容与版式，工具在页内循环）；**C** 纯脚本（无 Agent，模板化填空）。 |
| **裁定结果** | **B** |
| **依据** | ① A 的三类风险：阶段漏做（跳过充分性判定）、成本不可控（每阶段都可能多轮）、进度条无法映射到具体阶段；② C 无法完成"按内容选图/改版式/看截图修真"这类需要判断的动作；③ B 让进度条天然对齐 S0–S8，不确定性被限制在"单页内"，且每页最多 2 轮修补可硬性约束。 |
| **影响面** | 规划文档 §02 §3.2（阶段与权重）、§3.4（编排与工具清单）；实现落在 `src/ai/ppt_agent.py` + `src/ai/ppt_tools.py`。 |

---

## 裁定 4（2026-10-23）：账号沙箱 = `user_data/ppt/{user_id}/`，路径由**唯一事实源** `paths.py` 提供

| 项 | 内容 |
|---|---|
| **分叉点** | 目录按 `username` 还是 `user_id` 分；路径是否集中管理。 |
| **可选方案** | **A** 沿用平铺 `ppt_files/`（现状，无隔离）；**B** 按 `username` 分（可读但改名即失联）；**C** 按 `user_id` 分 + `paths.py` 归口（仿 `deep_search_pro/utils/paths.py`）。 |
| **裁定结果** | **C**（用户原话：「2.user_id」） |
| **依据** | ① 本系统账号可改名（`user_profiles` 支持改用户名），目录一旦落盘就失联；② JWT 里本来就有 `user_id`（整数），按整数拼路径天然免疫 `..`/分隔符注入；③ `paths.py` 解决 deep_search_pro 记录过的"数据散在 7 个根、三份目录清单互不一致、写进 A 读的是 B"的老问题。 |
| **影响面** | 规划文档 §02 §3.3；`safe_join()` 与"模型给的字符串只做 basename"写进实现约束（验证项 6 专测越权）。历史 `ppt_files/` 产物不迁移，下载端点双路径兼容。 |

---

## 裁定 5（2026-10-23）：多模态用**独立配置段** `PPT_VISION_*`，默认 AGNES；`reasoning_content` 一律丢弃

| 项 | 内容 |
|---|---|
| **分叉点** | 视觉能力是复用主模型（纯文本，不可行）、复用现有 `QWEN_*` 段，还是新开一段？ |
| **可选方案** | **A** 复用 `QWEN_*`（当前值指向 DeepSeek 视觉实验模型，语义会混）；**B** 新开 `PPT_VISION_*`，默认 AGNES，`PROVIDER` 字段预留多实现；**C** 写死 AGNES 的 base_url/model。 |
| **裁定结果** | **B**（用户原话：「3.Agenes,后面我也会接其他视觉模型」） |
| **依据** | ① 实测 AGNES 能看图：320×120 测试图（红块 + `SHUDAO-7391`）两张（直连/代理）均返回 `200`，答案 `SHUDAO-7391,红色` 全对；② 实测坑：它先输出 `reasoning_content`（180–215 字符），`max_tokens=40` 时 `finish_reason=length` 且 `content` 为空 —— 必须放宽预算并只读 `content`；③ 用户明确后续要接别的视觉模型 → 写死不可取。 |
| **影响面** | 规划文档 §02 §3.5/§3.13（`PPT_VISION_MAX_TOKENS` 默认 800）；实现 `src/ai/vision_agent.py`，独立 `ChatOpenAI`，不参与文本生成。 |

---

## 裁定 6（2026-10-23）：图片源 = **Openverse 单源**，但必须内建"失败即降级"

| 项 | 内容 |
|---|---|
| **分叉点** | 图片从哪来：国内正版图库 / 国外免费 API / 干脆不要图。 |
| **可选方案** | **A** 视觉中国等国内图库（`403` WAF + `robots` 禁爬 + 付费授权）；**B** Openverse（无需 key）+ Unsplash/Pexels/Pixabay（需 key）多源；**C** Openverse 单源 + 图标兜底。 |
| **裁定结果** | **C**（用户原话：「1.接收使用Openverse API源」） |
| **依据** | ① A 已被实测排除（付费 + 禁爬）；② 用户选了 Openverse；③ **但实测 Openverse 可达性不稳定**：同一端点 30 分钟内从 `200/240 条` 变为 `ConnectTimeout`/`ReadTimeout` 连续失败 —— 因此必须"超时 20s、重试 1 次、失败降级为图标/形状版式"，否则单张图失败会让整单失败；④ Iconify API 与 jsDelivr 实测 `200`，MIT/Apache，可作零授权风险的兜底。 |
| **影响面** | 规划文档 §02 §3.7；配置 `PPT_IMAGES_ENABLED`；验证项 5（断网/超时整单仍成功）。CC BY/BY-SA 需署名，`images/sources.json` 逐条记录 `license/creator/attribution`，末页出"图片来源"。 |

---

## 裁定 7（2026-10-23）：模板**由用户提供**，内置只保留 `builtin_simple` 一套兜底版式

| 项 | 内容 |
|---|---|
| **分叉点** | 模板从哪来：Agent 网搜下载 / 用户上传 / 内置若干套。 |
| **可选方案** | **A** Agent 网搜下载（实测：第一PPT 反爬 + 无直链 + 商用授权含糊；SlidesCarnival/SlidesGo 需署名或账号）；**B** 用户上传 + 无模板时内置兜底；**C** 继续维护"商务/简约/科技"三套（实际同一套）。 |
| **裁定结果** | **B**（用户原话：「7.Agentppt模版下载实现难的话,那就不麻烦Agent下载,要求用户自提供」） |
| **依据** | ① A 的取证结论：国内免费模板站自动化不可行且授权不清（research §5.4）；② C 已被实测证伪（三份产物主题色完全一致，`tpl_id` 都指向 102322）；③ B 有现成实现（`ppt_template_service.upload_template` 已有扩展名/大小/python-pptx 可打开校验）。 |
| **影响面** | 规划文档 §02 §3.8/§3.11；用户模板仍存共享库（00 的口径：团队共享），任务开始时复制副本到沙箱再改，避免多人同时改同一文件。 |

---

## 裁定 8（2026-10-23）：目标 pptx 的**唯一写者 = OfficeCLI**；`python-pptx` 仅作无 OfficeCLI 时的兜底渲染器

| 项 | 内容 |
|---|---|
| **分叉点** | 逐页编辑用 OfficeCLI，而现有兜底渲染用 python-pptx；两者都能写同一份 pptx。 |
| **可选方案** | **A** 两者都用（先 python-pptx 打底，再 OfficeCLI 修）——同一文件两个写入方；**B** OfficeCLI 独占写，python-pptx 只在 OfficeCLI 不可用时整体兜底，**同一任务只走一条**；**C** 全用 python-pptx（无截图/无 issues 自检能力）。 |
| **裁定结果** | **B** |
| **依据** | ① A 会让"谁最后写"变成隐式约定，`officecli` 常驻进程还会延迟 flush（空闲 2–10 s 自动落盘），极易互相覆盖；② OfficeCLI 实测具备 `add/set/remove/move/swap/batch` + `view screenshot` + `view issues` + `validate` + 常驻进程，正好覆盖"逐页改 + 自检"；③ 本机 PowerPoint 与 pywin32 均在，`--render auto` 走 native 渲染，不需要 LibreOffice。 |
| **影响面** | 规划文档 §02 §3.6/§3.8；验证项 7（OfficeCLI 缺失自动兜底）；`PPT_OFFICECLI_PATH` 置空即强制兜底。 |

---

## 裁定 9（2026-10-23）：进度通道 = **SSE**，状态落 Redis，**不改 `ppt_tasks` 表**

| 项 | 内容 |
|---|---|
| **分叉点** | 长流程进度怎么传给前端：SSE / 轮询 / WebSocket；状态落 DB 还是 Redis。 |
| **可选方案** | **A** SSE（沿用问答链路的 `text/event-stream` 范式）；**B** 轮询（新增 DB 字段）；**C** WebSocket（本项目无先例）。 |
| **裁定结果** | **A + Redis**（用户原话：「4,进度通道 SSE」） |
| **依据** | ① 问答链路已有 SSE 实现与前端读流代码（`routers/chat.py` + `index.html` 的 `getReader()` 循环），复用成本最低；② 改表要动 `database/schema.sql`（P3 管理，00/01 一贯不改）；③ Redis 已有客户端与降级惯例（01 大点），进度 key 带 TTL 不会堆积。 |
| **影响面** | 规划文档 §02 §3.9；新端点 `POST /api/ppt/agent/generate` 与 `GET /api/ppt/{id}/progress`；`ppt_tasks.status` 继续只写四态。断线/刷新靠 progress 接口恢复。 |

---

## 裁定 10（2026-10-23）：中途产物算**用户可见资产**，提供**自清理**端点，成品默认保留

| 项 | 内容 |
|---|---|
| **分叉点** | 下载的图片、逐页截图、中间 JSON 是"临时文件"（自动删）还是"用户资产"（可查可删）？ |
| **可选方案** | **A** 任务结束即清（用户看不到过程，磁盘省）；**B** 全部保留由用户自清理（用户原话：「5.算,用户可以自清理」）；**C** 保留但只给管理员清。 |
| **裁定结果** | **B** |
| **依据** | ① 用户明确要"能看到自己的工作区"；② 逐页截图与审计日志是"为什么这页长这样"的唯一证据；③ 磁盘风险用配额+清理端点控制（图片 ≤20 张/单、清理只删 `templates/images/shots/drafts/logs`，`out/` 默认保留）。 |
| **影响面** | 规划文档 §02 §3.3/§3.11；新增 `DELETE /api/ppt/{id}/workspace`（可带 `keep_out=true`）；验证项 8。 |

---

## 裁定 11（2026-10-23，实现级）：兜底渲染器必须修掉三处"静默劣化"

| 项 | 内容 |
|---|---|
| **分叉点** | 老 `ppt_local.py` 的三个已知缺陷要不要在本次一并修。 |
| **可选方案** | **A** 不动（Agent 链路为主，兜底很少用）；**B** 修掉：要点截断 `points[:5]`、目录页 6 章节溢出、三套模板同款。 |
| **裁定结果** | **B** |
| **依据** | 兜底链路是 `PPT_AGENT_ENABLED=false` 与"OfficeCLI 缺失"时的唯一出路，用户会真的走到；且"静默丢内容/内容出界"是最伤信任的缺陷（用户不知道自己的要点被丢了）。 |
| **影响面** | 规划文档 §02 §3.8/§3.17 第 7 步；`builtin_simple` 只保留一套版式并显式命名（不再假装三套）。 |

---

## 裁定 12（2026-10-23，实现级）：并发与卡死治理 —— 状态守卫 + 超时兜底

| 项 | 内容 |
|---|---|
| **分叉点** | 现状：读状态→写"生成中"不是原子操作，两个请求可同时通过；进程中断后任务永久停留"生成中"，编辑/生成/删除全部 400。 |
| **可选方案** | **A** 加行级锁（`SELECT ... FOR UPDATE`）；**B** 用 Redis `SETNX` 抢任务锁 + 整单超时（`PPT_AGENT_TOTAL_TIMEOUT_S`）+ 启动时/查询时把超时任务置"失败"；**C** 不管（现状）。 |
| **裁定结果** | **B** |
| **依据** | ① 不改表、不用 DB 锁，避免与 P3 的 schema 与其他模块的事务边界纠缠；② Redis 已有客户端与降级路径；③ 超时治理必须有：否则一次崩溃就让用户"这个任务再也动不了"（research 话题 3 的 A2）。 |
| **影响面** | 规划文档 §02 §3.9/§3.15 验证项 3；实现里抢锁失败返回 409「该任务正在生成中」。 |

---

## 裁定 13（2026-10-24）：图源由 **Openverse 切换为 Unsplash**（默认），Openverse 保留为可配备选

| 项 | 内容 |
|---|---|
| **分叉点** | 裁定 6 定的是"Openverse 单源"；实测该源在本机**完全不可用**，配图功能等于没有。 |
| **可选方案** | **A** 继续用 Openverse（等网络好转）；**B** 换成用户新申请 key 的 Unsplash（`PPT_IMAGE_PROVIDER=unsplash`）；**C** 自建图库/接国内图床。 |
| **裁定结果** | **B**，并且把图源做成 `PPT_IMAGE_PROVIDER` 可配（`unsplash` / `openverse`），Openverse 代码保留。用户 2026-10-24 口令：「我刚刚申请了 unsplash 图库的 api key……尝试一下这个图库的稳定性，比当前的要强的话就换成这个」。 |
| **依据（同机同网络实测，2026-10-24）** | ① **检索**：Unsplash 5 次查询 **4 次 200**（第 5 次是"confined space entry"本就 0 结果），单次 **1.69~1.85s**；Openverse 4 次尝试 **0 次成功**（直连 `ConnectTimeout` 42.7s、走代理 `ReadTimeout` 31.6s）。② **下载**：Unsplash 图片 `200 / 87 KB / 1.53s`；Openverse 无样本。③ **落地**：换源后 10 页端到端**配图 3 张、生成"图片来源与授权"页、pptx 550 KB（无图时 21 KB）**、`validate` 无问题。 |
| **已知约束** | Unsplash **Demo 应用限 50 次/小时**（响应头 `x-ratelimit-remaining` 实测 49→40）；一单 10 页 ≈ 3 次检索 + 3 次下载事件 = 6 次请求 → 约 8 单/小时。代码在剩余 ≤3 时 `warning`，遇 401/403/429 直接降级为无图版式（不重试、不阻塞）。申请 Production 后可到 5000/h。 |
| **影响面** | `src/ai/ppt_tools.py`（`current_provider()` + `_unsplash_candidates()` + `_openverse_candidates()`，字段映射与 `sources.json` 共用）；`config.py` 新增 `UNSPLASH_APP_ID/ACCESS_KEY/SECRET_KEY` 与 `PPT_IMAGE_PROVIDER`（默认 `unsplash`，无 key 自动回退 openverse）；`/api/ppt/capabilities` 增 `image_provider`；实现记录里的"配图未跑通"一条随之关闭。 |

---

## 裁定 14（2026-10-24）：Unsplash 合规口径 —— 不托管原图、署名必须可点击、回链带 UTM

| 项 | 内容 |
|---|---|
| **触发** | 用户提供 Unsplash 生产权限审核清单（Hotlink / Trigger downloads / 品牌 / 署名可点击 / 截图），要求自查并整改。 |
| **分叉点** | ① 课件内嵌 Unsplash 照片算不算"未热链"？② 界面要不要展示原图？③ 署名用纯文本还是超链接？ |
| **裁定结果** | ① 保持"照片只进课件、不在 UI 展示"：**我们不托管、不对外提供图片 URL**（`assets` 端点只放行 `shots`，`/sources` 只回署名元数据）；② 所有署名**必须是超链接**（课件图注、课件来源页、应用界面三层都有 `hlinkClick`/`<a>`）；③ 回链只给 `https://unsplash.com/` 页面加 `utm_source=shudao_safety&utm_medium=referral`，**绝不给 `images.unsplash.com` CDN 加参数**；④ 每张图落盘后必须打 `download_location` 事件。 |
| **依据** | 清单原文第 1（Hotlink）、第 2（Trigger downloads）、第 6（Attribute photographer and Unsplash，示例 "Photo by Annie Spratt on Unsplash" 且要求 linked）。我们不改产品形态（PPT 生成器必须把图嵌进文件），改为在"不展示即不托管 + 三层可点击署名 + 下载事件 + UTM"上做到无可指摘，并在申请描述里如实说明"照片仅作课件配图、应用不提供图片浏览"。 |
| **影响面** | `ppt_tools.py`（`creator_url/photo_url`、`_with_utm`、`apply_hyperlinks`、回执解析、元数据剥离）、`ppt_layout.py`（图注与来源页可点击）、`ppt_agent.py`（挂链）、`routers/ppt.py`（assets 只放 `shots`、新增 `/sources`）、前端「图片来源」列表；自查报告与申请文案见 `../Done/02-Unsplash合规自查.md`。 |
| **残留风险（已明示用户）** | 若审核方认定"内嵌文件"也不允许，则只能改成"课件留占位框、用户自行插图"——那属产品形态变更，需用户拍板。 |

---

## 裁定 15（2026-10-24）：OfficeCLI **随项目分发**，默认零配置；仓库直接收纳 32 MB 二进制

| 项 | 内容 |
|---|---|
| **触发** | 用户 2026-10-24：「我在 .env 看到你引用的是本机磁盘下的路径，我的想法是把 OfficeCLI.exe 复制一份进项目……不需要其他人 clone 项目时再去专门下一份并在 env 写地址」。 |
| **分叉点** | ① 继续让每个人自备 OfficeCLI 并在 `.env` 写绝对路径；② 把二进制放进仓库；③ 放 Release 附件手工解压。 |
| **裁定结果** | **②**：`team/tools/officecli/officecli.exe`（31.96 MB，v1.0.158）随仓库分发；`PPT_OFFICECLI_PATH` 默认改为**空**，代码按三级顺序自动定位：`.env` 显式值（相对路径按**项目根**解析）→ 随项目分发的 `tools/officecli/officecli.exe` → 项目根 `officecli.exe` / 本机旧安装路径（仅兼容老环境）。都找不到时返回 None，上层降级到 python-pptx 兜底渲染。 |
| **依据** | ① 用户明确选"接受体积"（选项 A，直接提交，不走 Git LFS）；② 单文件 31.96 MB < GitHub 100 MB 单文件上限；③ 三级定位保证老机器（`D:\appadte\OfficeCLI`）与新克隆（`tools/officecli`）都不炸；④ 相对路径按项目根解析，避免"从别的 CWD 启动就找不到"的隐患。 |
| **影响面** | 新增 `team/tools/officecli/officecli.exe`；`src/core/config.py` 默认值置空；`src/ai/ppt_tools.py` 新增 `officecli_path()/PROJECT_ROOT/VENDORED_OFFICECLI/LEGACY_OFFICECLI`，`_oci()` 改用解析结果；`/api/ppt/capabilities` 增加 `officecli_path`；`.env` 删掉本机绝对路径注释（含一处被转义写坏的 `D:\u0007ppadte\...`）。 |
| **验证** | `pytest tests/test_ppt_agent.py` → **47 passed**（新增 3 条定位用例）；不配任何 `.env` 时 `capabilities.officecli_path` 指向项目内 exe；真机 3 页生成（CPU 检索）252.1s、`validate` 通过、`S7 问题 0 条`、下载 99,977 B。 |

---

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-23 | 建立本文件；落裁定 1–12。裁定 1–8 依据用户 2026-10-23 口述（research 话题 6.1 原话）；裁定 9–12 为实现级取舍，依据 research 话题 1–6 的实测 |
| 2026-10-24 | 追加裁定 13：图源由 Openverse 切为 Unsplash（用户申请 key 后实测 4/5 成功 vs Openverse 0/4），Openverse 保留为 `PPT_IMAGE_PROVIDER` 备选；Demo 配额 50 次/小时与降级策略一并记录 |
| 2026-10-24 | 追加裁定 15：OfficeCLI 随项目分发（tools/officecli，31.96 MB）并把路径解析改为三级自动定位；`PPT_OFFICECLI_PATH` 默认空，clone 即用 |
| 2026-10-24 | 追加裁定 14：Unsplash 生产审核合规口径（不托管原图 / 署名可点击 / 回链 UTM / download 事件），并完成整改与自查报告 `Done/02-Unsplash合规自查.md` |
