# v2.0 · 02 PPT 工坊升级 · 实现记录

> 方案权威：[`../v2.0规划文档.md`](../v2.0规划文档.md) §02；裁定：[`../Verdict/02-PPT工坊升级.md`](../Verdict/02-PPT工坊升级.md)；
> 取证：[`../research/02-PPT工坊升级.md`](../research/02-PPT工坊升级.md)。
> 本文件只记「**改了什么、实测多少、与方案差在哪、还缺什么**」，不重复方案原文。
> Unsplash 生产审核的逐条合规自查、整改与申请截图：[`02-Unsplash合规自查.md`](02-Unsplash合规自查.md)（裁定 14）。

**状态：实现完成（2026-10-24）**。百度 PPT LLM 已整体移除；默认链路是本地 PPT 生成 Agent（S0–S8 阶段、逐页 OfficeCLI 制作 + AGNES 审图 + Openverse 配图 + SSE 进度 + 账号沙箱）；用户模板与"内置简洁"兜底版式可用；快速模式（无 Agent）保留。

---

## 一、交付物

### 1. 新增（8 个源文件 + 1 个测试）

| 文件 | 行数 | 作用 |
|---|---|---|
| `src/core/paths.py` | 153 | **沙箱路径唯一事实源**：`user_root/task_root/task_dir/out_path/log_path`、`safe_name/safe_join/is_within`、`cleanup_task`。只给路径不建目录 |
| `src/ai/ppt_agent.py` | 651 | **生成 Agent**：S0–S8 阶段编排、页数预算（`_alloc`）、规划兜底与重排（`_rebalance`/`_ensure_sections`）、逐页闭环（渲染→审图→修补）、JSONL 日志、OfficeCLI 缺失时的兜底分支 |
| `src/ai/ppt_layout.py` | 239 | **版式渲染**：把页面规格翻成 OfficeCLI batch 命令（封面/目录/章节/内容/来源/结尾），主题色可覆盖，支持 `_fix`（缩字号/减要点/去图） |
| `src/ai/ppt_tools.py` | 408 | **工具实现**：OfficeCLI（create/batch/screenshot/issues/validate/release）、**多图源**（`current_provider()`：Unsplash 默认 / Openverse 备选，字段映射共用一套下载器）、`kb_search`、沙箱文件读取 |
| `src/ai/vision_agent.py` | 137 | **AGNES 审图**：独立多模态段（`PPT_VISION_*`），只取 `content` 丢弃 `reasoning_content`，异常一律不阻断生成 |
| `src/ai/ppt_llm.py` | 71 | **方式 A 抽象位**：`PptLlmProvider` 协议 + 注册表 + `capabilities()`；当前无 provider（百度已删） |
| `src/services/ppt_progress.py` | 107 | **进度与任务锁**：Redis `shudao:ppt:progress:{id}` / `shudao:ppt:lock:{id}`，Redis 不可用全降级 |
| `team/tools/officecli/officecli.exe` | 31.96 MB | **随项目分发的 PowerPoint 编辑器**（v1.0.158；`create/batch/screenshot/issues/validate` 都由它执行）。clone 即用，不需要每人下载、也不需要写 `.env` |
| `tests/test_ppt_agent.py` | 578 | 35 个用例：沙箱/版式/编排/配图（含 Unsplash 映射与配额降级）/进度锁/下载/接口 |
| `scripts/ppt_e2e_probe.py` | 120 | 真机 HTTP 全链路探针（本文所有实测数据由它产出；复跑：`PYTHONPATH= C:\python\python.exe scripts/ppt_e2e_probe.py 8011 10`） |

### 2. 重写 / 大改

| 文件 | 变化 |
|---|---|
| `src/services/ppt_service.py` | 390 行重写：去掉百度分支；新增 `start_agent/finish_agent/fail_agent/_build_spec`（含模板副本）、`workspace/cleanup_workspace`、卡死治理 `_reap_if_stale`、下载双路径（沙箱优先 → 历史 `ppt_files/`） |
| `src/routers/ppt.py` | 290 行重写：新增 `GET /capabilities`、`POST /{id}/agent`（SSE）、`GET /{id}/progress`、`GET|DELETE /{id}/workspace`、`GET /{id}/assets/{kind}/{name}`（仅本人 + `safe_join`）；`/generate` 改快速模式 |
| `src/ai/ppt_local.py` | 233 行重写为**兜底渲染器**：要点不再 `[:5]` 截断（>5 双栏、>10 才截断且 warning）、目录分两栏、按条数降字号 |
| `src/schemas/ppt.py` | 内置模板收敛为 `builtin_simple`；新增 `PptAgentSpec`（topic/requirements/md_content/pages/words_per_page/allow_overflow/template） |
| `src/services/ppt_template_service.py` | 内置 1 套；`is_custom_template` 不再参与选链路（保留函数） |
| `src/core/config.py` | 删 `BAIDU_PPT_*` 4 项；新增 `PPT_*` 22 项（含 `PPT_VISION_*` 视觉段、`PPT_IMAGE_API_BASE` 可换镜像） |
| `front/safety/index.html` | PPT 页：表单 6 项 + 进度卡（进度条/阶段/日志/缩略图/下载/清理）+ `capabilities` 提示；任务行加「🚀 生成 PPT / 清理」；三处百度文案清除；缩略图改为**带鉴权 fetch → blob**（`<img src>` 带不上 Authorization，实测 6 张图全 401） |
| `.env` | 删 4 行 `BAIDU_PPT_*`，加 PPT 工坊注释块（视觉默认沿用 AGNES key） |
| `.gitignore` | 新增 `backend/user_data/` |

### 3. 删除

- `src/ai/ppt_baidu.py`（186 行，整文件）
- `scripts/test_ppt_api.py`（百度专用测试脚本）
- 全仓 `grep -i baidu_ppt` = 0（含前端）

---

## 二、实测数据（真机，2026-10-24）

环境：Python 3.12.4、MySQL 3306、Redis 6380、RTX 3050 Laptop 4GB、OfficeCLI `D:\appadte\OfficeCLI\officecli.exe`、AGNES `agnes-2.5-flash`、端口 8011（自起，用毕关闭）。

| # | 场景 | 页数 | 耗时 | 结果 |
|---|---|---|---|---|
| 1 | HTTP 全链路（6 页） | 5 | 86.9s | validate 通过；配图因 Openverse 超时降级；下载 15.5 KB |
| 2 | HTTP 全链路（8 页，未修预算前） | 5 | 119.6s | 暴露「页数预算把小页数算成 0 内容页」的缺陷 |
| 3 | HTTP 全链路（8 页，修预算后） | 7 | 186.7s | 少 1 页：图片来源页因无图被移除，补偿逻辑当时要求 ≥4 条才拆页 |
| 4 | HTTP 全链路（10 页） | **10** | **221.5s** | 严格命中目标页数；21 KB；24 条事件；validate 通过 |
| 5 | 浏览器 UI（6 页，Playwright） | 6 | 141.3s | 进度序列 `[3,10,25,40,50,56,63,70,76,83,90,100]`、6 张缩略图、下载 16,942 B、**控制台 0 报错** |
| 6 | 离线编排（OfficeCLI 置空） | 7 | 63s | 走 python-pptx 兜底：`done.fallback=true`，36 KB（验证项 7） |
| 7 | **换 Unsplash 后**全链路（Redis 在线） | 10 | **146.1s** | 配图 **3 张**、生成「图片来源与授权」页（第 9 页）、pptx **550 KB**（无图 21 KB）、`validate` 通过；进度接口回读 S8/100/updated（Redis 生效） |
| 9 | **不配 `.env` 路径**、用项目内 OfficeCLI 生成（CPU 检索） | 5 | 252.1s | `capabilities.officecli_path` → `team\tools\officecli\officecli.exe`；`validate` 通过、`S7 问题 0 条`、下载 99,977 B（证明"clone 即用"） |
| 8 | 换源前的同场景（Redis 关闭） | 10 | 192.5s | 三张图被写成同一个文件名（后修的 bug），来源页 3 条却指向同一张图；进度接口退化返回空 stage（裁定 9 的降级路径） |

关键分段（10 页那次）：S0 预处理 <1s（规则快路径）→ S1 检索 + 补检 ~5s（预热已完成）→ S4 大纲 1 次 LLM ~20s → S5 配图 2 次超时 ~61s（含重试）→ S6 逐页 10 页 ~120s（每页 ≈12s：batch 1~2s + native 截图 1~2s + AGNES 审图 ~8s）→ S7 validate <1s。

冷启动（启动后台预热，与 01 共用链路）：`corpus 8.4s + bm25 15.4s + embedding 14.9~54.4s + reranker 3.8~27.7s = 42.5~107.6s`；预热未完成时提问会被 `_await_warmup` 拦住（01 已记录）。

---

## 三、与方案的偏差（都是实现期发现，已在代码注释里写明原因）

1. **沙箱层级多一层任务目录**：方案写 `user_data/ppt/{uid}/{images,shots,...}`，实现为
   `user_data/ppt/u{uid}/templates/`（账号级）+ `user_data/ppt/u{uid}/tasks/t{tid}/{images,shots,drafts,logs,out}`（任务级）。
   原因：方案写法下多任务会互相覆盖截图与中间稿；模板是账号级复用，不该每任务复制一份。
2. **模板只继承配色/字体，不继承母版版式**：方案写"按模板版式新增页面"，实现只从 `ppt/theme/theme1.xml` 读 `dk2/accent1` 覆盖主色与强调色。
   原因：OfficeCLI 的 `add slide` 走 Blank 版式，要真正用模板母版需按占位符逐个映射（工作量大且易出错）；当前效果是"配色像你的模板"，属于**已知未做**（见 §五）。
3. **图标兜底未实现**：方案写 Iconify/jsDelivr 取 SVG，实际降级为**纯形状版式**（无图时页面用色块/线条）。
   原因：本机没有可用的 SVG→PNG 光栅化（cairosvg 未装），而 OfficeCLI 的 `picture` 需要栅格图。
4. **页数预算重写了两轮**：方案只写"目标页数"。实测发现两种失败：① 3 章节挤 6 页 → 内容页算出 0；② 图片来源页因无图被删 → 少 1 页。
   现在的口径：`_alloc()` 保证"每章至少 1 页"，目标 ≤7 页时**不排章节过渡页**（页数留给内容）；无图删来源页后，把 ≥6 条要点的内容页对半拆（3+3），不编造内容；素材实在不够时诚实少页并在事件里给出 `page_target` 与 `pages`。
5. **补了一块方案没写的东西**：OfficeCLI 常驻进程会**锁住 pptx**（`close` 后仍有几百毫秒锁窗）。
   `create_deck` 现在先 `release_file()`（close + 轮询等句柄释放），删任务也先释放；否则第二次生成同一任务会 `Device or resource busy`（实测踩过）。

6. **图源由 Openverse 换成 Unsplash**（裁定 13，用户 2026-10-24 授权）：Openverse 在本机 0/4 成功，Unsplash 4/5、约 1.8s、图片可下载。图源做成 `PPT_IMAGE_PROVIDER` 可配，Openverse 代码保留。
7. **换源时连带修掉两个真机 bug**：
   - **图片文件名互相覆盖**：原命名 `img{序号}_{候选序号}` 每次调用都从 01 开始，三个关键词的图全写成 `img01_0.jpg` → 来源页 3 条指向同一张图。改为 `img{序号}_{关键词slug}_{uuid6}`。
   - **图片被压扁**：版式固定 `4.5×3.4in`，把 16:9 的照片压成 1.32:1（officecli 有 `picture_aspect_distorted` 这条问题类型）。新增 `_fit_box()`：按下载后的真实像素等比缩放 + 居中（实测 1600×900 → 4.50×2.53in）。
   - **batch 拒绝 `_link/_tooltip` 元数据**：合规改造时把署名框的超链接地址挂在命令对象上，officecli 报 `unknown field(s) "_link", "_tooltip"` 并**整批回滚**（第 4 页直接失败）。改为下发前剥离下划线字段（`apply_commands` 内 `{k: v for k, v in cmd.items() if not str(k).startswith("_")}`），并有单测守住。
   - **OfficeCLI 路径写死在 `.env`/config 默认值**：换机器、换 clone 目录就找不到编辑器（`D:\appadte\OfficeCLI\...`）。改为随项目分发 + 三级自动定位（裁定 15），`PPT_OFFICECLI_PATH` 默认空。
   - **`.env` 里曾被 heredoc 写坏一行**：`PPT_OFFICECLI_PATH=D:\u0007ppadte\...`（`\a` 被当成响铃转义），虽只是注释但会误导人；已清掉并改成说明。
   - **`inspect_issues()` 一直静默失效**：officecli 的 `--type` **不接受逗号分隔**（`content,format` 会报 `invalid_issue_type` 并返回空），而默认值正是 `"content,format"` → 这项检查从上线起就恒为「0 条问题」。改为逐桶查询后合并，立刻暴露下面这条真问题。
   - **章节页大序号溢出**：96pt 实测需要 115pt 行高，而框只有 108pt（3 个章节页各报一条 `text overflow`）。把序号框从 1.6in 加到 2.1in、标题下移到 4.15in；修完 `inspect_issues` 返回 **0 条**（第 9 行实测 `S7: 问题 0 条`）。

---

## 四、验证项状态（对照规划文档 §02 §3.15）

| # | 验证项 | 状态 | 证据 |
|---|---|---|---|
| 1 | 百度链路 0 残留；不再有 20–60s 黑盒等待 | ✅ | `grep -ri baidu_ppt` = 0（src/scripts/tests/front）；接口只剩 agent（SSE）与快速模式 |
| 2 | 10 页端到端：阶段齐全、进度单调、有截图、有来源页 | ✅ | 第 7 行实测：10/10 页、进度单调、10 张截图、「图片来源与授权」页含 3 条署名、550 KB |
| 3 | 断线恢复 / 卡死治理 | ✅ | `GET /progress` 返回 stage/progress/msg；`_reap_if_stale` 在读取时把"生成中但无锁"的任务置失败；UI 刷新后任务行正常 |
| 4 | 自检有效性（超长文字被抓出并修好） | ✅ | 离线探针第 4 页 6 条要点被判 `overflow` → 缩字号重建 → 通过；日志有「修补中（1/2）」 |
| 5 | 图片失败整单仍成功、日志有降级标记 | ✅ | 4 次真机全部 Openverse 超时 → `S5 warn: 没有拿到可用配图`，产物正常 |
| 6 | 沙箱越权（`../`、跨账号） | ✅ | 单测 `test_safe_join_neutralises_traversal`、`test_assets_reject_traversal`、`test_download_rejects_escape`、`test_download_other_account_forbidden` |
| 7 | OfficeCLI 缺失 → 自动兜底渲染 | ✅ | 实测第 6 行：`done.fallback=true`，7 页 36 KB |
| 8 | 用户清理工作区 | ✅ | `DELETE /workspace?keep_out=true` 只删 images/shots/drafts/logs；单测 `test_cleanup_keeps_out_by_default` |
| 9 | 模板上传后产物沿用其配色 | ⚠ 部分 | 模板列表可见（`00111bcf_安全生产培训模板.pptx`）、副本进沙箱、主题色读取有单测；"母版版式复用"未做（§三-2） |
| 10 | 单测/回归全绿 | ✅ | `tests/test_ppt_agent.py` 30 passed；与 `test_isolation_download`(15) + `test_agent_cache`(17) + `test_preprocess_audit`(4) 合并 66 passed |

补充：`pytest tests/` 全量仍会在 `test_chat.py::test_chat_sse_flow` 后挂起（01 既知问题，与 02 无关）。

---

## 五、遗留 / 未做（明确记下来，别当成"已完成"）

1. **Unsplash 配额是 Demo 级（50 次/小时）**：一单 10 页约 6 次请求（3 次检索 + 3 次下载事件），约 8 单/小时；`x-ratelimit-remaining ≤ 3` 时 warning，遇 401/403/429 直接降级无图。要提额需在 Unsplash 后台申请 Production（5000/h）。Openverse 通道仍在（`PPT_IMAGE_PROVIDER=openverse`），但本机不可达，只作备选。
2. **模板母版版式复用未做**（只继承配色/字体）。
3. **图标兜底未做**（无 SVG→PNG）。
4. **逐页是串行审图**：10 页 ≈ 220s，主要花在 AGNES 审图（~8s/页）。可并行（页间无依赖），但 OfficeCLI 同一进程写同一文件，需要"先全量排版 → 再并行截图审图 → 再批量修"的两段式改造。
5. **`page`/`page_total` 在 `done` 事件里不带**，进度接口读到的是 0（前端只在生成中显示页码，不影响使用）。
6. **`allow_overflow` 目前只是入参**：页数超了就按拆页/截断处理，没有实现"允许超页"的实际行为（当前实现恒为"压缩到设定页数"）。

---

## 六、回滚方式（三个开关，env 级生效，不改代码）

| 开关 | 效果 |
|---|---|
| `PPT_AGENT_ENABLED=false` | `/agent` 返回 503；前端只能走「快速模式」`POST /generate`（python-pptx 渲染） |
| `PPT_OFFICECLI_PATH=`（置空） | Agent 自动改用兜底渲染器（实测第 6 行），产物仍可下载 |
| `PPT_IMAGES_ENABLED=false` | 不检索图片，直接无图版式 |

---

## 七、一行结论

OfficeCLI 已随仓库分发（`tools/officecli`，零配置）；百度黑盒链路已彻底移除，默认链路换成"代码管阶段、Agent 管每页"的本地生成器：10 页端到端 146~221s、页数严格命中、validate 通过、进度与截图在浏览器里可见可下载；配图链路已用 Unsplash 真机跑通（3 张图 + 来源页 + 等比不压扁），模板仍只做到"继承配色"——这条是下一步要补的。
