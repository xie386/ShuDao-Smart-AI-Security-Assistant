# 知识贡献模块 · MinerU 集成计划书（v2）

> 状态：规划中（v2 修订） ｜ 阶段：优化丰富期 ｜ 落点：backend（src/services/knowledge_contrib_service.py）
> 关联模块：知识贡献（K01–K06）、kb_govern 在线清洗治理
> 前置参考：`知识贡献模块实现计划书.md`、`施工安全领域文件鉴别判定标准.md`
> **v2 修订要点**：MinerU 产品线已从 1.x（magic-pdf）升级到 **3.4**（mineru），包名、API、配置方式、DOCX 支持全部重写；本版按 3.4 重写技术方案

---

## 一、背景与动机

### 1.1 当前问题

知识贡献模块（K01）已支持 md/txt/docx/pdf 四种格式上传，但文本解析能力薄弱：

| 格式 | 当前方案 | 问题 |
|------|----------|------|
| **PDF** | `PyMuPDF` 逐页 `get_text()` | 仅纯文本抽取，丢失表格结构、公式 LaTeX、标题层级、阅读顺序 |
| **DOCX** | `python-docx` 遍历 `paragraphs` | 丢失表格内容、页眉页脚、图片中的文字 |
| **md/txt** | 直接读文本 | 无问题（已是结构化文本） |

对于复杂 PDF（多栏学术论文、扫描件含图文混排、带公式的规程），当前方案提取的文本：
- 标题层级丢失（所有文字平铺）
- 表格内容丢失或变成无结构文字碎片
- 公式变成乱码或完全丢失
- 阅读顺序错乱（多栏 PDF 尤其严重）
- 页眉页脚等噪声混入正文

这导致：**入库后 RAG 检索质量下降，LLM 难以从碎片化文本中定位到有用信息**。

### 1.2 目标

**将 MinerU 集成到知识贡献模块的 PDF/DOCX 解析管线**，替代现有 PyMuPDF/python-docx 的简单文本抽取，获得：

1. **结构化 Markdown 输出**：保留标题层级（h1-h3）、列表、表格（MD 表格语法）
2. **公式 OCR**：数学公式 → LaTeX（`$...$` / `$$...$$` 包裹）
3. **阅读顺序还原**：多栏/图文混排 PDF 按人类阅读顺序排列
4. **页眉页脚/噪声剥离**：MinerU 内置去除 header/footer/footnote/page number
5. **表格重建**：无线表/合并单元格 → HTML → Markdown 表格
6. **扫描版 PDF OCR**：PP-OCRv6 识别图片型 PDF 文字

---

## 二、MinerU 简介（v2：3.x 产品线）

**MinerU** 是 OpenDataLab 开源的一站式文档解析工具。**2025-06 起产品线重大重构**：

- **2.0（2025-06）**：包名从 `magic-pdf` 改为 `mineru`，API 全面重构，废除手动 JSON 配置，模型自动下载管理
- **3.0（2026-03）**：**原生 DOCX 解析**（无需转 PDF，官方称比转 PDF 快数十倍）、新增 `mineru-api` HTTP 服务 + 异步任务端点、滑动窗口处理万页级文档
- **3.4（2026-06，当前版）**：PP-OCRv6（OCR 精度 +11%、速度翻倍）、支持 PDF/DOCX/PPTX/XLSX

### 2.1 核心能力（pipeline 后端）

| 模块 | 功能 | 底层模型 |
|------|------|----------|
| **布局分析** | 识别页面上的文字块、表格、公式、图片区域 | doclayout-yolo |
| **OCR 识别** | 中英文文字识别 | PP-OCRv6 |
| **表格识别** | 表格结构重建（含无线表、跨页合并） | RapidTable + 有线表模型 |
| **公式识别** | 数学公式图像 → LaTeX | UniMER |
| **阅读顺序** | 区域按阅读顺序排列 | layoutreader |

### 2.2 后端选型（本机 RTX 3050 4GB）

| 后端 | 说明 | 本机可行性 |
|------|------|-----------|
| **pipeline** | 传统模型栈（yolo+OCR+unimernet），小模型，**纯 CPU 可跑**，VRAM ≥4GB 可选 GPU | ✅ **选它** |
| hybrid | pipeline + VLM 结合 | ❌ 需 ≥8GB VRAM |
| vlm | 大 VLM 端到端（MinerU2.5-Pro-1.2B），效果最好 | ❌ 需 ≥24GB VRAM（sglang） |

### 2.3 与竞品对比

| 工具 | 布局分析 | OCR | 表格 | 公式 | 开源 | DOCX 原生 |
|------|----------|-----|------|------|------|-----------|
| PyMuPDF（当前） | ❌ | ❌ | ❌ | ❌ | ✅ | — |
| python-docx（当前） | ❌ | — | ⚠️ 部分 | ❌ | ✅ | ✅ |
| marker | ✅ | Tesseract | ✅ | ✅ | ✅ | ❌ |
| **MinerU 3.x** | ✅ yolo | ✅ PP-OCRv6 | ✅ | ✅ UniMER | ✅ | ✅ **原生** |

---

## 三、现状盘点（v2 核查确认）

| 项 | 状态 | 说明 |
|---|---|---|
| MinerU 桌面版 | ✅ 已安装 | `D:\appdate\mineru\`（Electron GUI，**无法被 Python 直接调用**） |
| mineru Python 包 | ❌ 未安装 | 需 `pip install "mineru[core]"`（v2：包名已从 magic-pdf 改名） |
| 模型权重 | ❌ 未下载 | **v2：无需手动 clone**，mineru 首次运行自动下载（可走 ModelScope 镜像） |
| 配置文件 | ❌ 无 | **v2：已废除**，CLI/API 直接传参 |
| 当前 PDF 解析 | ✅ PyMuPDF 1.28.2 | `knowledge_contrib_service.py::_extract_file_text` |
| 当前 DOCX 解析 | ✅ python-docx | 同上 |
| pywin32 | ✅ 已装（312） | **v2：不再需要**（DOCX 原生解析，无需 Word COM 中转） |

**关键结论（v2 修订）**：
1. 桌面版 MinerU.exe 是独立 GUI 程序，**无法作为库集成**，必须安装 `mineru` Python 包；
2. **不再需要 DOCX→PDF 中转**（3.x 原生 DOCX 解析），Word COM / LibreOffice 方案整体删除；
3. **不再需要手动下载模型、手动创建 JSON 配置**，mineru 自动管理与下载。

---

## 四、技术方案（v2 重写）

### 4.1 总体架构

```
用户上传 PDF/DOCX
       │
       ▼
mineru 可用？ ─── 否 ──→ 降级到 PyMuPDF / python-docx（现有逻辑）
       │
       是
       ▼
┌─────────────────────────────────┐
│  mineru pipeline 后端             │
│  ├─ 布局分析 (doclayout-yolo)     │
│  ├─ OCR (PP-OCRv6)               │
│  ├─ 表格/公式识别                  │
│  └─ 输出 Markdown                 │
└─────────────────────────────────┘
       │
       ▼
返回 Markdown 文本（含 h1-h3/表格/LaTeX 标记）
       │
       ▼
kb_govern.clean_text()  ← 后续清洗治理不变
       │
       ▼
knowledge.upsert_chunks()  ← 入库不变
```

### 4.2 依赖安装（v2 更新）

```bash
# 1. 安装 mineru（CPU 版核心，全局 Python）
pip install "mineru[core]==3.4.*" -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 模型：mineru 自动下载 + 自动缓存（v2：不再手动 clone）
#    国内环境建议配置 ModelScope 镜像源：
export MINERU_MODEL_SOURCE=modelscope

# 3. 也可手动预下载到 D 盘（离线部署/首次提速，可选）：
mineru-models-download --source modelscope --output-dir D:/appdate/mineru_models
#    配合环境变量 MINERU_MODEL_DIR=D:/appdate/mineru_models

# 4. 验证
mineru --version    # 期望 3.x
```

> ⚠️ **装包提醒**：mineru 依赖 torch（本机已有 cu124）。**装前备份 requirements.txt 版本快照**，装后跑依赖体检，确认既有锁定包未被降级（吸取 pillow 被 gradio 降级的教训）。
>
> **不装 CUDA 版说明**：`mineru[core]` 自带 CPU/CUDA 支持（复用现有 torch cu124）。RTX 3050 4GB 跑 pipeline 后端小模型够用；如遇 OOM 设 `device: cpu` 即可，无需重装。

### 4.3 代码改造（v2：按 mineru 3.x API）

#### 4.3.1 改造目标函数：`_extract_file_text`

```python
# src/services/knowledge_contrib_service.py 顶部追加

import asyncio
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# mineru 运行时可用性（lazy 探测，只执行一次，线程安全）
_MINERU_AVAILABLE = None
_MINERU_AVAIL_LOCK = threading.Lock()

# 解析超时：PDF/DOCX 解析最长时间（秒），10MB/50页 约 30-100s，留余量
_MINERU_TIMEOUT = 180


def _check_mineru_available() -> bool:
    """探测 mineru 是否可用（lazy，进程内只查一次）。"""
    global _MINERU_AVAILABLE
    if _MINERU_AVAILABLE is not None:
        return _MINERU_AVAILABLE
    with _MINERU_AVAIL_LOCK:
        if _MINERU_AVAILABLE is not None:
            return _MINERU_AVAILABLE
        try:
            import mineru  # noqa: F401
            _MINERU_AVAILABLE = True
            logger.info("mineru (3.x) 已加载，PDF/DOCX 将走高质量解析")
        except ImportError:
            _MINERU_AVAILABLE = False
            logger.warning("mineru 未安装，PDF/DOCX 降级到 PyMuPDF/python-docx")
    return _MINERU_AVAILABLE


def _extract_pdf_with_mineru(path: Path, tmp_dir: Path) -> str:
    """mineru 解析 PDF/DOCX → Markdown（pipeline 后端）。

    v2：优先 CLI 子进程通路（mineru 3.x CLI 是稳定接口，跨小版本兼容），
    SDK in-process 作为备选（签名随 3.x 版本演进，以实测为准）。
    """
    import subprocess

    # CLI 主通路（--lang ch 中文、开表格/公式识别）
    proc = subprocess.run(
        [
            "mineru", "-p", str(path), "-o", str(tmp_dir),
            "-b", "pipeline", "--lang", "ch",
            "--formula-enable", "--table-enable",
        ],
        capture_output=True, text=True, timeout=_MINERU_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mineru CLI 失败: {proc.stderr[:500]}")
    md_file = next(tmp_dir.rglob("*.md"), None)
    if md_file is None:
        raise RuntimeError("mineru 未产出 Markdown 文件")
    return md_file.read_text(encoding="utf-8")


def _extract_file_text(path: Path) -> str:
    """增强版文件解析（mineru 优先，不可用时降级）。

    改造要点（v2）：
    - PDF: mineru pipeline 高质量解析（布局+OCR+表格+公式）
    - DOCX: mineru 原生解析（v2 删除 DOCX→PDF 中转方案）
    - md/txt: 保持原样（直接读文本）
    """
    ext = path.suffix.lower()

    # ---- md / txt：直接读（无需 mineru）----
    if ext in (".md", ".markdown", ".txt"):
        for enc in ("utf-8", "gbk"):
            try:
                return path.read_text(encoding=enc)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="ignore")

    # ---- mineru 可用：PDF / DOCX 走高质量解析（v2：DOCX 不再转 PDF）----
    if _check_mineru_available():
        try:
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                return _extract_pdf_with_mineru(path, Path(tmp))
        except Exception as exc:
            logger.warning("mineru 解析失败（%s），降级到原方案: %s", path.name, exc)
            # 降级到下面的原方案

    # ---- 降级方案（mineru 不可用 / 解析失败）----
    if ext == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(str(path)).paragraphs)

    if ext == ".pdf":
        import pymupdf
        doc = pymupdf.open(str(path))
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()

    return ""
```

> ⚠️ **API 版本说明（重要）**：mineru 3.x 处于快速迭代期，Python SDK 签名可能随小版本变化。本方案**以 CLI 子进程为主通路**（`mineru -p ... -o ... -b pipeline` 是稳定接口），规避 SDK API 漂移风险；若未来需要 in-process SDK 加速，以实际安装版本官方文档/`help()` 为准校准。

#### 4.3.2 调用方式变更（submit 异步化）

`submit()` 中调用 `_extract_file_text` 时，mineru 解析可能耗时 30-180s。为避免阻塞 FastAPI 请求：

```python
# submit() 内替换 content = _extract_file_text(...) 这一行：
import asyncio

try:
    content = await asyncio.wait_for(
        asyncio.to_thread(_extract_file_text, Path(settings.UPLOAD_DIR) / rel),
        timeout=_MINERU_TIMEOUT,
    )
except asyncio.TimeoutError:
    raise HTTPException(
        status_code=status.HTTP_408_REQUEST_TIMEOUT,
        detail=f"文件解析超时（>{_MINERU_TIMEOUT}s），请减少文件页数或降低文件体积",
    )
except Exception as exc:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"文件解析失败：{exc}",
    )
```

> ⚠️ **重要**：
> 1. `asyncio.to_thread` 仅在异步上下文可用——`submit()` 需改为 `async def`（FastAPI 支持），或在 submit 外层路由包装；
> 2. mineru 是 CPU/GPU 密集任务，解析应**串行执行**（CLI 子进程天然串行，且避免显存/内存争抢）；
> 3. 超时后 `asyncio.wait_for` 取消等待，CLI 子进程由 `subprocess.run(timeout=...)` 真正杀掉（双保险）。

### 4.4 性能与资源预估（v2 更新）

| 项 | CPU 模式 | CUDA 模式（pipeline） |
|---|---|---|
| 模型首次加载 | ~10-30s（含自动下载） | ~5-15s |
| 单页 PDF 解析 | ~0.5-2s | ~0.2-0.8s |
| 10MB PDF（~50页） | ~30-100s | ~15-40s |
| 模型磁盘占用 | ~1-2GB（自动下载，D 盘） | 同左 |
| 内存峰值 | ~2-4GB RAM | ~2GB RAM + ~1.5GB VRAM |
| 并发限制 | 建议 1（串行 CLI） | 建议 1 |

> 注：RTX 3050 4GB 对 pipeline 后端（doclayout-yolo + PP-OCRv6 + unimernet）够用；若 OOM 自动设 CPU，不影响功能。

### 4.5 超时与降级保护（v2：asyncio.wait_for + CLI timeout）

Windows 上 `signal.SIGALRM` 不可用（v1 方案缺陷），v2 改用：
1. **外层 `asyncio.wait_for(timeout=180)`**：FastAPI 请求层超时，返回 408；
2. **内层 CLI `subprocess.run(timeout=180)`**：真正杀掉 mineru 子进程；
3. **降级链**：mineru 失败 → 捕获异常 → PyMuPDF/python-docx 原方案 → 仍失败才 422。

---

## 五、数据流对齐（不改链路）

mineru 仅替换**文件 → 文本**这一步，后续管线完全不变：

```
用户上传 PDF/DOCX
       │
       ▼
[改造] mineru 解析 → Markdown 文本（含 h1-h3/表格/LaTeX）
       │
       ▼
[不变] content 列写入 knowledge_contributions（MEDIUMTEXT）
       │
       ▼
[不变] admin 审核通过 → kb_govern.clean_text() 清洗治理
       │                → kb_govern.quality_check() 质量校验
       │
       ▼
[不变] SPLITTERS[doc_type] 分块
       │
       ▼
[不变] upsert_chunks() 增量入 Chroma
```

mineru 输出的 Markdown 对 `kb_govern.clean_text()` 无影响：
- `clean_text()` 仅处理 HTML 残留、控制字符、噪声行、相邻重复行
- 合法 Markdown（`# 标题`、`| 表格 |`、`$公式$`）不会被清洗
- `quality_check()` 的字数统计基于纯字符数，Markdown 标记占比较小

---

## 六、风险与缓解（v2 更新）

| 风险 | 概率 | 缓解措施 |
|------|------|----------|
| mineru 3.x SDK API 演进 | 中 | **CLI 子进程为主通路**（稳定）；锁定版本 `3.4.*` |
| 模型自动下载慢/失败 | 低 | `MINERU_MODEL_SOURCE=modelscope` 镜像；`mineru-models-download` 预下载到 D 盘 |
| RTX 3050 4GB 显存 OOM | 中 | 默认 CPU 模式（pipeline 纯 CPU 支持）；CUDA 模式单并发 |
| 解析超时（大文件） | 中 | 10MB 限制 + 180s 超时（asyncio.wait_for + CLI timeout 双保险） |
| DOCX 原生解析质量 | 低 | 3.x 原生 DOCX 官方验证过（比转 PDF 快且准）；个别文件失败自动降级 python-docx |
| mineru 版本更新 API 漂移 | 低 | `pip install "mineru[core]==3.4.*"` 锁版本 |
| 表格识别失败（复杂合并单元格） | 中 | 3.4 有专属有线表模型 + 跨页合并；失败时降级 PyMuPDF 文本，不阻塞入库 |
| 扫描版 PDF 效果差 | 低 | PP-OCRv6 支持，效果优于 PyMuPDF（后者对图片 PDF 无解） |
| 装包破坏既有依赖 | 中 | 装前备份 requirements 快照，装后跑依赖体检（pillow 教训） |
| 首次解析额外等模型下载 | 中 | 预下载步骤进落地清单（A 步） |

---

## 七、落地步骤（v2 更新）

### A 环境准备（一次性）

```bash
# 1. 备份 requirements.txt 版本快照（防装包降级既有依赖）
cp requirements.txt requirements.txt.bak

# 2. 安装 mineru（锁 3.4 大版本）
pip install "mineru[core]==3.4.*" -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 预下载模型（ModelScope 镜像优先，D 盘存储；可选，不设则首次解析自动下）
mineru-models-download --source modelscope --output-dir D:/appdate/mineru_models
export MINERU_MODEL_DIR=D:/appdate/mineru_models

# 4. 验证 CLI 可用
mineru --version   # 期望输出 3.x
```

### B 代码改造

1. `src/services/knowledge_contrib_service.py` 顶部追加：
   - `_MINERU_AVAILABLE` / `_MINERU_AVAIL_LOCK` 全局变量
   - `_check_mineru_available()`（lazy + 线程安全探测）
   - `_extract_pdf_with_mineru()`（CLI 子进程通路）
   - `_extract_file_text()` 主函数改造（mineru 优先 + 降级）
2. 同步改造：`submit()` 改为 `async def`，用 `asyncio.to_thread` + `asyncio.wait_for` 调解析

### C 测试验证

```bash
cd backend

# 1. 验证 mineru 加载
PYTHONPATH= C:\python\python.exe -c "
from src.services.knowledge_contrib_service import _check_mineru_available
print('mineru 可用:', _check_mineru_available())
"

# 2. 测试 PDF 解析（准备一个测试 PDF，多栏/含表格为佳）
PYTHONPATH= C:\python\python.exe -c "
from pathlib import Path
from src.services.knowledge_contrib_service import _extract_file_text
text = _extract_file_text(Path('test_sample.pdf'))
print(f'解析完成，{len(text)} 字')
print('--- 前 500 字 ---')
print(text[:500])
"

# 3. 测试 DOCX 解析（含表格）
PYTHONPATH= C:\python\python.exe -c "
from pathlib import Path
from src.services.knowledge_contrib_service import _extract_file_text
text = _extract_file_text(Path('test_sample.docx'))
print(f'解析完成，{len(text)} 字')
print('--- 表格检测（含 | 行）---')
print([l for l in text.splitlines() if l.startswith('|')][:5])
"

# 4. 对比：同文件 PyMuPDF 解析 vs mineru 解析（人工抽检标题/表格/公式保留）
```

### D 集成测试

1. 启动服务：`PYTHONPATH= C:\python\python.exe -m uvicorn src.main:app --port 8000`
2. 浏览器 `/safety → 贡献知识 → 上传 PDF/DOCX`，验证：
   - 上传成功（解析不报错）
   - 内容正确（Markdown 格式，标题/表格可见）
   - admin 审核通过后入库（chunk_count > 0）
   - RAG 问答能检索到该文档
3. 卸载 mineru 测试降级：`pip uninstall mineru -y` → 重新上传 PDF → 应走 PyMuPDF 文本解析成功

### E 性能基线

- 记录 5 个不同大小/类型的 PDF/DOCX 解析耗时（含首次模型加载）
- 对比 mineru vs PyMuPDF/python-docx 的解析质量（人工抽检标题/表格/公式保留情况）

---

## 八、后续扩展（可选，不在本次范围）

| 扩展 | 说明 |
|------|------|
| **mineru-api 服务端化** | 3.0+ 支持 `POST /file_parse`（同步）与 `POST /tasks`（异步任务）HTTP 接口；并发需求高时起独立 mineru-api 服务，后端异步提交任务轮询结果 |
| **mineru-router 多卡调度** | 多 GPU 统一入口 + 负载均衡（v3.0+），本项目单机不需要 |
| **图片入库** | mineru 提取的图片保存到 `uploads/kb_contrib/`，Markdown 中引用路径 |
| **公式保留** | 当前 `clean_text()` 不过滤 LaTeX 标记；如未来有问题需追加规则 |
| **PPTX/XLSX 支持** | 3.4 已支持 PPTX/XLSX 原生解析，可扩展上传白名单（本期仅 PDF/DOCX） |
| **批量上传** | 支持一次上传多个文件，异步解析后批量审核 |

---

## 九、验收标准（v2 更新）

| # | 验证项 | 通过条件 |
|---|--------|----------|
| 1 | mineru 加载 | 服务启动后日志输出 "mineru (3.x) 已加载" |
| 2 | PDF 解析质量 | 多栏 PDF（如学术论文）标题层级保留，表格输出为 MD 语法 |
| 3 | DOCX 解析质量 | 含表格的 DOCX 正确提取表格内容为 Markdown（**原生解析，无需 Word COM**） |
| 4 | 扫描版 PDF | 图片型 PDF 通过 OCR 提取文字（效果 ≥ PyMuPDF） |
| 5 | 降级可用 | 卸载 mineru 后，PDF/DOCX 仍能通过 PyMuPDF/python-docx 解析 |
| 6 | 入库审核链路完整 | 上传 → 审核通过 → chunk_count > 0 → RAG 可检索 |
| 7 | 超时保护 | 超 10MB 或 50+ 页 PDF 在 180s 内返回 408/422，不卡死服务 |
| 8 | 消息联动不变 | 提交/通过/驳回消息通知正常发送 |
| 9 | 既有依赖不破坏 | 装 mineru 后跑依赖体检，requirements 锁定包版本无回退（pillow 教训） |

---

## 十、与现有模块边界（铁律）

- ✅ **改造**：`_extract_file_text`（mineru 优先 + 降级）
- ✅ **追加**：`_check_mineru_available` / `_extract_pdf_with_mineru`
- ✅ **复用**：`kb_govern.clean_text()` / `quality_check()` / `SPLITTERS` / `upsert_chunks`
- ❌ **不改**：`knowledge.py` / `rag.py` / `embeddings.py` / `reranker.py` / 前端审核页面 / 判定标准
- ❌ **不引入新向量库 / 不重建 Chroma**
- ⚠️ **v2 删除（相对 v1）**：`_docx_to_pdf`（Word COM/LibreOffice 中转）、`magic-pdf.json` 配置、手动模型 clone、`ModelSingleton` 手动单例——全部由 mineru 3.x 原生能力替代