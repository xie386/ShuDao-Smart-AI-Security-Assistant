# v2.0 · 02 PPT 工坊升级 · 逐文件带读

> 配套：[`02-PPT工坊升级·实现记录.md`](02-PPT工坊升级·实现记录.md)（实测与偏差）、
> [`../v2.0规划文档.md`](../v2.0规划文档.md) §02（方案）、[`../Verdict/02-PPT工坊升级.md`](../Verdict/02-PPT工坊升级.md)（裁定）。
> 按"**先路径 → 再工具 → 再版式 → 再编排 → 再服务/路由 → 最后前端**"的顺序读；每个文件给"看什么 / 为什么这么写 / 思考题"。

---

## 0. 一张图看清谁调谁

```
routers/ppt.py  POST /{id}/agent (SSE)
      │  start_agent()（抢锁 + 规格归一 + 模板副本）
      ▼
services/ppt_service.py ──► ai/ppt_agent.py  run() 生成器（S0–S8）
                                   │  日志/事件 ──► services/ppt_progress.py（Redis）
                                   │
        ┌──────────────────────────┼───────────────────────────┐
        ▼                          ▼                           ▼
  ai/pptools.kb_search     ai/ppt_tools.search_images     ai/vision_agent.review_page
   （复用 01 的 rag）        （Openverse，失败降级）          （AGNES 看图判定）
                                   │
                                   ▼
                        ai/ppt_layout.build()  →  officecli batch
                        （页面规格 → 命令数组）      （唯一写者）
                                   │
                                   ▼
                        core/paths.py（沙箱路径唯一事实源）
```

---

## 1. `src/core/paths.py`（153 行，先读这个）

**看什么**：`SANDBOX_ROOT` / `user_root` / `task_root` / `task_dir` / `out_path` / `log_path` / `cleanup_task`；
三个安全函数 `safe_name` / `safe_join` / `is_within`。

**为什么这么写**：
- 文件名只由整数 id 拼（`u17` / `t11`），**模型/用户给的字符串只做 basename** —— `safe_name("../../etc/passwd")` = `passwd`。
  结果是"越界被压平"而不是"报错"，所以 `safe_join` 只在名字被压成空串时才抛（`..`、``、空格）。
- 模块只给路径、**不建目录**：落盘处显式 `ensure_dir()`，避免"读的时候顺手建了目录"这种隐形副作用。
- 历史产物在 `ppt_files/`、新产物在沙箱，两套命名都带 `u{uid}_t{tid}_`（00 大点定的口径），下载端点靠 `is_within()` 判断该走哪条。

**思考题**：为什么账号目录不用 username？（可改名 → 目录失联；`user_id` 是整数，天然免疫分隔符注入）
为什么 `cleanup_task` 默认保留 `out/`？（成品是用户资产，中间稿才是可清的垃圾）

---

## 2. `src/ai/ppt_tools.py`（303 行）

**看什么**：
- OfficeCLI 薄封装：`create_deck / open_doc / save_doc / close_doc / release_file / apply_commands / render_page / inspect_issues / validate`；
- `search_images()`：多图源检索（`current_provider()` → Unsplash / Openverse，各带 1 次重试）+ 流式下载（体积上限、文件名唯一、量真实像素）+ `write_sources()`（授权/署名落盘）；Unsplash 还需按 API 要求打一次 download 事件；
- `kb_search()`：复用 01 的 `retrieve_queries + format_context`；
- `fs_list / fs_read`：沙箱内只读。

**为什么这么写**：
- `batch` 命令走**临时 JSON 文件**（`--input`），不走 stdin —— Windows 下 stdin 编码最容易出坑。
- `release_file()` 是实测逼出来的：OfficeCLI 的 `close` 之后还有几百毫秒锁窗，立刻删/重建会 `Device or resource busy`。
- 图片**任何失败都返回已拿到的部分 + 空列表兜底**，绝不抛异常：一张图失败不能拖垮整单（裁定 6）。
- `write_sources()` 单独一个函数：末页署名与 `sources.json` 用同一份数据，避免两处口径漂移。

**思考题**：为什么 `_http()` 用 `trust_env=True`？（生产/本机都可能要走代理；写死代理反而更脆）
为什么图片文件名要带 uuid？（三个关键词各调一次，按"序号_候选序号"命名会互相覆盖 —— 实测来源页 3 条指向同一张图）
为什么图源要做成 `PPT_IMAGE_PROVIDER` 而不是直接写死 Unsplash？（换源/降级不改代码；无 key 自动回退 Openverse）
`PPT_IMAGE_API_BASE` 做成配置的意义？（换镜像/换图库不改代码，符合"配置导向"）

---

## 3. `src/ai/ppt_layout.py`（239 行）

**看什么**：`DEFAULT_THEME` + `build(slide_no, spec, idx, image, theme)`；六类页面 `cover/toc/section/content/refs/end`；`_fix_of()`。

**为什么这么写**：
- **几何由代码定，内容由模型定**（裁定 3）：模型不吐坐标，只给标题/要点/图片意图/版式类型，版式稳定可复现。
- `build()` 末尾统一补 `parent="/slide[N]"` —— officecli 的 `add` 必须有 parent，漏了会整批失败（实测第一版就栽在这）。
- 要点：>5 条走双栏（不再 `[:5]` 静默丢），>10 条才截断且调用方记 warning —— 这是裁定 11 要修的老毛病。
- `_fix`：`font_delta / max_bullets / drop_image`，只有审图不通过时才生效，重建本页用。

**思考题**：为什么"两栏阈值 5、上限 10"不是拍脑袋？（5 条单栏在 20pt 下刚好占满 5.2in；10 条是"再多人眼就不看了"的经验线）
为什么 `refs` 页也走同一套 `build`？（保证"来源页"也是普通页，随时能重排/删）

---

## 4. `src/ai/vision_agent.py`（137 行）

**看什么**：`_cfg()`（PPT 段 → AGNES 段回退）、`review_page(png, page_spec)`、`_parse_json()`、`available()`。

**为什么这么写**：
- AGNES 会先吐 `reasoning_content` 再吐 `content`，`max_tokens` 给小了 `content` 直接为空（实测 40 → 空）→ 默认 800 且**只读 content**。
- 审图失败/超时/未配置一律返回 `ok=True + skipped`：**自检是加分项，不能变成故障点**。
- 判定只问四类版式问题（溢出/遮挡/对比度/缺失），不问"内容写得好不好"——模型评内容会跑偏，内容质量由策划阶段负责。

**思考题**：为什么视觉模型要独立配置段而不是复用主模型？（主模型纯文本；以后接别的视觉模型只改 `.env`）
判定结果为什么必须结构化（`ok/problems/fix_hints`）？（修补逻辑要按 `type` 选修法，自由文本没法驱动代码）

---

## 5. `src/ai/ppt_agent.py`（651 行，本大点核心）

**看什么**（建议按顺序）：
1. `STAGES` + `_pct()`：阶段与权重，进度条的唯一来源；
2. `PLAN_PROMPT` + `plan_outline()` + `_alloc/_rebalance/_ensure_sections`：页数预算与规划兜底；
3. `build_pages()`：章节/内容/来源/结尾的页面序列；
4. `run()`：S0–S8 主流程（生成器，逐条 yield 事件）；
5. `_next_fix()`：审图不通过时的修法递进（先缩字号，再砍要点/去图）。

**为什么这么写**：
- **阶段固定、页内自由**（裁定 3）：进度条能对齐 S0–S8，模型的不确定性被关在"一页之内"。
- 页数预算改过两轮（见实现记录 §三-4）：`_alloc()` 保证"每章至少 1 页"；目标 ≤7 页不排章节过渡页；无图删来源页后，把 ≥6 条要点的内容页对半拆，**不编造内容**；实在不够就诚实少页。
- 规划失败有确定性兜底（`_fallback_plan`：把资料按句切块），保证"模型挂了也能出片"。
- 事件同时写 SSE 与 `logs/{task_id}.jsonl`：事后取证不依赖前端。
- OfficeCLI 缺失时走 `_pages_to_outline()` → `ppt_local.generate_local_ppt()`，并给 `done.fallback=true`（验证项 7）。
- 关键实现细节：每页先 `build` → `batch` → `screenshot` → 审图；不通过就 **`remove /slide[N]` 再重建**（而不是逐条 `set` 改属性）——重建只有一条路径，不会留下半改状态。

**思考题**：为什么"砍内容"要放在第二轮修法？（砍内容是不可逆信息损失，缩字号通常就够）
为什么审图只对 `content` 页做？（封面/目录/章节页版式固定，判定收益低、耗时白花）

---

## 6. `src/ai/ppt_llm.py`（71 行）

**看什么**：`PptLlmProvider` 协议、`_REGISTRY`、`register/build_provider/capabilities`。

**为什么这么写**：百度已删（裁定 1），但"两种工作方式"的产品形态要保留（裁定 2）。留一个空注册表的成本几乎为零，以后接新 provider 只需实现 `generate()` + 填 `.env`，前端靠 `capabilities.ppt_llm` 自动出现入口。

**思考题**：为什么 `capabilities()` 要 `lazy import` `ppt_tools/vision_agent`？（避免启动期加载重依赖）

---

## 7. `src/services/ppt_progress.py`（107 行）

**看什么**：`set_progress/get_progress/clear_progress` + `acquire_lock/release_lock/lock_held`。

**为什么这么写**：进度是"几分钟就过期"的瞬时状态，改 `ppt_tasks` 表要动 `schema.sql`（P3 管理，裁定 9 明确不改）。Redis 不可用时 `acquire_lock` **放行**（宁可并发风险，也不能让功能整体不可用）。

**思考题**：为什么锁的 TTL 是 `PPT_AGENT_TOTAL_TIMEOUT_S + 60`？（比整单超时略长，进程崩了让锁自己过期，不留死锁）

---

## 8. `src/services/ppt_service.py`（390 行）

**看什么**：`start_agent`（`_reap_if_stale` → 抢锁 → 状态"生成中" → `_build_spec`）、`finish_agent/fail_agent`、`workspace/cleanup_workspace`、`download_path`（沙箱优先 → 历史目录）、`_build_spec`（模板副本）。

**为什么这么写**：
- 生成是几分钟的长任务，**状态必须先生效**（前端立刻看到"生成中"），业务逻辑本身在 `ppt_agent`，service 只管状态机与规格。
- `_reap_if_stale`：进程被杀会留下"生成中但没有锁"的任务，读取时兜底置失败（裁定 12）。
- 模板**复制副本进沙箱**再改：多人用同一模板时不会互相踩。

**思考题**：为什么 `download_path` 先看沙箱再看 `ppt_files/`？（新产物在沙箱，历史产物不迁移但要能下载；两条路径都做根目录校验）
为什么 `delete_task` 要 `release_file()` 才删？（OfficeCLI 常驻进程锁文件）

---

## 9. `src/routers/ppt.py`（290 行）

**看什么**：端点清单在文件头 docstring；重点是 `generate_agent()` 的 `stream()`（**独立 `SessionLocal`**）、`_public_event()`（绝对路径 → 鉴权 URL）、`assets()`（`kind` 白名单 + `safe_join`）。

**为什么这么写**：
- SSE 生成器活几分钟，请求级 `SessionLocal` 在流式响应期间可能已被关闭 → 生成器里自己开会话、`finally` 关。
- 截图路径不能直接暴露磁盘路径，转成 `/api/ppt/{id}/assets/shots/page-XX.png` 再走鉴权。
- 静态路径（`/capabilities`、`/templates`）必须声明在 `/{task_id}` **之前**，否则会被当成 task_id 匹配。

**思考题**：为什么 `assets()` 要做 `kind` 白名单？（否则 `kind=../../.env` 这种路径会绕过 `safe_join` 的前提）

---

## 10. `src/ai/ppt_local.py`（233 行，兜底渲染器）

**看什么**：`generate_local_ppt(outline, topic, out_path, template_path)`；`_render_content` 的双栏与字号选择。

**为什么这么写**：它现在有两条用途 —— 快速模式（`POST /generate`）与 OfficeCLI 缺失兜底。裁定 11 要求修掉三处静默劣化：不再 `[:5]` 截断、目录分栏、按条数降字号。与 Agent 的分工（裁定 8）：**同一任务只走一条**，python-pptx 不参与 Agent 的逐页编辑。

**思考题**：为什么不干脆删掉它？（没有 OfficeCLI 的机器上，它是唯一能出片的路）

---

## 11. `src/schemas/ppt.py` / `src/core/config.py` / `.env` / `.gitignore`

- `schemas`：`BUILTIN_TEMPLATES=("builtin_simple",)`；`PptAgentSpec` 是前端表单 6 项的服务端契约（页数 3–40、字数 30–400）。
- `config`：`PPT_*` 22 项，最重要的三个是 `PPT_AGENT_ENABLED` / `PPT_OFFICECLI_PATH` / `PPT_VISION_MODEL`（回滚与换模型都靠它们）。
- `.env`：删掉 4 行 `BAIDU_PPT_*`；视觉 key 不重复配，`PPT_VISION_API_KEY` 为空时回退 `AGNES_API_KEY`。
- `.gitignore`：`backend/user_data/`（模板副本/图片/截图/中间稿/日志/成品全在这里，属用户产物）。

---

## 12. `front/safety/index.html`（PPT 页，约 400 行增量）

**看什么**：`pptSpec()`（表单 → 请求体）、`genPptAgent()`（fetch 读 SSE）、`pptSetProgress/pptAddShot`（进度条 + 缩略图）、`pptRestoreProgress()`（刷新恢复）、`cleanupPpt()`。

**为什么这么写**：
- 进度与日志**同源**（都来自 SSE 事件），不做第二套渲染。
- 缩略图必须 `api(url)` 取 blob 再挂 `<img>`：`<img src>` 带不上 `Authorization`，实测 6 张图全 401。
- 刷新后按 `status=生成中` 拉 `/progress` 恢复进度条（断线恢复的 UI 侧）。

**思考题**：为什么"生成 PPT"按钮要先建任务再流式生成，而不是一个接口全包？（任务 id 是 SSE 的进度键、下载键、清理键，先有 id 才能断线恢复）

---

## 13. `tests/test_ppt_agent.py`（463 行，30 用例）

**看什么**：五组 —— 沙箱路径（4）、版式（5）、编排（5）、配图（4）、进度锁（3）、下载与服务（3）、接口（4）、以及图片/来源页（2）。

**为什么这么写**：全部不联网、不调 LLM、不写真实 `user_data/`（`tmp_path` + `monkeypatch`）；配图链路用 `httpx.MockTransport` 模拟，因此**即使 Openverse 不可达也能验证真实下载/落盘/署名代码**。

**思考题**：为什么要专门测 `test_layout_injects_parent_for_all_elements`？（officecli `add` 缺 parent 会整批失败，且错误信息只说"requires parent"，不指出是哪条）
