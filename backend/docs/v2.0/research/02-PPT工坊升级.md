# v2.0 · 02 PPT 工坊升级 · 调研记录

> 本文件**只记录讨论与取证结果**，不写方案、不写取舍结论。
> 每个话题一节：`用户问题（原话）→ 取证方法与证据 → 该问题的事实回答 → 顺带查到的事实 → 仍需验证的点`。
> 话题按讨论顺序追加。

---

## 话题 1：百度 LLM PPT 返回的是「一整个 PPT」还是「待项目自行组装的 PPT 内容」？

### 1.1 用户原话

> 对于专职百度 ppt llm 以及当前的 ppt 工作流，我们需要生产流程一个大改，请先帮我取证一个问题：百度的 llm ppt 返回的是一整个 ppt；还是只是写好的 ppt 内容，嵌入 pptx 的文件中还需要项目自行找和处理？

### 1.2 取证方法

1. 读官方接口文档（`https://cloud.baidu.com/doc/qianfan/s/4mjcnolh6`，页面标注更新时间 2026-05-08）；
2. 读本项目调用代码（`backend/src/ai/ppt_baidu.py`、`backend/src/services/ppt_service.py`）；
3. **解剖本机真实产物**：`backend/ppt_files/` 下 4 个 .pptx，用 `zipfile` 统计 `ppt/slides/slideN.xml`、`ppt/media/*` 数量与体积，并抽取每页 `<a:t>` 文本；
4. 用数据库 `ppt_tasks` 对齐"任务大纲章节数"与"产物页数"。

### 1.3 证据

**证据 A：官方文档（原文摘录）**

| 位置 | 原文 |
|---|---|
| 概述 | 「智能 PPT 支持基于大纲内容以及上传的资源文件**快速生成完整演示文稿**，并提供自定义模板等能力」 |
| 请求参数 `outline` | 「大纲内容，由大纲生成接口返回的内容拼接而成」 |
| 响应 `status` | `PPT生成中 / PPT生成结束 / PPT生成失败 / PPT导出中 / PPT导出结束 / PPT导出失败` |
| 响应 `bd_ppt_json` | 「**每页**的百度PPT JSON数据」（内含 `background.image` 背景图 URL、`c:"..."` 文本框、`font-size_66px` 等排版属性） |
| `status = PPT导出结束` 时 | `data.pptx_url`：「pptx 下载链接」（示例值指向 `wkbjcloudbos.bdimg.com/...rtcs/ppteditor//.pptx?...`） |

**证据 B：本机真实产物解剖**（2026-10-23 实测）

| 文件 | 大小 | 页数 | 内嵌图片 | 图片总体积 | 来源通道 |
|---|---|---|---|---|---|
| `隧道施工安全.pptx`（task6） | 16.9 MB | **27** | 32 个（28 jpeg + 4 png） | 11.6 MB | 百度 |
| `高处作业安全.pptx`（task7） | 15.9 MB | **35** | 40 个（36 jpeg + 4 png） | 10.2 MB | 百度 |
| `安全生产法要点.pptx`（task8） | 17.0 MB | **34** | 32 个 | 11.6 MB | 百度（详见 1.5） |
| `u17_t10_安全生产法要点.pptx`（task10） | **40 KB** | **8** | **0** | 0 | 本地 python-pptx |

- 百度产物含 `ppt/theme/theme1.xml` + 11 个 `slideLayout`，封面页文字为 `隧道施工安全 / 汇报人：XXX / 2026-08-15`（Baidu 模板自带占位符），正文页是带配图的图文混排；
- 对照：task6/7/8 的大纲分别只有 5/6/4 个 `sections`、19/26/16 个 `points`，产物却是 27/35/34 页 —— 页数由百度按模板自行扩写与配图，不是按我们的 sections 一页一节；
- 本地通道产物 = `封面 + 目录 + N 个内容页 + 结尾`，纯文字、0 图片、40KB（`ppt_local.generate_local_ppt` 的输出特征）。

**证据 C：代码路径里没有"组装 pptx"这一步**

`ppt_service.generate()`（`ppt_service.py:171-179`）在百度通道只做两件事：

```python
pptx_url, _page_count = generate_ppt(outline, task.topic, task.template)   # 拿导出链接
download_pptx(pptx_url, dest)                                            # 原样落盘
```

`download_pptx()`（`ppt_baidu.py:157-186`）唯一的处理是 **zip 完整性校验**（必须是 zip 且含 `ppt/slides/slideN.xml`），不解析、不改写、不插入任何内容。项目自己的排版代码只存在于本地兜底通道 `ppt_local.py`。

### 1.4 对该问题的事实回答

**百度返回的是一整个成品 PPT（.pptx 文件）**，不是"写好的内容交给项目排版"：

- 排版、配图、母版、主题、页数与分页全部由百度完成；
- 项目只做「拿 `data.pptx_url` → 下载 → 落盘 → 提供带鉴权的下载接口」，不参与任何 pptx 组装；
- 只有在**降级到本地方案**时，才是项目自己用 python-pptx 拼（8 页、0 图、40KB）。

换句话说：百度通道的产物是"黑盒成品"，我们能控制的是**输入**（大纲 Markdown、`query/title`、`style_id/tpl_id` 或 `custom_tpl_url`、`gen_mode`），控制不了成品的中间结构。

### 1.5 顺带查到的事实（与"生产流程大改"直接相关，本话题不定方案）

1. **代码把 `query_id` / `chat_id` 硬编码成 1**（`ppt_baidu.py:103-104`），而官方文档明确这两个字段「存在上下文依赖关系，若无法匹配对应的大纲生成请求的 query_id / chat_id，可能影响最终 PPT 的生成效果」。
2. **实测到一次疑似"跨请求串台"的产物**：task8 的 `topic = 安全生产法要点`、大纲 4 章节/16 要点、模板是用户上传的自定义模板，但 `ppt_files/安全生产法要点.pptx` 的封面与正文**完全是「隧道施工安全」**（slide1–8 与 `隧道施工安全.pptx` 逐页文字一致，目录列出的是隧道那 5 个章节），文件时间 2026-08-17 14:25，比 `隧道施工安全.pptx`（08-15 16:08）晚两天，页数 34 vs 27。
   - 与"大纲章节数"对齐后：该产物的 5 个章节对应的是 task6（隧道施工）的大纲，而不是 task8 自己的 4 章节大纲；
   - 与文档的"上下文依赖"、代码的"硬编码 1/1"合起来构成一个可解释链条，但**尚未做对照实验**（见 1.6）。
3. **百度接口支持 `custom_tpl_url`（自定义模板 URL）**，且与 `style_id/tpl_id` 互斥（「3 个字段都传以 custom_tpl_url 为准」）；本项目从未使用该参数，而是"识别到自定义模板 → 强制走本地生成"（`ppt_service.py:154-162`）。文档还要求「上传自定义模板生成 PPT 时，style_id / tpl_id 非必填」。
4. **百度接口支持 `gen_mode = 2`「严格依从」**（1 = 智能润色，2 = 严格依从）；本项目固定传 1（`ppt_baidu.py:110`），即成品内容会被百度改写润色，而非严格按我们的大纲文字。
5. **`page_count` 我们拿到了但丢弃**（`ppt_service.py:173` 的 `_page_count`），页数没有落库。
6. **历史产物缺失**：task3（`临时用电安全.pptx`）状态已完成、`file_path` 有值，但磁盘上该文件不存在（`ppt_files/` 只有 4 个文件），该任务现在点下载会得到「产物文件已丢失，请重新生成」。
7. **内置三套模板在百度侧映射到同一个 `tpl_id=102322`**（`ppt_baidu.py:29-33`，simple/tech 标着 TODO），本地通道也只有一套配色 —— P04「商务/简约/科技」目前三条路生成的是同一种版式。

### 1.6 仍需验证的点（对照实验，尚未执行）

- **串台复现**：连续发起两次不同主题的生成（topic A → topic B），固定 `query_id/chat_id=1`，检查 B 的产物封面/章节是否变成 A；再把 `query_id/chat_id` 改成与大纲请求真正配对的值（或每次唯一）重跑同一组，对比差异。
- **`gen_mode=1` vs `2` 的成品差异**：同一大纲分别生成，比对页数、要点文字是否被改写、是否新增/删减章节。
- **`custom_tpl_url` 通道可行性**：把用户上传的 .pptx 放到可公网/内网访问的 URL，传 `custom_tpl_url` 生成，检查成品是否沿用自定义模板母版（若可行，现有"自定义模板强制本地"的分支前提就变了）。
- **额度与失败形态**：确认每日 10 次额度用尽时返回的 `errno` / `status` 具体值（当前代码对额度用尽只能表现为"降级本地"）。

---

## 话题 2：既然返回整个 PPT，为什么每次都是同一套模板？是否要先给百度一个（空白）pptx 模板？

### 2.1 用户原话

> 既然是返回整个ppt,但是为什么返回的ppt都是一个ppt模版的ppt,是不是需要先提供一个原始内容空白的pptx模版给ppt llm

### 2.2 取证方法与证据

**证据 A：三份百度产物的主题完全一致（同一套模板）**

解剖 `ppt_files/` 下三份百度产的 pptx 的 `ppt/theme/theme1.xml`：

| 文件 | theme name | dk1/lt1 | accent1 | accent2 |
|---|---|---|---|---|
| 隧道施工安全（task6，模板标签 business） | Office 主题 | 000000 / FFFFFF | **78A8C9** | 5E90B2 |
| 高处作业安全（task7，模板标签 **tech**） | Office 主题 | 000000 / FFFFFF | **78A8C9** | 5E90B2 |
| 安全生产法要点（task8，模板标签 **自定义**） | Office 主题 | 000000 / FFFFFF | **78A8C9** | 5E90B2 |

三份主题色、母版、页脚（`汇报人：XXX / 日期`）完全相同 —— 与"三套模板"的界面语义不符。

**证据 B：代码把三个模板标签都映射到同一个 `tpl_id`**

`backend/src/ai/ppt_baidu.py:29-33`：

```python
TEMPLATE_MAP = {
    "business": {"style_id": 0, "tpl_id": 102322, "label": "商务"},
    "simple":   {"style_id": 0, "tpl_id": 102322, "label": "简约"},  # TODO: 待模板接口确认
    "tech":     {"style_id": 0, "tpl_id": 102322, "label": "科技"},  # TODO: 待模板接口确认
}
```

`style_id=0 / tpl_id=102322` 是百度**官方示例**里的取值（`scripts/test_ppt_api.py:29` 注释也这么写）。也就是说 `business/simple/tech` 是**我们自己的标签**，与百度的模板库没有任何对应关系。

**证据 C：百度侧确实有"模板"这一层，且我们从未调用**

| 接口 | 作用 | 本项目是否调用 |
|---|---|---|
| `POST /v2/tools/ai_ppt/get_ppt_theme` | 「获取 PPT 模版」：返回 `ppt_themes[]`，每项含 `style_id` / `tpl_id` / `style_list` / `color_list` / `scene_list` / `main_img_url`（预览图）；支持按风格、场景、颜色筛选；可选项枚举在 `ppt_theme_option` | **从未调用**（全仓无该 URL） |
| `POST /v2/tools/ai_ppt/generate_outline` | 生成大纲，返回与生成接口配对用的 `chat_id`/`query_id`，可用 `page_range` 控制页数（1-10/11-20/…) | **从未调用**（我们用自家 LLM 出大纲） |
| `POST /v2/tools/ai_ppt/generate_ppt_by_outline` | 按大纲生成完整 PPT；模板来源 = `style_id`+`tpl_id` **或** `custom_tpl_url`（三者互斥，都传以 `custom_tpl_url` 为准） | 调用，但只传固定的 `style_id=0 / tpl_id=102322` |

**证据 D：官方参数口径**

- `style_id` / `tpl_id`：「**通过获取模版接口获取**；上传自定义模板生成 PPT 时，参数非必填；无自定义模板 URL 则必填。**两个参数无默认值**」；
- `custom_tpl_url`：「自定义模板 URL」；`custom_tpl_url` 与 `style_id`/`tpl_id` 互斥；
- 响应里带 `error`：「**模板解析失败信息**」→ 说明百度会**解析**我们给的模板，解析失败会报错，而不是静默降级；
- 文档「请求示例」区附了一个示例模板下载地址（`…/ai-ppt/ppt-template/上传模板制作示例：农场品市场分析PPT模板(1).pptx`），即官方对"什么样的 pptx 能当模板"给了样例。

### 2.3 对该问题的事实回答

1. **不是"百度只会出一种模板"，而是我们每次都点名同一个模板**：三个界面选项 → 同一个 `tpl_id=102322`，所以版式必然相同（证据 A + B）。
2. **"必须先给一个空白 pptx"不是唯一前提，但"必须给一个模板来源"是**。百度接受三种输入：
   - 用它的模板库：先调 `get_ppt_theme` 拿真实 `style_id/tpl_id`（可筛选风格/颜色/场景，有预览图），再传给生成接口；
   - 用我们自己的模板：把 .pptx 放到**百度服务端可访问的 URL**，传 `custom_tpl_url`（此时 `style_id/tpl_id` 可不传）；
   - 两个都不给：不合法（两个参数无默认值）。
3. **`custom_tpl_url` 的语义是"模板"，不是"待填内容的 pptx"**：从响应存在 `error=模板解析失败信息` 看，百度是**解析模板的母版/版式/配色**后重建整套演示文稿，而不是往我们那个文件的页面里插入内容。这与本项目本地兜底路径（`ppt_local.py:148-165`：`Presentation(template_path)` 后**在末尾追加**封面/目录/内容/结尾页，保留模板原有页）是**两种不同语义**。

### 2.4 顺带查到的事实

1. `get_ppt_theme` 在官方文档里标注为**付费组件**（「该组件为付费功能」），调用它本身也有成本/延迟，不只是白拿列表。
2. 百度的 `generate_outline` 支持 `page_range`（1-10 / 11-20 / 21-30 / 31-40 / 40+）控制页数，并且要求生成 PPT 时传回同一组 `chat_id`/`query_id`；本项目大纲由自家 LLM 生成，这两个 id 因此无从配对（也是 1.5 节串台疑点的结构性原因）。
3. 实测本机产物页数 27/34/35，而大纲只有 4-6 章节 —— 页数与分页完全由百度按模板决定，除 `page_range` 外我们无法控制。

### 2.5 仍需验证的点（尚未执行）

- **`custom_tpl_url` 是否保留模板母版/占位符**：用官方示例模板（或我们自己的 .pptx，放到可访问 URL）生成一次，检查成品：母版是否沿用、模板原有页是否被保留或丢弃、内容页是否套用模板版式。
- **`get_ppt_theme` 的真实返回**：调一次拿全量模板列表，确认 `tpl_id` 数量级、是否含"商务/简约/科技"这类风格、预览图可访问性，以及是否存在使用上限/计费口径。
- **模板 URL 的可访问性边界**：百度服务端能否访问内网地址（大概率不能），需确认是否有"媒资上传"类接口可以先把文件托管到百度侧。
- **同一模板不同 `gen_mode`**：`gen_mode=2「严格依从」` 是否只影响文字、不影响版式（与模板问题正交，可一并验证）。

---

## 话题 3：升级动因与两种工作方式的定位（可选直连 PPT LLM / 常驻 PPT 生成 Agent）

### 3.1 用户原话

> 一个事实是:百度ppt llm的配置时全项目env性质的,即不是一个用户一天10次,是所有用户一天10次,这会造成很大的使用体验下降,所以此次升级的目的是尽可能的脱离ppt llm的依赖,把ppt llm变成可选链路项,原来的降级生成简陋pptx的链路要升级成默认使用链路,这就需要把降级链路升级,并且尝试提升输出ppt质量的方法,为此,ppt 工坊变成两种工作的方式:1.可选直连ppt llm API 生成ppt链路;2.常驻ppt生成Agent(与原先的AI助手一样,当前的ppt工坊只是形式上的agent,依然本质上为AI 从接口进入工作流)

### 3.2 事实复核（逐条）

**① 「配额是全项目 env 级别，不是每用户每天 10 次」—— 代码层面成立**

| 事实 | 证据 |
|---|---|
| 只有一个密钥，来自 `.env` | `src/ai/ppt_baidu.py:95,114`（`settings.BAIDU_PPT_API_KEY`），`src/core/config.py:44` 注释「每日 10 次免费」 |
| 请求体里没有任何"是谁在用"的字段 | `ppt_baidu.py:102-112`（只有 query_id/chat_id/outline/query/title/style_id/tpl_id/gen_mode/ai_info） |
| 没有按用户/按天计数，没有密钥池与轮换 | 全仓 `grep BAIDU_PPT` 只有上述 4 处；无计数器、无 Redis 计数、无多 key |

结论：一个部署 = 一个 key = 一份额度，**所有账号共享**；任何用户点到第 N 次，别人就没得用了。

补充一处口径差异（如实记录）：项目内注释与 docstring 写「每日 10 次免费」（`config.py:44`、`ppt_service.py:13`、`ppt_baidu.py:35`），但**现行官方文档**对该接口的表述是「该组件为**付费功能**，具体计费说明请参见使用文档」（同一接口的付费标注也出现在 `ai_command_ppt` 上）。本次检索未找到官方公开的"每日 10 次"原文，额度口径应以百度云控制台账单/配额页为准。

**② 「当前的 ppt 工坊只是形式上的 agent」—— 代码层面成立**

| 判定要素 | 现状 |
|---|---|
| 是否有规划/多步决策 | 无。P01 是**一次** `llm_client.chat()` 产出大纲 JSON（`ppt_service.py:81-91`） |
| 是否有工具调用/循环 | 无。全流程没有 tool、没有"做完检查再补"的回路 |
| 是否有中间态持久化 | 只有 `status` 四态与 `outline` 文本；无步骤、无产物分解 |
| 是否有反思/自评 | 无。生成的成品不校验（页数、文字溢出、要点缺失都不检查） |
| P03 生成 | 固定二分支：百度（黑盒成品）或本地（固定渲染函数），由 `use_local` 与"是否自定义模板"决定 |

也就是说：现在的"agent"是"接口 → 一次 LLM → 固定渲染"，与 AI 助手（预处理 → 判定 → 检索 → Agent 生成 → 缓存/续轮）那种带决策的编排不是一个量级。

**③ 「降级链路要升级成默认链路」—— 现状基线有多简陋（实测）**

| 维度 | 现状（`ppt_local.py`，python-pptx 1.0.2） |
|---|---|
| 页数 | 固定 `封面 + 目录 + N 内容页 + 结尾`；本机实测 8 页（5 章节时） |
| 体积 | 40 KB / **0 张图片**（百度产物 15.8–17.4 MB / 27–40 张图） |
| 版式种类 | 只有 1 种内容页（标题条 + 左侧金色竖线 + ≤5 条要点文字） |
| 要点上限 | 硬截断 `points[:5]`（`ppt_local.py:108`），超过静默丢弃 |
| 目录页 | 6 章节时纵向溢出页外（`ppt_local.py:86-96`，1.8+5×1.05+0.85=7.9in > 7.5in） |
| 模板差异 | 内置三套在本地通道**完全一样**（只有一种深蓝+金配色） |
| 配图 | 无。项目内**没有任何文生图能力**（全仓无 `image_gen`/生图 API）；`knowledge_src` 里 **0 张图片**；`uploads/` 有 101 张（隐患上报照片，非素材图库） |
| 其他可用素材 | `knowledge_src` 有 **816 份 .md**（法规/规程/预案/案例）——文案与数据可取用，图片没有 |

**④ 成本口径：两条链路花的是两种钱**

| 链路 | 消耗 |
|---|---|
| 直连百度 PPT LLM | 项目级共享的百度额度（口径见 3.2 ①）；单次实测 20s 上下、产物 12–17MB |
| 常驻 PPT 生成 Agent | 主 LLM（DeepSeek/agnes）token，按轮计；每次生成会多轮调用（规划/写稿/校验），与百度额度无关 |

### 3.3 用户已明确的方向（原话记录，本条不定方案）

1. 升级目的是**尽可能脱离 PPT LLM 依赖**，把百度 PPT LLM 变成**可选链路项**；
2. 原"降级生成简陋 pptx"的链路要**升级为默认使用链路**；
3. 因此"降级链路"本身需要升级，并要**尝试提升输出 PPT 质量的方法**；
4. 工坊最终是**两种工作方式**：
   - 方式 1：可选「直连 PPT LLM API」生成链路；
   - 方式 2：「常驻 PPT 生成 Agent」（对标 AI 助手的形态，而非现在这种"接口进工作流"）。

### 3.4 由此暴露、仍需讨论的边界问题（记录，不预设答案）

| # | 问题 | 为什么它会影响后续设计 |
|---|---|---|
| Q1 | 百度额度用尽/未配置时，方式 1 的入口怎么表现（隐藏、置灰、还是照点然后降级） | 决定"可选链路"是显式选择还是自动兜底 |
| Q2 | 方式 2 的产物边界：Agent 只产出"大纲/文稿/讲稿"，还是连"排版与配图"一起产出 | 决定是否需要模板渲染层、是否需要配图能力 |
| Q3 | 配图从哪来（项目无文生图；`knowledge_src` 无图；`uploads` 是隐患照片） | 直接决定"提升质量"能做到什么程度：图标/形状/图表 vs 真实照片 |
| Q4 | 质量口径怎么定（页数、要点密度、图文比、是否可编辑、是否可追到知识库出处） | 没有口径就无法判断"升级后是否真的更好" |
| Q5 | 成品可编辑性要求（P08 单页编辑/替换图片是否在此次范围内） | 影响产物是"最终 pptx"还是"可再编辑的结构化中间态 + 渲染" |
| Q6 | 两种方式是否共享同一份"大纲/文稿"中间产物 | 决定 Agent 与百度链路能否复用同一套前置步骤 |

---

## 话题 4：用户提供的 PPT Agent 设计草案（`C:\Users\ZQK\Desktop\草案.md`）

### 4.1 草案要点（按原文顺序，保留原意）

**前置判断**
- 百度 PPT LLM「极有可能是每日 10 次免费的政策结束了」（两个月前页面上确有"每日 10 次"标注）；若属实，可能要考虑**全面弃用**百度 PPT LLM。

**Agent 设计**
| 段 | 原文要点 |
|---|---|
| (-2) | PPT 制作必然产生图片、模板、正式 PPT 等产物 → **按用户账号隔离，每用户一个产物沙箱**；Agent 的工作空间与产物严格放在该沙箱内 |
| (-1) | Agent 承担**网页操作浏览**，故必须是**多模态模型**接入 → 在 env 里**另写一个专门配置段** |
| (0) 工具 | ① 网络信息搜索与图片截取（复用 deep_search_pro 接入 ui_browser 的经验，可网搜资料、下图片、下 PPT 模板）；② 向量库检索（复用 AI 助手的**问题预处理**能力拆解主题，共用 RAG 检索链路代码）；③ 超文本文件操作（拟集成 OfficeCLI，`D:\appadte\OfficeCLI\officecli.exe`，复制进项目再考虑集成方式）；④ 基本操作（列工作区文件、展示文件内容等） |
| (1) 前端配置 | ① 简单描述或详细要求 md；② 页数规定；③ 字数规定；④ **是否接受"调研后发现内容比预期多"时超页超字数**；⑤ 模板自选（可选项）；⑥ 若不做⑤，则由 Agent **自行网搜下载 PPT 模板**，并选择样式喜好 |
| (2) | 主题预处理 → 调向量库检索 tool → 返回检索信息 |
| (3) | 按主题与要求，到**可信源网址下载 pptx 模板、符合主题的图片**（免费 PPT/图库站点需调研）到账号工作区 |
| (4) | 评估向量检索信息是否够 → 不够继续用 ui_browser 搜 |
| (5) | 收集好模板、图片、信息后**策划大纲** |
| (6) | **逐页制作**，每页完成后**自截图查看效果**，可引入 OfficeCLI 等超文本编辑工具 |
| (7) | 产出结果 |

### 4.2 事实核对：草案里每项能力的现状与可行性（本机实测）

**① 逐页制作 + 自截图自检 —— OfficeCLI 已具备，无需自建渲染管线**

`D:\appadte\OfficeCLI\officecli.exe`（33.4 MB，2026-09-17）实测能力：

| 需要的能力 | OfficeCLI 对应命令 | 实测结论 |
|---|---|---|
| 逐页编辑（加形状/文字/图片、改属性、删除、移动） | `add` / `set` / `remove` / `move` / `swap` / `batch` | 支持，且路径模型是 `/slide[1]/shape[@id=N]` 形式 |
| **每页截图** | `view <file> screenshot --page 1-N`（还可 `--grid` 出缩略图contact sheet、`--range` 裁剪到某元素） | 支持 .pptx；`--render auto` 在 Windows 上走 **native（PowerPoint）** |
| 自检（对比度/坏引用/未求值字段） | `view <file> issues --type format/content/structure`，pptx 专有子类型含 **`low_contrast`**、`slide_field_not_evaluated`、`broken_part_ref` | 现成 |
| 结构与合法性校验 | `validate <file>` | 现成 |
| 常驻内存加速（反复改同一文件） | `open` / `save` / `close`（resident，空闲 2-10s 自动 flush） | 现成 |
| 万能兜底（任意 OpenXML 操作） | `raw` / `raw-set` / `add-part` | 现成 |

另：本机 **Microsoft PowerPoint 已安装**（`C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE`）、**pywin32 可用**，即使不用 OfficeCLI 也有 COM 导出 PNG 的路；**没有 LibreOffice**。

**② 网搜 + 图片/模板下载 —— deep_search_pro 的经验与坑都是现成的**

`deep_search_pro/tools/browser_tool.py`（v4.0 大点 05）实测要点：

- 技术栈是 **browser-use**，跑在**独立 `browser_venv/`**、用子进程 + JSON 进出（`scripts/browser_runner.py`）；
- **不能装进主 venv**：项目的记录是装进去会解析出 106 个包、替换 pydantic 并卸掉 14 个既有包、项目起不来（这条对本项目同样适用，我们也依赖 pydantic）；
- 下载有坑：直接把 Chrome `downloads_path` 指到工作目录会 watchdog 报 "Download did not complete within 20 seconds"、文件始终不落盘；可行做法是**先落到 `_runtime/browser_dl/run_<时间戳>/` 暂存，再搬移**到工作目录；
- 它有代理处理（`_proxy()`）与"是否为下载任务"的判定（放宽步数）。

**③ 向量库检索 + 主题预处理 —— 本项目已有可直接调用的函数**

| 需要 | 现有可复用函数 | 位置 |
|---|---|---|
| 主题预处理（陈述句化/专业化/拆分建议/剔除已检索） | `preprocess(question, history, covered)` | `src/ai/query_preprocess.py:94` |
| 多子查询检索（子块级命中 + 重排分） | `retrieve_queries(queries, k, topics)` | `src/ai/rag.py:564` |
| 上下文装配（预算裁剪 + 来源标注） | `format_context(blocks, budget_chars)` | `src/ai/rag.py:609` |

即草案 (2) 的两步在本项目**不需要新写**，只是从"问答链路"搬到"工具"里调用。

**④ 多模态配置段 —— 现状是"半成品"**

- `src/core/config.py:40-42` 只有 `QWEN_API_KEY / QWEN_BASE_URL / QWEN_MODEL`（`.env` 里当前把 QWEN_* 指到 AGNES 的免费低质视觉端点，注释写明"开发测试阶段视觉模型暂时切换到免费低质AGNES"）；
- 主模型 `llm_client` 是**纯文本** `ChatOpenAI`，没有图像输入通道；
- 结论：草案 (-1) 的"另写一个专门的多模态配置段"确实是**新增**，现有 `src/ai/vision.py` 只能作为"如何传图"的参考（它用 OpenAI 兼容 content + image_url data URL）。

**⑤ 账号沙箱 —— 现状是平铺，不是沙箱**

| 产物 | 现状路径 |
|---|---|
| 成品 pptx | `backend/ppt_files/u{uid}_t{tid}_{主题}.pptx`（同一目录平铺，实测 4 个文件 50MB） |
| 用户上传模板 | `backend/knowledge_src/ppt_model/<uuid8>_<原名>.pptx`（**全账号共享**，与知识库同级） |
| 图片素材 | 没有"用户素材"目录；`uploads/` 是隐患上报照片（101 张） |

**⑥ 长流程与前端交互 —— 现状是"一次同步 HTTP"**

草案 (2)→(7) 是一条多步骤、多工具、逐页循环的流程。现状 P03 是同步请求（百度 20-60s / 本地 <1s），无任务化、无进度、无中间态；`ppt_tasks` 表只有 `status` 四态与 `outline` 文本。

**⑦ 成本口径（与 3.2 ④ 呼应）**

- 网搜/截图/下载：browser-use 走项目配置的 LLM（按 token 计）；
- 逐页截图 + 视觉自检：**每页至少一次多模态调用**（10 页 → 10 次以上），叠加图片理解时更多；
- 这一项与"百度一次生成出全套"是**完全不同的成本结构**（N 次视觉 vs 1 次专用 API）。

### 4.3 草案与现状的差距清单（事实性，不含取舍）

| 草案要素 | 现状 | 差距类型 |
|---|---|---|
| 每用户产物沙箱 | 平铺 + 模板全账号共享 | 目录结构与归属模型改造 |
| 专用多模态配置段 | 只有 QWEN_* 半成品、主模型纯文本 | 新增配置 + 新增图像输入通道 |
| 网搜/下载工具 | 无；外部项目有成熟实现（独立 venv + 子进程） | 引入外部运行时（browser_venv）+ 下载暂存/搬移 |
| 向量检索工具 | 函数已存在（01 大点） | 包装成 tool |
| 文件操作工具 | 无；OfficeCLI 已在本机且能力覆盖 | 集成第三方 exe（含常驻进程生命周期） |
| 逐页截图自检 | 无 | OfficeCLI `view screenshot` 现成 |
| 前端 6 项配置 | 只有 topic / template / md 上传 | 表单与 schema 扩项 |
| 大纲策划 → 逐页制作 | 一次 LLM 出大纲 + 固定渲染 | 需要"规划 → 执行 → 自检"的循环编排 |
| 长流程交互 | 同步 HTTP、无进度 | 任务化/进度通道 |

### 4.4 需要澄清的点（记录，不预设答案）

| # | 问题 |
|---|---|
| Q7 | "每用户沙箱"里放什么边界：只放产物，还是连中间产物（检索结果、截图片、下载的模板/图片）都放；配额/清理策略是什么 |
| Q8 | Agent 的"逐页自检"判定标准：仅"能截出图、无 low_contrast"，还是要有视觉模型判断"排版是否难看/文字是否溢出"；后者每页一次多模态调用，成本与时延按页线性增长 |
| Q9 | 网搜下载的**版权与可信源范围**：草案说"可信源网址"，需要落到具体白名单（是否包含国内免费 PPT 站、图库的商用授权） |
| Q10 | 模板来源优先级：用户自选模板 vs Agent 网搜模板，两者的渲染路径是否会分叉（一份走"套模板填充"，一份走"自己排版"） |
| Q11 | 页数/字数与"超页超字数"的判定点放在哪一步（大纲阶段还是逐页阶段），超了怎么办 |
| Q12 | 是否保留百度链路（若额度政策确已结束），保留时如何计量、如何避免全项目共享额度被单人耗尽 |
| Q13 | 产物可编辑性：Agent 产出的 pptx 是否要保留"可二次编辑的结构"（OfficeCLI 编辑的是 pptx 本身，保留了形状/文本框结构） |

---

## 话题 5：三处差距的落地调研 + 素材源（免费图库 / PPT 模板）取证

### 5.1 差距一：账号隔离的 `user_data` 归口模式（学习 deep_search_pro）

**对方项目的做法（原文与代码事实）**

`deep_search_pro/utils/paths.py`（166 行）是该项目的"用户数据唯一事实源"，文档头记录了这个模块的由来：

> 账号级数据原本散在 7 个根下（`agents_docs/`、`output/`、`pic/`、`rag_knowledge/db/`、`data/sandbox/`、`data/probe_sets/`、`data/tts_ref/`），每个模块各自拼路径、各自维护一份"账号目录清单"（实测三份互不一致），清理账号时反复漏项。2026-10-13 用户拍板：在项目根新建 `user_data/` 归口全部账号级用户数据（`data/personal.db` 暂留 `data/`）。之后**谁要用户数据路径都来问这里，不要在别处再拼一次**。历史教训：同一个文件被三处各拼一遍 → 出现过 `agents_docs/agents_docs/{user}/` 的"写进 A、读的是 B"事故。

目录树（原文）：

```
user_data/agents_docs/{账号}/        人格 SOUL / 记忆 MEMORY / 人格库 / skills
user_data/output/{账号}/             周报、导出稿、测评打分表、tts/ 音频
user_data/pic/{账号}/                背景图 + chat_images/（HTTP 仍挂在 /pic）
user_data/rag_knowledge/db/{账号}/   知识库向量（chroma / chroma_route / store.json）
user_data/sandbox/{账号}/            命令执行沙箱（另含匿名兜底 public/）
user_data/probe_sets/{账号}.json     测评题集
user_data/tts_ref/{账号}/            音色克隆参考音频
```

设计要点（可迁移的 4 条）：

| 要点 | 实现 |
|---|---|
| 单一事实源 | 所有路径由 `paths.py` 的取路径函数提供（`user_dir/output_dir/pic_dir/rag_db_dir/sandbox_dir/...`），**本模块只给路径、不建目录、不写文件**，落盘要显式 `ensure_dir()` |
| 账号级目录清单只维护一份 | `ACCOUNT_DIRS = (AGENTS_DOCS, OUTPUT_DIR, PIC_DIR, RAG_DB, SANDBOX_ROOT, TTS_REF_DIR, PROBE_ARTIFACTS)`，清理/统计/迁移都读它 |
| 匿名兜底 | `ANON_USERNAME = "public"`（与 shell 运行时同口径） |
| 清理范围显式白名单 | `PROBE_ARTIFACT_DIRS` 专门标注"清理只允许碰这些"，并写明**不含** `TTS_REF_DIR`（误删丢用户素材）、**不含** `RAG_DB` |

**一处必须注意的事实**：`_name(username)` 只做 `str(...).strip()`，**没有校验 `..` / 路径分隔符**；它的防穿越是分散在调用点做的（例如 `api/probe.py:305` 注释"必须落在本账号 sandbox 内"）。也就是说"归口"解决的是**口径一致**，不解决**注入**。

**本项目的现状（对照）**

| 数据 | 现状位置 | 是否按账号 |
|---|---|---|
| 成品 pptx | `backend/ppt_files/u{uid}_t{tid}_{主题}.pptx`（平铺，实测 4 个文件 50MB） | 文件名带 uid，但目录共享 |
| 用户上传模板 | `backend/knowledge_src/ppt_model/<uuid8>_<原名>.pptx`（与知识库同级） | **全账号共享**（00 大点口径：模板团队共享） |
| 隐患照片 | `backend/uploads/`（101 张） | 文件名随机，DB 记归属 |
| 试卷/PPT 导出件 | `backend/exam_files/`、`ppt_files/` | 平铺 |
| 会话/AI 产物 | MySQL（`conversations`/`chat_messages`/`ppt_tasks`） | 表内 `user_id` |

**本项目适配时要回答的具体差别**（事实层面，不含取舍）：PPT Agent 会新增四类文件——下载的模板、下载的图片、逐页截图的 PNG、中间稿（大纲/文稿/JSON）；这四类是"临时/中间"还是"用户资产"、是否要跨任务复用、按 `user_id` 还是 `username` 分目录（本项目登录用 JWT 里的 `user_id`，无 username 唯一性约束）。

### 5.2 差距二：PPT Agent 的多模态模型要单独一段 env

**事实**

| 事实 | 证据 |
|---|---|
| 主模型是纯文本 | `llm_client.py:34,46` 两个 `ChatOpenAI`（DeepSeek 主 / agnes 备），没有图像输入通道 |
| 现有视觉段只有三行，且**当前指向 DeepSeek 的视觉实验模型** | `.env`：`QWEN_BASE_URL=https://api.deepseek.com/v1`、`QWEN_MODEL=deepseek-v4-flash-vision-exp`（`config.py:40-42` 定义，`vision.py:41-49` 使用） |
| 历史注释说开发期曾切到 AGNES 免费低质视觉 | `.env:20` 注释「方案一开发测试阶段视觉模型暂时切换到 免费低质AGNES（vision.py 读取 QWEN_*）」 |
| 视觉调用方式 | `vision.py` 用 OpenAI 兼容 content（text + image_url data URL），与主模型同一套 SDK |

即：本项目已有"另一套模型配置段"的先例（QWEN_*），**PPT Agent 需要的是第二段**（草案要求"专门配置段"），候选端点都是 OpenAI 兼容：`deepseek-v4-flash-vision-exp`（实验）、`agnes-2.5-flash`（当前降级模型，是否支持图像需实测）等。

### 5.3 差距三：前端进度条 + 后端阶段日志

**草案阶段（原文顺序）** = 进度条的自然分段：(2) 主题预处理/检索 → (3) 下载模板与图片 → (4) 充分性评估/补充搜索 → (5) 策划大纲 → (6) 逐页制作（每页自截图自检）→ (7) 产出。

**现状可用的通道与缺口（事实）**

| 项 | 现状 |
|---|---|
| 流式通道 | 仅 AI 问答在用：`routers/chat.py` 返回 `StreamingResponse`，帧格式 `data: {"meta":…}` / `data: {"delta":…}` / `data: [DONE]` |
| `ppt_tasks` 表 | 只有 `status`（草稿/生成中/已完成/失败）与 `outline`；**无 progress / stage / step / updated_at 字段** |
| 生成接口 | `POST /api/ppt/{id}/generate` 同步阻塞，无进度回报 |
| 前端 | `genPpt()` 用 `await api(...)` 后 `alert()`，等待期间只有一个 confirm 文案"约 20-60s" |
| 现成的"四态"语义 | `草稿 / 生成中 / 已完成 / 失败` 已被前端 badge 使用（`index.html:1721`） |

**备选（记录，不选）**：

- A：沿用问答的 SSE（`text/event-stream`），每阶段/每页推一帧，前端读流更新进度条；
- B：任务化 + 轮询（新增进度字段或 Redis key，前端 `setInterval` 拉 `/api/ppt/{id}/progress`）；
- C：混合——SSE 推事件、DB 落最终态（断线后靠轮询补齐）。

同时需要一个"阶段日志"约定（打给运维/用户看的），现有 `logger` 只用文本（`logger.info("PPT 本地模式生成: task=%s")`），无结构化字段。

### 5.4 免费图片库 / PPT 模板源取证（2026-10-23 实测）

**图片源（按授权与自动化可行性）**

| 源 | 实测 | 授权 | 自动化 |
|---|---|---|---|
| **Openverse API**（`api.openverse.org`） | `200`，`q=construction safety` 命中 **240** 条，首条 `license=by`(CC BY) | CC 系列（含 CC0/BY/BY-SA，需按条看） | ✅ 无需 key，JSON 直取，可带 license 过滤 |
| **Unsplash API**（`api.unsplash.com`） | 无 key 返回 `401`（**端点可达**） | Unsplash License：可商用、免署名，禁止转售未修改内容 | ✅ 需免费 key |
| **Pexels API**（`api.pexels.com`） | 无 key `401`（可达） | Pexels License：可商用、免署名，类似限制 | ✅ 需免费 key |
| **Pixabay API**（`pixabay.com/api`） | 无 key `400`（可达） | Pixabay License：可商用、免署名 | ✅ 需免费 key |
| Unsplash / Pexels / Pixabay **站内** | `403 Just a moment...`（Cloudflare） | — | ❌ 站内抓取不可行，只能走 API |
| Wikimedia Commons API | 直连**超时**、走代理 `403` | CC/PD（逐条不同） | ⚠️ 本机网络下不稳定，需重试/换 UA/换端点 |
| **视觉中国 VCG**（用户提供） | 首页 `403 安全验证`；`robots.txt`：`Disallow: /*?*`、`Disallow: /login/` | **正版商用图库＝付费授权** | ❌ 不适合当自动下载源（需采购授权与其商用接口，且明确禁爬） |
| 中国政府网 / 应急管理部 | `200` / `200` | 政府网站内容（转载一般需注明来源） | ✅ 适合取**文案/数据/通报**，不适合批量下图片 |

**PPT 模板源**

| 源 | 实测 | 授权/门槛 | 自动化 |
|---|---|---|---|
| **SlidesCarnival** | `200`（1.47MB 首页） | 免费模板，通常要求保留署名（逐套不同） | ⚠️ 需解析页面 + 署名处理 |
| **SlidesGo**（Freepik） | `200`，页面含 `login` | 免费版需署名 / 付费去水印 | ⚠️ 反爬与账号体系 |
| **微软官方模板**（create.microsoft.com） | `200`（3.1MB 页面） | 免费 | ⚠️ 下载交互重，多为跳转/登录 |
| **第一PPT（1ppt.com）** | 首页 `200`；分类页 `200` 但返回 **1.7KB「您的访问出错了」**（反爬）；详情页带 Referer `200`（13KB）但**页面内无直链下载** | 站点多为"仅供学习交流"，**商用授权含糊** | ❌ 自动化成本高、授权不清 |
| 图标类（补"配图"缺口） | **Iconify API `200`**（返回 SVG，如 `tabler:helmet`）；**jsDelivr CDN `200`**（`lucide-static/hard-hat.svg`） | MIT / Apache-2.0 等 | ✅ 程序化可取 SVG，无授权风险 |

**这一轮取证得到的两条硬事实**：
1. "能程序化拿到的免费图片"主要来自 Openverse（无需 key）与 Unsplash/Pexels/Pixabay（需免费 key）；**国内正版图库（视觉中国）是付费且禁爬**；国内免费模板站（第一PPT）**有反爬且商用授权不清**。
2. 项目内**没有任何图片素材**（`knowledge_src` 0 张图、`uploads` 是隐患照片），所以"配图"要么来自上述外部 API，要么来自程序化生成的**图标/形状/图表**（Iconify + python-pptx 原生图表）。

### 5.5 进入定方案期前，仍需要用户给出的输入

| # | 需要确认的事 | 为什么必须先定 |
|---|---|---|
| I1 | 是否接受"外部免费图库 + 需署名(CC BY) / 需申请免费 API key"这一路线；若要求"全部零外部依赖"，配图只能退化为图标/形状/图表 | 决定质量上限与是否要申请 key |
| I2 | `user_data/` 适配时，账号目录用 `user_id`（JWT 里的整数）还是 `username`（可改名） | 目录一旦落盘，改名会导致数据"失联" |
| I3 | PPT Agent 的多模态模型选哪一个（`deepseek-v4-flash-vision-exp` 实验模型 / AGNES / 其他），以及是否接受"每页一次视觉调用"的成本 | 决定进度条阶段粒度与耗时预期 |
| I4 | 进度通道选 SSE / 轮询 / 混合（三者都已确认技术上可行） | 决定后端接口形态与前端改动量 |
| I5 | 中途产物（下载的模板/图片、截图片、中间 JSON）是否算"用户可见资产"、是否要保留可查 | 决定沙箱子目录结构与清理策略 |
| I6 | 百度链路去留：保留为"可选"还是本次直接移除 | 决定进度条/任务表是否需要容纳"百度黑盒 20-60s"这一段 |

---

## 话题 6：用户对 I1–I6 的确认（含新增 I7）+ 两项补充取证

### 6.1 用户原话

> 1.接收使用Openverse API源;2.user_id;3.Agenes,后面我也会接其他视觉模型;4,进度通道 SSE;5.算,用户可以自清理;6.调研过了,就是变成付费接口了,直接移除;7.Agentppt模版下载实现难的话,那就不麻烦Agent下载,要求用户自提供

映射到 5.5 的输入项：

| # | 确认结果 |
|---|---|
| I1 | 图片源 = **Openverse API**（单源） |
| I2 | 账号目录用 **`user_id`** |
| I3 | 多模态模型 = **AGNES**；后续会再接其他视觉模型（即"配置段可扩展、不止一个端点"） |
| I4 | 进度通道 = **SSE** |
| I5 | 中途产物**算用户可见资产**，**用户可自行清理** |
| I6 | 百度链路**直接移除**（理由：已变付费接口） |
| I7（新增） | 模板：**不要求 Agent 网搜下载；由用户自己提供模板** |

### 6.2 补充取证 A：AGNES 视觉能力实测（`agnes-2.5-flash`）

方法：用 PIL 生成 320×120 测试图（左侧红色方块 + 文字 `SHUDAO-7391`），以 OpenAI 兼容 `image_url` data URL 传给 `https://apihub.agnes-ai.com/v1/chat/completions`。

```
[agnes direct] 200 finish=stop
   content:   SHUDAO-7391,红色          ← 编号与颜色都答对
   reasoning_len: 215
[agnes proxy]  200 finish=stop
   content:   SHUDAO-7391,红色
   reasoning_len: 176
```

结论与一个**必须记住的坑**：

- AGNES 确实能看图（直连与走代理都 200），可承担"逐页截图自检"；
- 它的返回**先输出 `reasoning_content`（约 180–215 字符）**，`content` 才是答案；第一次用 `max_tokens=40` 时 `finish_reason=length`、`content` 为空 —— 即**token 预算给小了会被思考内容吃光**。这与 01 大点记录过的"langchain-openai 丢 reasoning_content"同源，接入时要么放宽 max_tokens，要么直接读 `content`。

### 6.3 补充取证 B：Openverse 可达性（间歇失败，需重试）

| 时间 | 请求 | 结果 |
|---|---|---|
| 第一次（本轮早些时候） | `api.openverse.org/v1/images/?q=construction safety&page_size=3`（代理） | **200**，`result_count=240`，首条 `license=by` |
| 第二次（30 分钟后） | 同上，`q=construction worker helmet&license=cc0,pdm`（代理） | `ReadTimeout` |
| 第三次 | 同上（直连） | `ConnectTimeout`（WinError 10060） |
| 第三次 | 同上（代理） | `ReadTimeout`（45s） |

即：**Openverse 在本机网络下可达性不稳定**（同一端点 30 分钟前 200、之后连续超时）。作为"唯一图片源"，这一点会直接影响 PPT Agent 的成功率，接入时需要重试/超时/降级策略（例如拿不到图就退化为图标/形状版式），否则"图片下载"这一步会随机失败。

另：Openverse 的授权是 CC 系列**逐条不同**（CC0/PDM 无需署名，CC BY / BY-SA 需署名并注明许可），返回体里带 `license`、`creator`、`attribution` 字段可直接落"图片来源与署名"。

### 6.4 百度链路移除清单（代码事实，供方案期直接用）

| 位置 | 内容 | 处理类型 |
|---|---|---|
| `src/ai/ppt_baidu.py` | 整文件 186 行（`TEMPLATE_MAP`/`generate_ppt`/`download_pptx`/`PptBaiduError`/`outline_to_baidu`） | 删除 |
| `src/services/ppt_service.py:25` | `from src.ai.ppt_baidu import PptBaiduError, download_pptx, generate_ppt` | 删除 |
| `src/services/ppt_service.py:132-134,152-184` | `generate()` 的"百度主 / 本地兜底"分支、`except PptBaiduError`、`_page_count` 丢弃逻辑 | 重写为单一本地/Agent 通道 |
| `src/services/ppt_service.py:154-162` | 「自定义模板 → 强制本地」的特判（其存在前提就是百度无自定义模板） | 随百度移除而失去意义 |
| `src/services/ppt_template_service.py:105-109` | `is_custom_template()`（同上，为百度分支服务） | 语义随之变更 |
| `src/core/config.py:46-49` | `BAIDU_PPT_API_KEY / BASE_URL / MODEL / DAILY_QUOTA` | 删除 |
| `.env:42-45` | `BAIDU_PPT_API_KEY / BASE_URL / MODEL / DAILY_QUOTA` 四行 | 删除 |
| `src/schemas/ppt.py:8` | `TEMPLATES = ("business","simple","tech")` 三内置 | 模板改为"用户提供"后需重定义 |
| `scripts/test_ppt_api.py` | 整脚本（调用百度 SSE、写死 `tpl_id=102322`） | 删除或改指向新链路 |
| `front/safety/index.html:794` | hint「免费额度用尽后自动降级本地生成」 | 改文案 |
| `front/safety/index.html:997` | hint「生成时百度会按大纲渲染每页」 | 改文案 |
| `front/safety/index.html:1840` | confirm「百度 API 约 20-60s，免费额度每日 10 次」 | 改文案 |
| `ppt_files/` 三份百度产物 | 50MB 中的 3 份（16.9/17.0/15.9MB） | 历史产物，不影响链路 |

**连带影响（事实）**：百度移除后，"三套内置模板"在本地渲染里本来就只有一套配色（`ppt_local.py` 只有深蓝+金），加上 I7 改为"用户自提供模板"，模板体系实际只剩"用户上传的 .pptx 当基底"这一条路；而模板基底目前由 `python-pptx` 追加页面实现（`ppt_local.py:148-165`），与 Agent 用 OfficeCLI 逐页编辑是两套不同的落盘方式（同一份 pptx 两种写入方，需要明确谁是最后写入者）。





