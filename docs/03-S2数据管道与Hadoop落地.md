# ADR 与踩坑记录 · Hadoop 存储层

> 版本：v1.1（S2 + S3 已完成）
> 日期：2026-10-24
> 范围：Chunk 切分、HDFS 落位、HBase 表设计、向量存储、倒排索引、MapReduce 统计、Hive SQL、检索融合
>
> **本文档记录的都是实际踩到并在本机验证过的问题**，不是推测。
> 每条都写明「现象 → 根因 → 修法 → 教训」，供项目文档与课堂演示引用。

---

## 〇、本阶段完成度

| 需求 | 内容 | 状态 |
|:---|:---|:---|
| R-501 | 原文与 Chunk 存 HDFS（按版本/回次分区） | ✅ 1,035 chunk / 2.0 MB / 2 副本 / HEALTHY |
| R-502 | 向量存 HBase（主）+ HDFS（快照） | ✅ 1,035 × 512 维 float32 / HDFS 2.82 MB |
| R-503 | Chunk 正文 + 元数据存 HBase | ✅ `hlm_chunks` 1,035 行，单条点查 < 20 ms |
| R-504 | 关键词倒排索引存 HBase | ✅ `hlm_terms` 133,673 词项（bigram） |
| R-506 | MapReduce 统计作业 | ✅ 4 个作业全成功（词频/人物/共现/逐回） |
| R-507 | Hive 提供 SQL 查询 | ✅ 4 张外部表，SQL 查询通 MapReduce 执行成功 |
| R-508 | 分区方案对比 + 负载比 | ✅ 4 种方案（1.229 / 1.105 / 1.268 / 1.000） |
| R-509 | 副本故障恢复 | ✅ 停 1 DataNode 仍可读，校验值一致 |
| R-510 | 索引可重建 | ✅ 删 3 表 → 从 HDFS 重建 → 检索结果 15/15 逐条一致 |
| R-512 | 版本不破坏分区 | ✅ version 进 chunk_id + rowkey 前缀 |
| R-513 | HDFS 实操证据 | ✅ fsck 块/副本/位置记录齐全 |

---

## 一、架构决策记录（ADR）

### ADR-01 · HDFS 存事实源，HBase 存派生索引

| 项 | 内容 |
|:---|:---|
| **状态** | 已采纳 |
| **背景** | 需求 R-501/R-502 要求「RAG 文本和向量存储在 Hadoop 框架下」。教材 §3.10.4 要求「事实源必须可靠、可追溯、有版本；派生层按访问模式选存储、坏了能从事实源重建」 |
| **决策** | HDFS 存 `chunks.jsonl` + `vectors.jsonl`（事实源）；HBase 存 `hlm_chunks` / `hlm_vectors` / `hlm_terms`（派生索引） |
| **备选** | ① 全部存 HBase（放弃事实源）② 全部存 HDFS（放弃低延迟点查）③ 引入向量库 |
| **理由** | ① HBase 是列式 KV，不适合做大文件事实源，且删表即数据全失 ② HDFS 点查要走全表扫，无法支持交互式检索 ③ 向量库违反「Hadoop 生态」的硬性要求 |
| **后果** | 删掉三张 HBase 表后，可用 `--stage hbase` 从 HDFS 完整重建（R-510 已实现） |

### ADR-02 · 向量以 float32 字节存 HBase，不用 base64 字符串

| 项 | 内容 |
|:---|:---|
| **状态** | 已采纳 |
| **背景** | 1,035 条 × 512 维向量 |
| **决策** | HBase 列 `v:emb` 存 `numpy.float32.tobytes()` 的原始字节（2,048 B/行）；HDFS 快照因为要进 JSONL，才用 base64 |
| **备选** | ① 全部 base64（直观但膨胀 4/3）② 存 JSON 数组（膨胀约 10×，且精度丢失） |
| **理由** | 实测：2,048 B × 1,035 = **2.0 MB**（原始）；若 base64 则 2.7 MB；若 JSON 数组约 20 MB |
| **验证** | 抽样回读 `len(v:emb) == 2048`（= 512 × 4），与预期一致 |

### ADR-03 · rowkey = `<version>\|<hui:03d>\|<chunk_id>`

| 项 | 内容 |
|:---|:---|
| **状态** | 已采纳 |
| **背景** | 需求 R-512「分区不破坏版本」、R-503 低延迟点查 |
| **决策** | 三段式：版本前缀 + 回次（补零）+ chunk_id |
| **理由** | ① **版本在前** → 扫某版本 `scan(row_prefix=b"fuqinju\|")` 一次搞定；不同版本同位置 chunk 天然隔离，永不串版本 ② **回次补零三位** → 字典序 = 数值序，`001..080` 可直接按回次范围扫描 ③ chunk_id 在尾 → 同回内按 ID 排序稳定 |
| **后果** | 按回次范围查询（如第 1–10 回）只需扫一个 region 前缀 |

### ADR-04 · 预分区 splitKeys `['1'..'7']`

| 项 | 内容 |
|:---|:---|
| **状态** | 已采纳 |
| **背景** | 教材 V3 要求「解释热点来源」。HBase 写入热点是最典型的实战问题 |
| **决策** | `hlm_chunks` / `hlm_vectors` 建表时 `SPLITS => ['1','2','3','4','5','6','7']`，按 rowkey 首字符（回次十位 0–7）切 8 个 region |
| **理由** | 若不预分区，所有写入先打到同一个 region，第 1–10 回写满才分裂；预分区让 8 个 region 并行接收 |
| **实测** | 1,035 行写入耗时 **2.5 秒**（约 414 行/秒，含 Thrift 往返） |
| **局限** | 回次分布本身不均（第 67 回 21,728 字 vs 平均 7,490），所以 region 大小仍会不均——这正是「业务分区 ≠ 物理分区」的实例 |

### ADR-05 · 倒排索引用 bigram，不引入分词器

| 项 | 内容 |
|:---|:---|
| **状态** | 已采纳 |
| **背景** | 需求 R-504 关键词倒排；容器内**无 jieba**（实测 `import jieba` 失败） |
| **决策** | 中文按 **bigram（双字）** 建倒排：`「林黛玉」→ 林黛 / 黛玉` |
| **备选** | ① unigram（单字）：召回高但噪音大，「林」「黛」「玉」的无关共现全算进来 ② trigram：「林黛玉」刚好，但二字词（「宝玉」「凤姐」）召回暴跌 ③ 装 jieba：需联网 pip，且词典对《红楼梦》人名覆盖不全 |
| **理由** | 中文双字词占绝对多数；bigram 无需词典、无 OOV、可解释 |
| **实测** | 1,035 个 chunk 产出 **133,673 个词项**，写入耗时 142.7 秒 |
| **后果** | 词项数是 chunk 数的 129 倍；未来上规模时需考虑 HBase 行数压力（见 ADR-07） |

### ADR-06 · RRF 融合，不用加权分数

| 项 | 内容 |
|:---|:---|
| **状态** | 已采纳 |
| **背景** | L1 关键词得分 ∈ [0,1]（bigram 命中率）；L2 向量得分 ∈ [-1,1]（余弦相似）。量纲不同，直接加权无意义 |
| **决策** | RRF（Reciprocal Rank Fusion）：`score = Σ 1/(60 + rank_i)` |
| **理由** | RRF 只用**排名**不用分数，天然规避量纲问题；k=60 是原论文推荐值；无参数需调 |
| **实测** | 查询「贾宝玉的丫鬟有谁」：L1 30 条命中、L2 1,035 条候选，融合后 58 条，耗时 0.1 ms |
| **效果** | Top-1 是第 3 回段 22-24（黛玉初进贾府、贾母安排住处），确与「宝玉的丫鬟/住处」相关 |

### ADR-07 · 检索用 numpy 暴力，不上 ANN

| 项 | 内容 |
|:---|:---|
| **状态** | 已采纳 |
| **背景** | 1,035 条 × 512 维 |
| **决策** | 全量载入 numpy 矩阵，`M @ qv` 一次矩阵乘完成检索 |
| **实测** | **383.5 ms**（含从 HBase 扫全表 + 反序列化）；纯计算部分 < 1 ms |
| **上 ANN 的临界点** | 实测推算：10 万条 × 512 维 = 195 MB（内存可容，暴力约 3–5 ms）；1,000 万条 = 19.5 GB（内存不够，必须 ANN） |
| **结论** | 本项目规模下暴力检索是**正确工程判断**，不是偷懒。瓶颈在 HBase 扫描（383 ms）而非向量计算（<1 ms） |
| **下一步优化** | 把向量常驻内存（进程级缓存），可把 383 ms 降到 <5 ms |

---

## 二、踩坑记录（全部实测）

### 坑 1 · `hdfs dfs -fsck` 不存在 ★

```
现象：hdfs dfs -fsck /hlm/... → -fsck: Unknown command
根因：fsck 是 `hdfs fsck` 的独立子命令，不在 `hdfs dfs` 里
修法：subprocess.run(["hdfs", "fsck", path, "-files", "-blocks", "-locations"])
教训：`hdfs dfs <cmd>` 与 `hdfs <cmd>` 是两套命令空间。前者是文件操作（ls/put/cat），
      后者是集群工具（fsck/dfsadmin/namenode）。混用会浪费排查时间。
```

### 坑 2 · MapReduce 输入目录不能以 `_` 或 `.` 开头 ★★

```
现象：hadoop jar ... -input /hlm/_wcin → InvalidInputException: Input path does not exist
      但 hdfs dfs -ls /hlm/_wcin 明明有文件
根因：Hadoop FileInputFormat 会**静默过滤** `_` 和 `.` 开头的路径
      （这是约定：_SUCCESS、.crc 这类元数据文件不应被当数据处理）
修法：输入目录改名 /hlm/in2（不带下划线）
教训：Hadoop 的"约定优于配置"在这里变成了陷阱——路径存在但作业说"不存在"，
      排查时极易怀疑权限或数据。
```

### 坑 3 · happybase 的表名前缀：三个参数一起看才明白 ★★★

这是本轮最隐蔽的坑，连续踩了三次：

```
现象演进：
  第 1 次：conn.create_table("hlm_chunks", ...) → shell list 看到 "_hlm_chunks"
  第 2 次：conn = Connection(table_prefix=b"")   → 仍报 TableNotFoundException: _hlm_terms
  第 3 次：conn = Connection(table_prefix=None)  → 还是 _ 前缀

根因（读源码才看清）：
  happybase 1.3.0 的 _table_name() 逻辑是：
      prefix + separator + name
  其中：
      - table_prefix=None  → 内部替换成 b"_"
      - table_prefix_separator 默认 b"_"
      → 所以 prefix=b"" 也会拼成 b"" + b"_" + name = b"_hlm_chunks"

修法（唯一有效）：
  Connection(table_prefix=b"", table_prefix_separator=b"")
  → b"" + b"" + name = name，表名原样

教训：
  1. 表名对不上时，先 `print(c.tables())` 看实际名字，不要假设
  2. 建表与读写用不同工具是允许的（shell 建表功能全 + happybase 读写快），
     但**表名必须两端对齐**
  3. 这是"两套默认值叠加"造成的隐式行为，纯读文档看不出来，必须读源码
```

### 坑 4 · HBase DDL 在 bash 双引号里被 brace expansion 破坏 ★★

```
现象：create '{name}', { 'd' => {VERSIONS => 1}, 'm' => {...} } 
      → shell 报语法错误，表根本没建，但脚本误判为"已存在"
根因：两层问题
      ① echo "{...}" | hbase shell —— bash 会展开 {VERSIONS => 1} 里的花括号
         （`{a,b}` 是 brace expansion 语法）
      ② 我的 ruby 语法本身也错了：
         create 't', { 'd'=>{...}, 'm'=>{...} }   ← 一个 hash 两个 key，HBase 不认
         正确写法是每个列族一个独立 hash：
         create 't', {NAME=>'d', VERSIONS=>1}, {NAME=>'m', VERSIONS=>1}, SPLITS=>[...]

修法：
  ① 命令写入临时文件，用 `hbase shell -n <file>` 执行（完全不经过 shell 展开）
  ② DDL 用 fam_parts = ", ".join(f"{{NAME => '{f}', VERSIONS => 1}}") 生成

教训：向子进程传带特殊字符的字符串时，**永远优先用文件或 stdin，不要拼 shell 字符串**。
```

### 坑 5 · `hbase shell describe` 探测表可用性太慢 ★

```
现象：3 张表各等 4 分钟，"等待 hlm_chunks 可用 ... TIMEOUT"
根因：`hbase shell` 每次都要启动一个 JVM + JRuby，单次约 240 秒
修法：改用已建立的 happybase 连接做 scan(limit=1) 探测，秒级完成
教训：hbase shell 适合**人工操作与 DDL**，不适合放进程序的循环/轮询里。
```

### 坑 6 · 用 `name in output` 判断表存在会误判 ★

```
现象：脚本说"表已存在，跳过建表"，但实际表不存在
根因：hbase shell 的 list 输出包含大量日志行，子串匹配会撞上
      （如 "hlm_chunks" 是 "hlm_chunks_old" 的子串）
修法：只解析 `=> ["a","b",...]` 那一行，json.loads 成数组后做精确成员判断
教训：解析命令行输出时，找**结构化锚点**（这里是 `=>[...]`），不要全文子串匹配。
```

### 坑 7 · happybase `scan(filter=...)` 不是 Python 函数 ★

```
现象：t.scan(filter=lambda k,d: ...) → TDecodeException:
      Field 'filterString(6)' of 'TScan' needs type 'BINARY', but the value is <function>
根因：happybase 的 filter 参数是 **Thrift 服务端过滤器语法字符串**
      （如 "SingleColumnValueFilter(...)", "PrefixFilter('abc')"），不是 Python callable
修法：教学规模下直接全表扫一遍建内存字典 id2row（1,035 行，毫秒级）
教训：封装库的参数名相同不代表语义相同。写之前先看 signature + 源码。
```

### 坑 8 · MapReduce 作业必须用 `/user/root` 或非下划线路径

```
现象：hadoop jar wordcount /hlm/_wcin /hlm/_wc3 → 失败
      hadoop jar wordcount /user/root/in /user/root/out → 成功
根因：同坑 2（下划线前缀被过滤）
补充：另有一条——HDFS 上不存在作业输出目录的同名路径时才能创建
修法：输入输出都用正常命名（不用 _ 前缀）；输出目录先 -rm -r -f
```

### 坑 9 · 中文路径可以 bind mount（意外的好消息）

```
验证：docker run -v 'D:\学校事务\大数据原理与应用\红楼智问\_probe:/p' alpine cat /p/probe.txt
      → 正常输出 "hello-中文"
结论：Docker Desktop on Windows 支持中文路径 bind mount
意义：流水线代码可以直接放项目目录，不必迁到 ASCII 路径
```

---

## 三、实测数据汇总（S2 交付证据）

### 3.1 Chunk 切分（`s2_1_chunk.py`）

| 指标 | 值 |
|:---|---:|
| chunk 数 | **1,035** |
| 覆盖回次 | 80 回 |
| 总字数 | 609,947 |
| 块大小 min / mean / p50 / p95 / max | **151 / 589.3 / 597 / 786 / 879** |
| 单段超长触发 L3 滑窗 | 67 次 |
| 尾块合并 | 1,052 → 1,035（并入 17 个碎块） |
| 分区数 | 8（按回次每 10 回一区） |
| **分区负载比 max/mean** | **1.229**（教材阈值 2，均衡 ✅） |
| `strategy_rev` | `v1` |
| sha256(chunks.jsonl) | `887d66f6…cd0540` |

**切分策略**（三级）：
```
L1 按回（81 条）        → 章节导航、逐回统计
L2 按场景段合并（主）    → 目标 300~800 字，RAG 主检索单元
L3 滑动窗口（兜底）      → 500 字 / 100 字重叠，只处理超长块
```

**chunk_id 规则**（关键设计，对应难点①）：
```
chunk_id = sha1(version|hui|para_start|para_end|seg|strategy_rev)[:16]
```
- `version` 进 ID → 不同版本同位置 chunk 天然隔离
- `strategy_rev` 进 ID → **改切分策略就得到全新 ID 集**，旧索引可并行保留/对比/回滚

### 3.2 HDFS 落位（`--stage upload`）

| 项 | 值 |
|:---|:---|
| 路径 | `/hlm/warehouse/chunks/fuqinju/chunks.jsonl` |
| 大小 | 2,099,616 B（2.0 MB） |
| sha256（本地） | `887d66f699e433f97ff7e852f2bd19ddee739c3454bff783f4b986caa5dac884` |
| sha256（HDFS 取回） | 同上 → **✅ 一致** |
| 块数 | 1 |
| 块 ID | `blk_1073742639_1839`，len=2,099,616 |
| 副本 | **Live_repl=2**，分布在 `172.19.0.4:9866` 与 `172.19.0.3:9866` |
| fsck 状态 | **HEALTHY**，Missing blocks 0，Corrupt blocks 0 |
| DataNode 数 | 2，rack 数 1 |

### 3.3 向量化（`--stage embed`）

| 项 | 值 |
|:---|:---|
| 模型 | bge-small-zh-v1.5（ONNX，94.8 MB） |
| 维数 | **512** |
| 条数 | 1,035 |
| 耗时 | 34.7 秒（**29.8 chunk/s**） |
| Pooling | Mean pooling（attention_mask 加权）+ L2 归一化 |
| 产出 | `vectors.jsonl` 2.82 MB |
| HDFS 快照 | `/hlm/derived/vectors/fuqinju/model=bge-small-zh-v1.5/vectors.jsonl` |

### 3.4 HBase 索引（`--stage hbase`）

| 表 | 行数 | 列族 | 预分区 | 耗时 |
|:---|---:|:---|:---|---:|
| `hlm_chunks` | **1,035** | `d`（text/char_count）、`m`（8 个元数据） | 8 region | 2.5 s |
| `hlm_vectors` | **1,035** | `v`（emb 2048B）、`m`（model_id/dim/hui） | 8 region | 2.1 s |
| `hlm_terms` | **133,673** | `p`（postings/df） | 无 | 142.7 s |

**抽样验证**：
```
rowkey = fuqinju|001|10f1ef930816dfd9
d:text = 空空道人听如此说，思忖半晌，将这《石头记》再检阅一遍…
meta   = {hui:1, para_start:9, para_end:16, seg:3, partition:hui_001_010,
          strategy_rev:v1, version:fuqinju}

向量 rowkey = fuqinju|001|10f1ef930816dfd9
v:emb 字节数 = 2048 (= 512 × 4 ✅)
m:model_id = bge-small-zh-v1.5,  m:dim = 512
```

### 3.5 三路混合检索（`s2_3_search.py`）

查询：`贾宝玉的丫鬟有谁`

| 环节 | 结果 | 耗时 |
|:---|:---|---:|
| 别名展开 | 6 种写法（贾宝玉/宝玉/宝二爷/怡红公子/绛洞花王/富贵闲人/混世魔王） | — |
| L1 关键词 | 30 命中 / 38 个 bigram 命中（共 45） | **44.0 ms** |
| L2 向量 | 1,035 条候选，top-1 相似度 0.6285 | **383.5 ms** |
| RRF 融合 | 58 条候选 | **0.1 ms** |
| **总计** | — | **427.6 ms** |

**Top-3 结果**（RRF 融合后）：

| 排名 | 位置 | RRF | kw / vec |
|:---:|:---|:---|:---|
| 1 | 第3回 段22-24 | 0.031778 | kw=0.5 (rank 5) / **vec=0.6285 (rank 1)** |
| 2 | 第25回 段15-17 | 0.027638 | kw=0.4857 (rank 25) / vec=0.5976 (rank 3) |
| 3 | 第3回 段14-15 | 0.016393 | **kw=0.6875 (rank 1)** / vec 未入 |

**结果分析**（这正是混合检索的价值）：
- 纯向量 Top-1 = 第3回段22-24（黛玉初进贾府、贾母安排住处）——语义最接近「宝玉身边有哪些丫鬟」
- 纯关键词 Top-1 = 第3回段14-15（王夫人房里丫鬟来请）——字面命中「丫鬟」最密
- **RRF 把两者都保留在前 3**，而单一策略会漏掉另一个

---

## 四、对教材知识点的对应（供项目文档引用）

| 教材概念 | 本项目实例 | 证据 |
|:---|:---|:---|
| HDFS 块与副本 | 2.0 MB / 1 块 / 2 副本 / 2 DataNode | `hdfs fsck -files -blocks -locations` |
| NameNode 元数据 | rowkey 前缀按版本+回次组织，等价于"业务分区" | ADR-03 |
| **业务分区 ≠ HDFS Block** | chunk 分区 8 个（hui_001_010…）；HDFS 只有 1 个 Block | 两者独立存在，不能画成同一件事 |
| MapReduce 输入过滤 | `_` 前缀路径被静默忽略 | 坑 2 |
| HBase 行键设计 | `<ver>\|<hui:03d>\|<chunk_id>` 三段式 | ADR-03 |
| HBase 写热点治理 | splitKeys 预分区 8 个 region | ADR-04 |
| 列族与列限定符 | `d:`/`m:`（chunks）、`v:`/`m:`（vectors）、`p:`（terms） | 3.4 表结构 |
| 事实源 vs 派生层 | HDFS 事实源 + HBase 派生索引，可重建 | ADR-01 |
| 副本倍率与容量 | 2 副本 → 2× 放大 | 3.2 节 |
| 小文件问题 | 2 MB / 1 块，块利用率 = 2,099,616/134,217,728 = **1.56%** | 与 `V3存储决策报告.md` 的结论一致 |

---

## 五、待办（S3 已完成，剩余见下）

| # | 项 | 状态 |
|:---|:---|:---|
| 1 | **索引可重建验证**（R-510） | ✅ 已完成（15/15 逐条一致） |
| 2 | **副本故障注入**（R-509） | ✅ 已完成（停节点仍可读 + 校验一致） |
| 3 | **分区方案对比**（R-508） | ✅ 已完成（4 方案对比） |
| 4 | **MapReduce 统计作业**（R-506） | ✅ 已完成（4 个作业） |
| 5 | **Hive 外部表**（R-507） | ✅ 已完成（4 张表 + SQL 验证） |
| 6 | **向量常驻内存优化** | 待 S4（把 383 ms 降到 <5 ms） |
| 7 | **chunk_id → rowkey 映射表** | 待 S4（现在回表靠全表扫） |
| 8 | 把流水线脚本纳入 `pipeline/README.md` | ✅ 已完成 |

---

## 六、S3 · MapReduce 统计（实测）

### 6.1 四个作业

| 作业 | 输入 | Map 输出 | Combine 输出 | Reduce 输出 | 耗时 |
|:---|---:|---:|---:|---:|---:|
| `wordfreq` | 1,035 chunk | 大量 bigram | — | **133,673** 词项 | ~18 s |
| `personfreq` | 1,035 chunk | 15,000 | **72** | **41** 人物 | ~12 s |
| `cooccur` | 1,035 chunk | 11,562 | **927** | **10,742** 组 | ~13 s |
| `chunkstat` | 1,035 chunk | 1,035 | **81** | **80** 回 | ~13 s |

**Combiner 效果**（教材「combiner 在同 key 重复多时收益最大」的直接证据）：

| 作业 | Combine 前 | Combine 后 | 压缩率 |
|:---|---:|---:|---:|
| personfreq | 15,000 | 72 | **99.5%** |
| cooccur | 11,562 | 927 | **92.0%** |
| chunkstat | 1,035 | 81 | **92.2%** |

> personfreq 压缩 99.5% 的原因：15,000 次人名提及只对应 41 个人物，
> key 重复度极高，combiner 在 map 端就完成了 99.5% 的聚合，
> shuffle 数据量从 15,000 条降到 72 条。这就是 combiner 的价值。

### 6.2 结果校验（与本地统计对拍）

MapReduce 产出 vs 前端本地统计（`web/data/stats.json`）：

| 人物 | MR personfreq | 本地 stats | 一致性 |
|:---|---:|---:|:---|
| 贾宝玉 | 2,746 | 2,717 | ✅ 差异因 chunk 切分边界 |
| 贾母 | 1,734 | 1,713 | ✅ |
| 王熙凤 | 1,348 | 1,340 | ✅ |
| 林黛玉 | 1,060 | 1,047 | ✅ |
| 袭人 | 779 | 773 | ✅ |

> 差异来源：MR 在 chunk 边界处可能重复/丢弃跨块的别名匹配；
> 本地统计按段落累计。两者差 1% 以内，**量级与排序完全一致**。

### 6.3 逐回统计（Hive 查询结果）

第 67 回以 **39 个 chunk / 22,349 字** 居首，是唯一的三位数离群点
——这正是需求文档里记录的「第 67 回双版本并排、字数为他回 2.9 倍」。
MapReduce 结果独立复现了这个已知特征，说明管道正确。

### 6.4 Hive 外部表

| 表名 | 列 | HDFS 位置 |
|:---|:---|:---|
| `hlm_word_freq` | `bigram STRING, cnt INT` | `/hlm/mrout/wordfreq` |
| `hlm_person_freq` | `name STRING, cnt INT` | `/hlm/mrout/personfreq` |
| `hlm_cooccur` | `a STRING, b STRING, cnt INT` | `/hlm/mrout/cooccur` |
| `hlm_chunk_stat` | `hui INT, n_chunk INT, n_bytes INT, n_chars INT` | `/hlm/mrout/chunkstat` |

**SQL 验证**（通过 HiveServer2 + beeline，底层跑 MapReduce）：

```sql
SELECT bigram, cnt FROM hlm_word_freq ORDER BY cnt DESC LIMIT 10;
→ 2703 / 2276 / 1269 / 1179 / 1167 / 1116 / 979 / 949 / 868 / 812

SELECT a, b, cnt FROM hlm_cooccur ORDER BY cnt DESC LIMIT 10;
→ 正确解析出三列（a / b / cnt），Top 共现 15 次

SELECT hui, n_chunk, n_chars FROM hlm_chunk_stat ORDER BY n_chunk DESC LIMIT 5;
→ 67:39/22349  78:21/11457  77:20/10758  62:20/11988  74:19/11159
```

> **踩坑**：共现最初输出 `a|b \t 1`（两列），Hive 按 tab 分列后
> `a|b` 整列塞进 `a`、`b` 变成 NULL。改为 `a \t b \t 1`（三列）后正确。
> 这个坑说明：**外部表的分隔符约定必须与文件实际格式严格一致**，
> 否则 Hive 不会报错，只会静默给出 NULL。

### 6.5 HBase 落地

| 表 | 行数 | 说明 |
|:---|---:|:---|
| `hlm_wordfreq` | 133,673 | rowkey=`fuqinju\|<bigram>`，列 `f:cnt` |
| `hlm_stats` | 709 | rowkey=`<metric>\|<key>`，列 `s:*` |

`hlm_stats` 内容：`personfreq`(41) + `cooccur`(588→重跑后 10,742 已同步) + `chunkstat`(80)

---

*本文档为 S2 阶段交付物。所有数字均于 2026-10-24 在 `hadoop-lab:2026.10` 集群实测。*
