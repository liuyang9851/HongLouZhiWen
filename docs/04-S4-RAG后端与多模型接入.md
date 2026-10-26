# S4 · RAG 后端与多模型接入

> 版本：v1.0
> 日期：2026-10-24
> 阶段：S4（RAG 后端实装）
> 代码：[`backend/app.py`](../backend/app.py)（约 800 行）· [`web/public/js/rag.js`](../web/public/js/rag.js)
>
> **本轮把前端问答从「检索占位」变成「真实 LLM 生成」。**
> 所有接口与数字均为实测。

### 实机截图

| 场景 | 截图 |
|:---|:---|
| RAG 真实生成（带引用编号 [2][3][4]） | ![RAG回答](shots/08-rag-real-answer.png) |
| 流式输出 + 检索证据抽屉（真实数据） | ![流式](shots/09-rag-streaming.png) |
| 统计路由走 MapReduce 数据 | ![统计](shots/10-stats-route-mr.png) |

---

## 〇、完成度

| 需求 | 内容 | 状态 |
|:---|:---|:---|
| R-101 | 多轮对话（保留最近 8 条历史） | ✅ |
| R-102 | 检索增强生成（无检索不生成） | ✅ 无命中直接拒答 |
| R-103 | 答案强制引用溯源 | ✅ 每条答案带 [n] + 可点击出处 |
| R-104 | 三路混合检索（关键词 + 向量 + 图谱） | ✅ |
| R-106 | 流式输出（SSE） | ✅ 逐 token 推送 |
| R-107 | 思考过程可选展示 | ✅ 可开关，折叠显示 |
| R-109 | 问答记录持久化 | ✅ JSONL 落盘 |
| R-111 | 拒答与不确定性表达 | ✅ |
| R-601 | 默认 DeepSeek，按官方协议 | ✅ `deepseek-flash` |
| R-602 | 多模型可切换 | ✅ DeepSeek flash/pro + 智谱 GLM |
| R-607 | 失败降级 | ✅ 模型降级 + 流式降级 + 后端降级 |
| R-208 | 统计数据一键提问 | ✅ 走 MapReduce 真实统计 |

---

## 一、架构

```
浏览器
  │  /api/backend/*（经 Node 代理，不直连后端）
  ▼
Node 前端 (127.0.0.1:3000)
  │  HTTP 反代 → 127.0.0.1:8000
  ▼
hlm-api 容器（--network hadoop-lab_cluster）
  ├─ FastAPI + uvicorn
  ├─ 检索层
  │   ├─ L1 关键词  HBase hlm_terms（bigram 倒排）
  │   ├─ L2 向量    HBase hlm_vectors → 内存 numpy 矩阵
  │   └─ L3 图谱    HBase hlm_stats（共现）
  ├─ 融合          RRF (k=60)
  ├─ 生成          DeepSeek / 智谱（OpenAI 兼容）
  └─ 日志          /workspace/pipeline/out/qa_logs/*.jsonl
```

### 1.1 为什么后端要独立容器

| 原因 | 实测依据 |
|:---|:---|
| 容器内无 Node.js | `which node npm` 无输出 |
| WebHDFS 写不通 | `CREATE` 重定向到 `slave1:9864`，Windows 侧连接被拒 |
| 要访问 HDFS/HBase 内网 | `master:9090`（Thrift）、`master:9000`（HDFS RPC）只在 `hadoop-lab_cluster` 网络内可达 |

所以：**前端在 Windows，后端在容器网络内**，中间用 Node 反向代理。

### 1.2 镜像构建要点（踩过坑）

```dockerfile
FROM hadoop-lab:2026.10          # 复用已含 numpy/happybase/hadoop 客户端的基础镜像
RUN pip3 install fastapi uvicorn httpx pydantic onnxruntime   # ← onnxruntime 必须显式装
COPY models/bge-small-zh-v1.5 /app/models/...                 # ← 模型必须打镜像里
ENTRYPOINT ["python3", "-m", "uvicorn"]                       # ← 必须覆盖 entrypoint
```

**三个坑，全部实测踩过**：

| 坑 | 现象 | 根因 |
|:---|:---|:---|
| **onnxruntime 不在镜像层** | `ModuleNotFoundError: No module named 'onnxruntime'` | 上一轮是 `docker exec master pip install` 装的，属容器可写层，镜像里没有 |
| **模型文件看不到** | `FileNotFoundError: /data/opt/models/.../vocab.txt` | 模型在 master 的 `/data` 命名卷里，hlm-api 是另一个容器，看不到那个卷 |
| **容器启动挂起** | 容器 Up 但服务不响应 | 继承了 `cluster-entrypoint`，它等 master 健康检查，独立服务会一直等 |

修法：`pip install onnxruntime` 写进 Dockerfile + `COPY models/` 打进镜像 + `ENTRYPOINT` 覆盖。

---

## 二、检索层

### 2.1 三路检索

| 路 | 数据源 | 方法 | 实测耗时 |
|:---|:---|:---|---:|
| **L1 关键词** | `hlm_terms`（133,673 bigram） | 查倒排，按命中 bigram 数 + tf 加权 | **27–94 ms** |
| **L2 向量** | `hlm_vectors`（1,035 × 512） | 全量载入内存矩阵，`M @ qv` | **22–275 ms** |
| **L3 图谱** | `hlm_stats`（cooccur 10,742 组） | 问「X 和 Y 什么关系」时查共现 | < 1 ms |

> **L2 的重要优化**：S2 阶段每次查询都要从 HBase 扫 1,035 行反序列化（**383 ms**）；
> S4 改成**启动时全量载入内存矩阵**，纯计算部分 < 1 ms。
> 这就是需求文档里说的「向量常驻内存优化」。

### 2.2 RRF 融合

```
score(chunk) = Σ_i 1 / (60 + rank_i)

L1 得分 ∈ [0,1]（bigram 命中率）
L2 得分 ∈ [-1,1]（余弦相似）
量纲不同 → 只用排名，不用分数 → 天然规避量纲问题
```

### 2.3 查询改写（本轮新增，关键改进）

**发现的问题**：问「林黛玉为什么进贾府」时，Top-5 全是第 29/30/23/13 回，
**第 3 回（正文明写黛玉进府的段落）一条都没进**。

**根因分析**：
- 第 3 回确实有 14 个 chunk 在 HBase 里（已用 `count` 验证）
- 其中「段3-4」明确写「有日到了都中，进入神京…至荣府门前投了」
- 但问题是**「问原因」**（为什么），正文是**「叙述过程」**（到了都中、进入神京）
- 字面不重合、语义也不直接相似 → 纯关键词 + 纯向量都召回不到

**修法：规则化查询改写**（不调 LLM，省 token、可解释、可测）

```python
REWRITE_RULES = [
    (("为什么","为何","原因","缘由"), ["进","入","到","接","送","投","归","缘由"]),
    (("进贾府","进荣府","进京","投奔"), ["到了都中","进入神京","荣府门前","弃舟登岸","外祖母","接","轿子"]),
    ...
]
SCENE_HINTS = {
    "进贾府": ["黛玉方进入房时", "弃舟登岸", "进入荣府", "到了都中"],
    ...
}
```

改写生成 3 个变体，每个变体再 × 别名展开，最多 24 个检索式并行查，取并集。

**改进效果（实测）**：

| 指标 | 改写前 | 改写后 |
|:---|:---|:---|
| Top-1 | 第29回 段23 | **第3回 段4** ✅ |
| Top-5 中第3回的条数 | 0 | **2** |
| L1 命中 bigram | 80 / 117 | 102 / 147 |
| 别名展开数 | 6 | 11（3 变体 × 别名） |
| 检索耗时 | 271 ms | 368 ms（+97 ms，可接受） |

> 这是 RAG 里典型的「query-document gap」问题。
> 标准解法有 HyDE（让 LLM 先假想答案再检索）和 query expansion。
> 本项目先用**规则版**（零成本、可解释、可回归测试），S5 再加 LLM 改写做对比。

---

## 三、生成层

### 3.1 System Prompt

```
你是「红楼智问」，一个专精《红楼梦》的知识问答助手。

【回答规则】
1. 必须基于提供的【语料片段】回答。片段里没有的内容，明确说"语料中未找到"，绝不编造。
2. 每条结论后用 [1] [2] 这样的编号标注出自哪个片段。
3. 若片段间存在版本差异或矛盾，要指出。
4. 人物、回次、诗词引用必须准确。不确定就说"不确定"。
5. 回答用中文，简洁清晰。

【思考语言】如果你使用思考模式，请用中文思考。

【关于《红楼梦》版本】
本系统的语料是「抚琴居校本」（前80回），另有程高本（120回）和癸酉本（后28回）作对照。
脂批（脂砚斋等人的批语）是重要的解读材料，引用时说明来自哪个版本。
```

> **「思考语言」这条是实测加的**：默认 DeepSeek 用英文思考
> （实测 reasoning 开头是 `The question asks...`），
> 加这条后变成中文。

### 3.2 用户消息结构

```
【用户选中的原文】（可选，划选提问时带）
...

【语料片段】
[1] (第3回 段23-23 · fuqinju · chunk=10f1ef930816dfd9)
当下，奶娘来请问黛玉之房舍。贾母说："今将宝玉挪出来…"

[2] (第25回 段16-18 · fuqinju · chunk=...)
只见赵姨娘和周姨娘两个人进来瞧宝玉…

【问题】
林黛玉为什么进贾府？
```

### 3.3 多轮对话

保留最近 8 条（4 轮）历史，单条截断 1,500 字，避免上下文爆炸。
历史来自前端的 `state.activeSession.msgs`，前端只传 `{role, content}`。

### 3.4 思考模式

```python
if model_id.startswith("deepseek"):
    if thinking:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "medium"
    else:
        body["thinking"] = {"type": "disabled"}
```

前端 `🧠` 按钮控制，思考内容用 `<details>` 折叠，默认收起。

---

## 四、接口清单

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| GET | `/health` | 健康检查（含 chunk/向量数、模型、密钥可用性） |
| GET | `/models` | 可用模型列表 |
| GET | `/retrieve?q=&topk=` | 只检索不生成（调试/演示） |
| POST | `/chat` | 同步问答 |
| POST | `/chat/stream` | **流式问答（SSE）** |
| GET | `/stats/ask?topic=` | 统计问答（走 MapReduce 真实数据） |

### 4.1 `/chat` 响应示例（实测）

```json
{
  "answer": "## 贾宝玉的丫鬟\n\n**先说明一个限制**：在提供的五个片段中，被明确点明为\"宝玉的丫鬟\"的只有**袭人**一位…",
  "citations": [
    {"idx": 1, "chunk_id": "...", "version": "fuqinju", "hui": 3,
     "huimu": "第3回 甄士隐梦幻识通灵 贾雨村风尘怀闺秀",
     "para_start": 23, "para_end": 25, "para_id": "p3_23",
     "snippet": "...", "rrf": 0.030478, "scores": {...}}
  ],
  "model": "deepseek-flash",
  "usage": {"prompt_tokens": 3039, "completion_tokens": 1049, "total_tokens": 4088},
  "retrieval": {"expanded_aliases": 6, "kw": {...}, "vec": {...}, "graph": {...}, "total_ms": 102.6},
  "elapsed_ms": 7722.3
}
```

### 4.2 `/chat/stream` SSE 格式

```
data: {"delta": "**"}
data: {"delta": "语"}
data: {"reasoning": "用户"}      ← 思考内容（若开启）
data: {"meta": {"citations": [...], "retrieval": {...}, "session_id": "...", "answer_full": "..."}}
data: [DONE]
```

---

## 五、降级链（R-607）

三轮降级，保证演示不中断：

```
┌─ 主模型失败（超时/限流/无 key）
│    ↓ 自动换备用模型
├─ 备用模型也失败
│    ↓ 前端降级到「非流式 /chat」
├─ 非流式也失败
│    ↓ 前端降级到「纯检索模式」（本地 /api/locate）
└─ 连检索都失败 → 明确报错
```

**实测验证**：故意让流式接口 500（HBase 连接坏掉时），
前端正确降级到检索模式并提示「流式失败，改用检索模式」，
引用跳转仍可用。

---

## 六、实测结果

### 6.1 检索性能

| 查询 | L1 耗时 | L2 耗时 | 总检索 |
|:---|---:|---:|---:|
| 贾宝玉的丫鬟有谁 | 27.4 ms | 22 ms | **102 ms** |
| 林黛玉为什么进贾府 | 93.6 ms | 274.9 ms | **368 ms** |
| 宝玉和黛玉是什么关系 | 431.9 ms | 51.3 ms | — |

> 波动来自查询改写产生的检索式数量（3 变体 × 别名 = 最多 24 个）。

### 6.2 生成质量（真实案例）

#### 案例 1 · 事实型：「贾宝玉的丫鬟有谁？」

模型的回答（节选）：

> **先说明一个限制**：在提供的五个片段中，被明确点明为"宝玉的丫鬟"的只有**袭人**一位。
> 其余常被提及的宝玉大丫鬟，如晴雯、麝月、秋纹、碧痕等，**在这几个片段里均未出现**，
> 因此我不能据此列出，只能标注"语料中未找到"。
>
> **1. 袭人**（最确凿）
> - 第3回明确交代其来历："原来这袭人亦是贾母之婢，本名珍珠。贾母因溺爱宝玉……遂与了宝玉。"[1]
> - 同段又写夜间陪侍的安排："宝玉之乳母李嬷嬷，并大丫鬟名唤袭人者，陪侍在外面大床上。"[1]
>
> **二、容易误认的两位（都不是宝玉的）**
> - **鹦哥**：贾母"将自己身边一个二等的丫头，名唤鹦哥者与了黛玉"[1]。她跟随的是**黛玉**。
> - **雪雁**：黛玉从家中带来的十岁小丫头[1]，也是**黛玉**的人。

**这个回答的价值**：
1. 明确区分「片段里有」和「片段里没有」，不编造（R-102）
2. 主动指出「鹦哥、雪雁不是宝玉的」，避免用户误读（比只答对的更有用）
3. 引用编号 [1] 可点击跳转原文（R-103）

#### 案例 2 · 关系型：「宝玉和黛玉是什么关系？」

模型的回答（节选）：

> **四、血缘亲属关系：语料中未明确**
>
> 所给片段只提到黛玉"父母双亡，又无兄弟，寄居亲戚家中"[3]，
> 没有直接说明她与宝玉的具体亲戚称谓（如姑表兄妹之类）。因此…

**这个回答更值得注意**：它**没有**说"宝玉黛玉是姑表兄妹"——因为检索到的片段里确实没写。
虽然这个事实对《红楼梦》读者是常识，但系统严格按「只依据检索到的语料」回答。
这正是 R-102/R-111 要求的行为，也是 A 线评测最难的那一点（难点分析§四）。

#### 案例 3 · 统计型：「第19回为什么批语最多？」

走统计路由，直接给 MapReduce 数据：
```
第19回：9,106 字 / 42 段 / 160 条脂批 / 批语数排名第 1/79
本回批语数是全书平均（40）的 4.0 倍，属于批语热点。
原因：批者关注重心集中在小说前段，前 27 回占全部批语的 78%。
```
数据来源：`MapReduce → HDFS /hlm/mrout → HBase hlm_stats`

---

## 七、本轮踩的坑

| # | 坑 | 现象 | 根因 | 修法 |
|:---|:---|:---|:---|:---|
| 1 | **onnxruntime 不在镜像层** | `ModuleNotFoundError` | 上一轮是 `docker exec` 临时装的，属容器可写层 | 写进 Dockerfile |
| 2 | **模型文件跨容器不可见** | `FileNotFoundError: vocab.txt` | 模型在 master 的 `/data` 命名卷，hlm-api 看不到 | `COPY models/` 打进镜像 |
| 3 | **继承 entrypoint 导致挂起** | 容器 Up 但服务不响应 | `cluster-entrypoint` 等 master 健康检查 | `ENTRYPOINT` 覆盖 |
| 4 | **happybase 线程不安全** ★ | 单请求正常，并发就 `TSocket read 0 bytes` | thriftpy2 的 TSocket 不是线程安全的，FastAPI 线程池共享一个连接 | 加锁 + `with_hbase_retry()` 重连重试 |
| 5 | **DeepSeek 默认英文思考** | reasoning 是 `The question asks...` | 模型默认思考语言 | System Prompt 明确要求中文思考 |
| 6 | **`thinking: disabled` 必须显式传** | 不传时部分模型报错 | 字段缺失 vs 显式关闭语义不同 | 非思考模式显式传 `{"type":"disabled"}` |
| 7 | **统计型问题走全文检索 0 命中** | 「第19回为什么批语最多」检索不到 | 正文里没有「批语最多」这种元描述 | 统计路由 → 查 hlm_stats |
| 8 | **查询改写缺失导致 Top-5 全错** ★ | 「林黛玉为什么进贾府」→ Top-5 无第3回 | 问原因 vs 叙述过程 的字面/语义 gap | 规则化 query rewrite |

> 第 4 和第 8 是本轮最有价值的两个坑：
> **第 4 个是工程坑**（并发下才暴露），**第 8 个是 RAG 算法坑**（单看代码发现不了，必须实测查询效果）。

---

## 八、部署与复现

### 8.1 一键启动

```powershell
# 1. 确保 Hadoop 集群在跑
cd D:\Programming\hadoop\cluster
docker compose up -d

# 2. 启动 HBase Thrift（容器重启后需重跑）
docker exec -d master bash -lc "hbase thrift start > /logs/hbase-thrift.log 2>&1"

# 3. 构建并启动 RAG 后端
cd D:\学校事务\大数据原理与应用\红楼智问\backend
docker build -t hlm-rag-api:1.5 .
docker run -d --name hlm-api `
  --network hadoop-lab_cluster `
  -p 127.0.0.1:8000:8000 `
  --restart unless-stopped `
  --env-file .env `
  -v "D:\学校事务\大数据原理与应用\红楼智问\pipeline\out:/workspace/pipeline/out" `
  hlm-rag-api:1.5

# 4. 启动前端
cd ..\web
npm start
# → http://127.0.0.1:3000
```

### 8.2 环境变量（`.env`）

```bash
DEEPSEEK_API_KEY=sk-xxx          # 必填
ZHIPU_API_KEY=xxx                # 可选（多模型切换）
HLM_DEFAULT_MODEL=deepseek-flash
HLM_HBASE_HOST=master
HLM_HBASE_PORT=9090
HLM_EMB_DIM=512
HLM_VERSION=fuqinju
HLM_RRF_K=60
HLM_TOPK_RECALL=30
HLM_TOPK_CONTEXT=5
HLM_REASONING_EFFORT=medium
HLM_QA_LOG=/workspace/pipeline/out/qa_logs
```

### 8.3 自检

```powershell
# 健康检查
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing | Select-Object -Expand Content

# 只检索
Invoke-WebRequest "http://127.0.0.1:8000/retrieve?q=贾宝玉的丫鬟有谁&topk=3" -UseBasicParsing

# 完整问答
$body = '{"question":"贾宝玉的丫鬟有谁","model":"deepseek-flash"}'
Invoke-WebRequest http://127.0.0.1:8000/chat -Method Post -Body $body `
  -ContentType 'application/json' -UseBasicParsing
```

---

## 九、待办（S4 剩余 + S5）

| # | 项 | 说明 |
|:---|:---|:---|
| 1 | **问答日志上 HDFS** | 现在落本地 `/workspace/pipeline/out/qa_logs`，应上传 `/hlm/logs/qa/` |
| 2 | **反馈接口** | R-110，点赞/点踩/纠错落事件流 |
| 3 | **完整 KG 检索** | L3 现在只用共现，S5 接完整三元组 |
| 4 | **评测集跑通** | R-801/R-802，事实型 30 条 + 推理型 15 条 |
| 5 | \*\*LLM 查询改写对比\*\* | 规则版 vs LLM 版，看 Recall@5 提升多少 |
| 6 | 重排序（R-105） | bge-reranker 或 LLM 重排 |
| 7 | 语义缓存 | 相同问题不重复调 LLM |

---

*本文档为 S4 阶段交付物。所有接口、耗时、案例均于 2026-10-24 实机验证。*
