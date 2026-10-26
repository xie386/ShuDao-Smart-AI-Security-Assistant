# 向量库重建 & Recall@K 评测 操作指南

> 适用场景：`backend/knowledge_src/` 新增/替换了大批知识文档（本次：laws 236 + cases 450 + plans 168 + procedures 234 = **1088 份 md**，manifest 1082 行），需要**清空重建** Chroma 并复测检索质量。
> 全部命令在 **`backend/` 目录**下执行，用**全局 Python**（`C:\python\python.exe`，前置 `PYTHONPATH=` 清空避免串包）。
> 嵌入/重排是 GPU 长耗时任务（**预计 8～20 分钟**，比 D3 的 3116 块翻了 3 倍量），请自己在终端跑，别挂后台。

---

## 0. 现状核对（30 秒，先跑）

```bash
cd "D:/code/aicode/ai智能体开发实战5期/weekend/ShuDao Smart AI Security Assistant/team/backend"

# 各类文档数 + 达到入库门槛(>2000B)的数量
for d in laws cases plans procedures; do
  echo "$d md=$(ls knowledge_src/$d/*.md 2>/dev/null|wc -l) 达标=$(find knowledge_src/$d -name '*.md' -size +2000c|wc -l)"
done

# 旧集合块数（重建前基线）
PYTHONPATH= C:/python/python.exe -c "import chromadb;c=chromadb.PersistentClient(path='chroma');print([(x.name,c.get_collection(x.name).count()) for x in c.list_collections()])"
```
当前基线：`safety_kb = 3116` 块，`chroma/` 目录 803MB。

---

## 1. 治理新增 HTML（若新数据是 .htm 采集来的）

已经是 .md 的可跳过。判断依据：某目录 `*.htm*` 数量 > 对应 `.md` 数量，说明有未治理的源。

```bash
# 先干跑看会产出多少
PYTHONPATH= C:/python/python.exe scripts/govern_html.py --dry-run
# 正式治理（全量四类；也可 --doc-type laws 单类）
PYTHONPATH= C:/python/python.exe scripts/govern_html.py
```
产出：同目录同名 `.md`，并回填 `knowledge_src/kb_manifest.jsonl`。

## 2. 清理空壳文档（必做）

批量采集会带进大量「附件下载页」——正文在 .doc/.pdf 里，治理后 md 只剩标题，入库纯属噪声。

```bash
PYTHONPATH= C:/python/python.exe scripts/clean_empty_kb.py --dry-run          # 先看名单
PYTHONPATH= C:/python/python.exe scripts/clean_empty_kb.py --min-chars 300 --keep-htm
```
`--keep-htm` 保留源 htm，日后改治理规则还能重跑。

## 3. 抽样体检（1 分钟，别省）

```bash
PYTHONPATH= C:/python/python.exe scripts/kb_sample_check.py
```
看两件事：① 抽样正文是否干净（无导航残留/无乱码）；② 交通/公路主题关键词覆盖度是否合理。**这一步不过关就别浪费 20 分钟嵌入。**

---

## 4. 清空 Chroma 集合（关键，不可跳过）

入库 ID 是「内容哈希」稳定 ID：文本变了 ID 就变，**旧块不会被覆盖而是残留**；已删除的空壳文档块也会一直留在库里污染检索。所以必须先清空。

```bash
# 方式 A（推荐）：删集合，脚本会自动重建
PYTHONPATH= C:/python/python.exe -c "import chromadb;c=chromadb.PersistentClient(path='chroma');c.delete_collection('safety_kb');print('deleted')"
```
```bash
# 方式 B（更彻底，顺带回收 803MB 磁盘）：整目录删掉
mv chroma chroma_bak_$(date +%m%d)   # 确认新库没问题后再 rm -rf
```
> 只增量补一类文档、且**旧文本一个字没动**时，才可以跳过本步，直接用 `--doc-type xxx` 增量入库。

## 5. 全量入库（长耗时，8～20 min）

```bash
PYTHONPATH= C:/python/python.exe scripts/collect_kb.py
```
- 行为：扫四类目录 → 结构感知父子分块 → **一次性批量嵌入**（bge-large-zh, cuda）→ 单次 upsert；`<2000B` 文件自动跳过。
- 屏幕会逐文件打印 `[laws] xxx.md: N 块（累计 M）`，最后打印总块数与分类统计。
- 预期量级：约 1000 份达标文档 → **8000～12000 块**（D3 是 83 份 / 3116 块，本次文档更短更多）。
- 若 GPU 显存告急（RTX 3050 4G）：临时把 `src/core/config.py` 的 `EMBEDDING_DEVICE` 改 `cpu` 重跑（会慢 3～5 倍），跑完改回。

**入库后立刻验一次数量：**
```bash
PYTHONPATH= C:/python/python.exe -c "import chromadb;c=chromadb.PersistentClient(path='chroma');print(c.get_collection('safety_kb').count())"
```

---

## 6. RAG 六指标评测（200 题，分跳数统计）

golden QA 已扩到 **200 题**，`scripts/extend_qa.py` 保证 单跳:双跳:多跳 = **3:4:3（60/80/60）**，每条带 `hop` 字段。
指标实现在 `scripts/rag_metrics.py`，由 `scripts/eval_qa.py` 调用。

| 指标 | 中文 | 需要 LLM | 依赖标注 |
|------|------|----------|----------|
| `chunk_recall_at_k` | chunk 级召回 @K | 否 | expected_docs |
| `chunk_precision_at_k` | chunk 级精确率 @K | 否 | expected_docs |
| `context_recall` | 上下文召回 | 否 | expected_docs |
| `answer_correctness` | 答案正确性 | 是 | expected_answer（**当前未标注 → N/A**）|
| `faithfulness` | 忠实度 | 是 | 无 |
| `answer_relevance` | 答案相关性 | 是 | 无 |

```bash
# 扩集/重标（写前自动备份 .bak，且校验 expected_docs 真在库里）
PYTHONPATH= C:/python/python.exe scripts/extend_qa.py --dry-run   # 只看分布+校验
PYTHONPATH= C:/python/python.exe scripts/extend_qa.py             # 写入

# ① 检索三指标（纯计算，不调 LLM，200 题跑全量）—— P2 主验收
PYTHONPATH= C:/python/python.exe scripts/eval_qa.py --no-rewrite

# ② 加生成三指标（每题 1 次生成 + 2 次裁判，务必抽样！）
PYTHONPATH= C:/python/python.exe scripts/eval_qa.py --no-rewrite --judge --limit 20

# 分组排查 / 只看失分 / 存明细对比调参前后
PYTHONPATH= C:/python/python.exe scripts/eval_qa.py --no-rewrite --hop multi
PYTHONPATH= C:/python/python.exe scripts/eval_qa.py --no-rewrite --category cases
PYTHONPATH= C:/python/python.exe scripts/eval_qa.py --no-rewrite --fail-only
PYTHONPATH= C:/python/python.exe scripts/eval_qa.py --no-rewrite --json-out eval_before.json

# 单题排查
PYTHONPATH= C:/python/python.exe scripts/eval_qa.py --question "桥梁施工高处坠落如何防范" --judge
```
⚠️ **200 题必加 `--no-rewrite`**：开改写要走 200 次 LLM，必定超时；改写效果属 P3 人工评分。
⚠️ **`--judge` 必须配 `--limit`**：200 题 × 3 次 LLM = 600 次调用，烧额度且必然超时（脚本会自己警告）。

输出形如：
```
===== 检索侧指标（分跳数） =====
跳数         题数    chunk召回@K    chunk精确@K       上下文召回      命中率      全中率
single      60         0.93         0.71        0.93     93%      93%
double      80         0.78         0.65        0.78     95%      62%
multi       60         0.61         0.58        0.61     92%      18%
ALL        200         0.77         0.65        0.77     93%      57%

===== 生成侧指标（LLM 裁判） =====
跳数             答案正确性       忠实度       答案相关性
ALL                  N/A      0.88        0.91
```

**怎么读（组合看才有诊断价值）**
- P2 验收看 `ALL` 行 **chunk召回@10 ≥ 0.80**
- `multi` 行天然偏低（3 份期望命中 1 份只有 0.33），**别拿总平均一刀切**；但 `multi` 全中率≈0 才是真问题 → `FUSE_TOPK` 过小 或 重排把同主题兄弟文档互相挤掉
- **召回高 + 精确率低** → 检索噪声大，下调 `topk` 或加强重排
- **召回高 + 忠实度低** → 检索没问题，是生成在编，去查 `safety_qa` 提示词约束
- **上下文召回 < chunk召回** → 父块去重丢了内容，`hybrid_retrieve` 的 `seen_parent` 逻辑值得看
- 指标显示 `N/A` 表示无有效样本（如缺 `expected_answer`），**不是 0 分** —— 脚本刻意不用 0 冒充，避免误读成"检索烂"

**补齐 answer_correctness**：在 `golden_qa.jsonl` 对应行加 `"expected_answer": "参考答案文本"` 即可，代码无需改动，有标注的题自动纳入统计。

## 7. 结果不达标怎么排（按顺序，别乱调参）

1. **先查知识覆盖，不是查链路**——期望文档到底进库了吗：
   ```bash
   PYTHONPATH= C:/python/python.exe -c "
   import chromadb;c=chromadb.PersistentClient(path='chroma').get_collection('safety_kb')
   r=c.get(where={'doc':'official-accident-1cb44b42921904695de5'},include=['metadatas'])
   print(len(r['ids']))"
   ```
   返回 0 → 该文档被 <2000B 门槛或空壳清理干掉了，**是标注/数据问题，不是检索问题**：换期望文档或补数据。
2. **标注漂移**：文档大批换新后，老 QA 的 `expected_docs` 可能已不存在。用 `scripts/extend_qa.py` 结合 `--question` 单题输出重新补标，这是本次新增大量数据后最可能的失分来源。
3. **再谈调参**：`DENSE_K / SPARSE_K / FUSE_TOPK`（D3 调优值 30）——库大了 3 倍，可试 50 观察召回增益与耗时。
4. 最后才看重排（bge-reranker-base）与 `RAG_TOP_K_RERANK`。

## 8. 一次性顺序清单（复制即用）

```bash
cd "D:/code/aicode/.../team/backend"
PYTHONPATH= C:/python/python.exe scripts/govern_html.py              # 有未治理 htm 才跑
PYTHONPATH= C:/python/python.exe scripts/clean_empty_kb.py --min-chars 300 --keep-htm
PYTHONPATH= C:/python/python.exe scripts/kb_sample_check.py
PYTHONPATH= C:/python/python.exe -c "import chromadb;chromadb.PersistentClient(path='chroma').delete_collection('safety_kb')"
PYTHONPATH= C:/python/python.exe scripts/collect_kb.py               # 8~20 min
PYTHONPATH= C:/python/python.exe scripts/eval_qa.py --topk 10 --no-rewrite
```

## 踩坑记录（沿用 D3 教训）

| 坑 | 表现 | 处理 |
|---|---|---|
| 不清集合直接重跑 | 块数只增不减，检索命中旧脏块 | 治理后**必须**先删集合 |
| 忘记 `--no-rewrite` | 50/100 题跑到超时 | 主指标一律关改写 |
| Chroma 空 list 元数据 | `ValueError` | knowledge.py 已处理（空 tags 不写字段） |
| 附件下载页入库 | 检索出一堆只有标题的块 | `clean_empty_kb.py` 先清 |
| 期望文档未入库 | Recall 突然崩到 0.x | 先 sqlite/`collection.get` 查覆盖再调参 |
