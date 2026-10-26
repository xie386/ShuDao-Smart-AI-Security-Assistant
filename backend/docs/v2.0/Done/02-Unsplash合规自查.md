# Unsplash 生产权限 · 合规自查与整改

> 依据：Unsplash 生产应用审核清单（用户 2026-10-24 提供）。本文逐条对照本项目实现，标出**违规项与整改**，并给出可直接上传的申请素材。
> 相关：[`02-PPT工坊升级·实现记录.md`](02-PPT工坊升级·实现记录.md)（图源切换与实测）、[`../Verdict/02-PPT工坊升级.md`](../Verdict/02-PPT工坊升级.md) 裁定 13/14。

**应用**：蜀道智能安全助手 · ShuDao Smart AI Security Assistant
**用途**：企业安全隐患治理平台；其中「PPT 工坊」按主题从企业知识库取据，自动生成安全培训课件（.pptx）。
**图片用途**：仅作为课件页面的**配图**，自动排版进生成的 pptx，并在页面底部与末页列出摄影署名；应用界面不提供图片浏览/下载功能。

---

## 一、逐条对照

| # | 清单项 | 结论 | 证据 / 说明 |
|---|---|---|---|
| 1 | **Hotlink photos**（照片必须热链到 Unsplash 原始 URL） | ⚠→✅ **已按"不展示即不托管"整改** | 我们**不在应用 UI 里展示 Unsplash 照片**（界面只有我们渲染的 PPT 页截图），照片只作为素材进入生成的 pptx。原来 `GET /api/ppt/{id}/assets/images/{name}` 会把原图当本地静态资源下发 —— 这属于二次托管，**已关闭**（现在只放行 `shots`）。服务端只在排版期间临时读取原图，不对外提供图片 URL。 |
| 2 | **Trigger downloads**（用户使用照片时触发下载端点事件） | ✅ | `src/ai/ppt_tools.py::_track_download()`：每张图落盘后 GET `links.download_location`（`Authorization: Client-ID <ACCESS_KEY>`），失败只记 debug 不阻断。单测 `test_unsplash_maps_fields_and_tracks_download` 断言 download 事件确实发出。 |
| 3 | **不使用 Unsplash 标志、名称不相似** | ✅ | 应用名「蜀道智能安全助手 / ShuDao Smart AI Security Assistant」；全仓无 Unsplash logo 资源（`grep -i unsplash front/` 只命中署名文案与链接）。 |
| 4 | **视觉上与 Unsplash 明确不同、名称不含 'Unsplash'** | ✅ | 独立的企业安全 SaaS 界面（深蓝/金、川蜀风），名称不含 Unsplash；Unsplash 仅作为署名词出现（这是清单第 6 条要求的）。 |
| 5 | **应用名称与描述准确** | ✅ | 见下方「二、申请描述（可直接粘贴）」。 |
| 6 | **Attribute photographer and Unsplash（全名 + 可点击链接）** | ⚠→✅ **已整改** | 改造前：课件里是纯文本 `Photo by X on Unsplash`，**不可点击**；界面只有「配图 3 张」一句。改造后三层都带链接：① 课件内容页图注 `图：Photo by {摄影师} on Unsplash` → 原图页；② 课件「图片来源与授权」页逐条可点击（`hlinkClick` 已写入 pptx，实测 12 个链接 / 6 个外部关系）；③ 应用界面「图片来源（Unsplash）」列表：`Photo by 全名 on Unsplash` → 原图页、`（摄影师主页）` → 摄影师主页、页脚 `Unsplash` → unsplash.com，全部带 `utm_source=shudao_safety&utm_medium=referral`。 |
| 7 | **上传截图（覆盖上述合规点）** | ✅ | 见下方「三、申请截图」。 |

**附注（API 指引里同样会被审的点）**

- 回链 UTM：`_with_utm()` 只给 `https://unsplash.com/` 页面链接加 `utm_source=shudao_safety&utm_medium=referral`，**不给 `images.unsplash.com` 图片 CDN 加参数**（加了会 404）。
- 配额：Demo key 50 次/小时（`x-ratelimit-remaining` 已在代码里监控，≤3 告警）；本应用一单 10 页约 6 次请求。申请 Production 后可到 5000/h。
- 不缓存/不再分发：图片仅存在于该任务的账号沙箱 `user_data/ppt/u{uid}/tasks/t{tid}/images/`，随任务删除或用户「清理工作区」一并删除，不进入知识库、不做 CDN。

---

## 二、申请描述（可直接粘贴到 Production 申请表单）

**Name**：ShuDao Smart AI Security Assistant（蜀道智能安全助手）

**Description (EN)**:

> An internal enterprise safety-management platform for construction companies. Its "PPT Workshop" turns a training topic into a safety-training PowerPoint: it retrieves the relevant regulations from the company knowledge base, plans the outline, picks one illustrative photo per section from Unsplash, lays out each slide, and renders the deck to .pptx for the safety officer to present. Unsplash photos are used only as slide illustrations: each slide carries "Photo by {photographer} on Unsplash" as a clickable link, and every deck ends with a "Photo credits & licenses" slide linking each photographer and Unsplash. The app shows no photo gallery and does not host or redistribute Unsplash images; the app requests the download endpoint whenever a photo is embedded, and every Unsplash link carries the utm_source/utm_medium parameters.

**Description (中文，参考)**：

> 面向施工企业的内部安全管理平台。其中「PPT 工坊」把培训主题变成安全培训课件：先从企业知识库检索相关法规与做法、规划大纲，再为每个章节从 Unsplash 选一张示意照片、逐页排版并导出 .pptx 供安全员授课。Unsplash 照片仅作课件配图：每页图注为可点击的「Photo by 摄影师 on Unsplash」，课件末页是「图片来源与授权」页，逐条链接摄影师与 Unsplash；应用内不提供图片浏览/下载，不托管、不再分发照片；嵌入照片时会调用 Unsplash 的 download 端点，所有回链带 utm_source/utm_medium。

**Screenshot（上传下面第三部分的图）**：`01-app-sources-panel.png`（应用界面：署名+链接+下载按钮）、`03-slide-sources.png`（课件来源页）。

---

## 三、申请截图（本目录 `unsplash/`）

| 文件 | 展示内容 | 对应清单项 |
|---|---|---|
| `01-app-sources-panel.png` | 应用界面：10 页缩略图、「图片来源（Unsplash）· 3 张」→ `Photo by 摄影师全名 on Unsplash`（可点）、`（摄影师主页）`、页脚 `Unsplash` 链接、`下载 .pptx` / `清理工作区` 按钮 | 6（署名+链接）、7 |
| `02-app-full.png` | 同上，整页（含任务列表与上方表单），用于说明应用是什么 | 3/4/5 |
| `03-slide-sources.png` | 生成课件里的「图片来源与授权」页：3 条可点击署名 + 底部「以上照片来自 Unsplash…」 | 6 |
| `04-slide-photo-caption.png` | 课件内容页：左文右图，图注 `图：Photo by {摄影师} on Unsplash`（课件内可点击） | 6 |

> 截图里的名字与链接是真机生成的：`Muraya Githiga`、`Jevgeņijs Grigorjevs`、`Jan Huber`（Unsplash 摄影师）。

---

## 四、本次整改的代码改动

| 文件 | 改动 |
|---|---|
| `src/ai/ppt_tools.py` | ① `_norm()` 增加 `creator_url` / `photo_url`；Unsplash 取 `user.links.html` 与 `links.html`，Openverse 用 landing page 兜底；② `_with_utm()` 给页面链接补 UTM（CDN 除外）；③ `write_sources()` 写入链接与像素尺寸；④ `apply_commands()` 改为返回**回执**（含每个元素的真实路径），并**剥离 `_link/_tooltip` 元数据**（officecli 会报 unknown field —— 实测踩到，整页 batch 直接失败）；⑤ 新增 `apply_hyperlinks()`。 |
| `src/ai/ppt_layout.py` | 内容页图注改为「`Photo by {摄影师} on Unsplash`」并挂 `_link`；来源页改为**一行一条**、每条独立可点击，底部加 Unsplash 授权说明。 |
| `src/ai/ppt_agent.py` | 每页 batch 后解析回执、给带 `_link` 的元素批量挂超链接（失败只告警，不阻断出片）。 |
| `src/routers/ppt.py` | `assets` 只放行 `shots`（不再下发 Unsplash 原图）；新增 `GET /api/ppt/{id}/sources`（只返回署名元数据，不下发图片）。 |
| `front/safety/index.html` | 生成完成/点击「来源」时渲染「图片来源（Unsplash）」列表：可点击署名 + 摄影师主页 + Unsplash 页脚；新增 `openPptResult()` 支持回看已完成任务的缩略图与来源。 |
| `tests/test_ppt_agent.py` | 新增/更新 6 个用例：图注与来源页必须带 `_link`、回执路径解析、超链接批量构造、元数据剥离、`images` 资产端点关闭、`/sources` 需登录。 |

**验证**：`pytest tests/test_ppt_agent.py` → **40 passed**；真机 10 页端到端 152.6s、配图 3 张、`validate` 通过、S7「问题 0 条」；pptx 内 `hlinkClick` 共 12 处、外部关系 6 条；UI 截图见上（Playwright，控制台 0 报错）。

---

## 五、还剩的风险（诚实列出）

1. **"不热链"这类条款的解读**：Unsplash 的 API 指引本意是"展示照片时请热链、不要自行托管"。我们的产品形态是**生成 pptx 文件**，照片必须被嵌入文件才有意义，因此采取了"不在 UI 展示照片 + 不对外提供图片 URL + 每张图打 download 事件 + 三层署名"的做法。若审核方要求"连嵌入也不行"，那只能改成"课件里留占位框 + 提示用户自行插图"，属于产品形态变更，需要你拍板。
2. **Unsplash License 不是 CC**：课件里写的授权是 `unsplash license`（不是 CC BY），文案已按此表述，不冒充 CC。
3. **生产配额**：现在仍是 Demo key（50/h）。审核通过后换成 Production key，代码无需改动（同一 `UNSPLASH_ACCESS_KEY`）。
