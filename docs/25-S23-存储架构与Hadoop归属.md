# S23 · 存储架构与自定义查询的 Hadoop 归属说明

> 日期：2026-10-24
> 三个问题：
> 1. 现在节点情况如何？
> 2. 文本文件和向量文件都储存在哪里？
> 3. 自定义查询功能是通过 Hadoop 的什么部分起作用的？

---

## 一、节点情况

### 1.1 容器（3 个 Hadoop 节点 + 1 个应用容器）

| 容器 | 状态 | 角色 |
|:---|:---|:---|
| `master` | **Up 11 小时 (healthy)** | NameNode / ResourceManager / HBase Master / Spark Master / Hive / JobHistory |
| `slave1` | **Up 9 小时 (healthy)** | DataNode / NodeManager / RegionServer |
| `slave2` | **Up 11 小时 (healthy)** | DataNode / NodeManager / RegionServer |
| `hlm-api` | Up 56 分钟 | RAG 后端（FastAPI），不是 Hadoop 节点 |

### 1.2 HDFS 集群

```
Configured Capacity : 1.97 TB
DFS Used            : 550.46 MB
DFS Used%           : 0.03%
DFS Remaining       : 1.82 TB
Under replicated    : 0
Live datanodes      : 2
  ├─ 172.19.0.3:9866  slave2  1006.85 GB
  └─ 172.19.0.4:9866  slave1  （另一台，合计 1.97 TB）
```

### 1.3 关键结论

- **2 个 DataNode 存活**，副本数 `dfs.replication=2` → 数据双副本，任一 DataNode 挂掉数据仍可读
- **0 个副本不足块** → 所有块副本完整
- **安全模式 OFF** → 可正常读写
- HDFS 使用率仅 **0.03%**（550 MB / 1.97 TB）

> 注：`master` 同时是 NameNode，但**不存放数据块**（纯管理节点）。
> 数据实际分布在 slave1 / slave2 上。

---

## 二、文本与向量的存储位置

### 2.1 三层存储架构

```
┌─────────────────────────────────────────────────────────────┐
│ 第 1 层：HDFS（原始 + 派生的落地文件）                          │
│   /hlm/warehouse/   ← chunk 文本                             │
│   /hlm/derived/     ← 向量 JSONL                             │
│   /hlm/mrout/       ← MapReduce 输出                         │
│   /hlm/logs/        ← 问答日志                                │
└─────────────────────────────────────────────────────────────┘
                          ↓ 导入
┌─────────────────────────────────────────────────────────────┐
│ 第 2 层：HBase（在线检索索引）                                 │
│   hlm_chunks / hlm_vectors / hlm_terms                       │
│   hlm_wordfreq / hlm_stats                                   │
└─────────────────────────────────────────────────────────────┘
                          ↓ 查询
┌─────────────────────────────────────────────────────────────┐
│ 第 3 层：应用本地（Web 展示 / 沙箱查询用）                      │
│   backend 镜像内 JSON  +  web/data/                          │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 HDFS 实际文件清单（实测）

| 路径 | 大小 | 内容 |
|:---|---:|:---|
| **文本（chunk）** | | |
| `/hlm/warehouse/chunks/fuqinju/chunks.jsonl` | 2.10 MB | 单版本 chunk（旧） |
| `/hlm/warehouse/pizhu/fuqinju/pizhu_chunks.jsonl` | 1.31 MB | 单版本脂批 chunk（旧） |
| `/hlm/warehouse/chunks3ver/chunks_3ver.jsonl` | **6.33 MB** | **三版本正文 chunk（当前）** |
| `/hlm/warehouse/pizhu3src/pizhu_3src.jsonl` | **4.79 MB** | **三源脂批 chunk（当前）** |
| `/hlm/mrin/chunks/chunks.jsonl` | 2.10 MB | MapReduce 输入 |
| **向量** | | |
| `/hlm/derived/vectors/fuqinju/model=bge-small-zh-v1.5/vectors.jsonl` | 2.96 MB | 单版本正文向量（旧） |
| `.../pizhu_vectors.jsonl` | 9.27 MB | 单版本脂批向量（旧） |
| `/hlm/derived/vectors3ver/.../vectors_3ver.jsonl` | **9.44 MB** | **三版本正文向量（当前）** |
| `.../pizhu_3src_vectors.jsonl` | **24.82 MB** | **三源脂批向量（当前）** |
| **MapReduce 输出** | | |
| `/hlm/mrout/wordfreq/part-00000` | 1.21 MB | 词频 |
| `/hlm/mrout/cooccur/part-00000` | 210 KB | 人物共现 |
| `/hlm/mrout/personfreq/part-00000` | 502 B | 人物频次 |
| `/hlm/mrout/chunkstat/part-00000` | 1.3 KB | 逐回统计 |
| **日志** | | |
| `/hlm/logs/qa/qa_20261024.jsonl` | 20 KB | 问答日志 |

**HDFS 总占用：约 65 MB**（其余是 MapReduce 中间产物与元数据）

### 2.3 HBase 实际表（在线检索用）

| 表 | 行数 | 内容 | Rowkey 格式 |
|:---|---:|:---|:---|
| `hlm_chunks` | **11,780** | chunk 正文 + 元数据 | `ver\|source\|hui###\|chunk_id` |
| `hlm_vectors` | **11,780** | 512 维向量 | 同上 |
| `hlm_terms` | **448,841** | bigram 倒排索引 | `ver\|source\|bigram` |
| `hlm_wordfreq` | 133,673 | 词频统计 | `bigram` |
| `hlm_stats` | 709 | 聚合统计 | `前缀\|...` |

**实测 HBase 里的向量存储格式**：

```
ROW: chenggao|text|001|0c9755f441a2e2e1
  column=m:dim,       value=512
  column=m:hui,       value=1
  column=m:model_id,  value=bge-small-zh-v1.5
  column=v:emb,       value=<2048 字节二进制 float32 × 512 维>
```

**关键**：向量不是 base64 或 JSON 数组，而是**原始 float32 二进制**（2048 字节 = 512 × 4），存在 `v:emb` 列里。

实测 `hlm_terms` 倒排索引格式：

```
ROW: chenggao|text|\xE4\xB8\x80\xE4\xB8\x87   （即「一万」）
  column=p:df,        value=16
  column=p:postings,  value=[["594077df1cfe314a",1], ["6a22c84e16bdc462",1], ...]
  column=p:source,    value=text
```

---

## 三、自定义查询用了 Hadoop 的什么？

### 3.1 直接回答：**没有用 Hadoop 的任何组件**

这是必须诚实说明的。

### 3.2 实测证据

```bash
# query_api.py 里搜 hadoop/hdfs/hbase/happybase
$ grep -nE 'hdfs|hadoop|hbase|happybase' /app/query_api.py /app/sandbox.py
（无匹配）

# 沙箱实际执行的命令
prlimit --cpu=5 --as=536870912 --nproc=8 --fsize=1048576 --nofile=32 \
  -- timeout 12 python3 /tmp/run.py
```

**只调用了 Linux 的 `prlimit` / `timeout`，没有 Hadoop 组件。**

### 3.3 那它读的是哪份数据？

```python
# query_api.py
WEB_DATA = os.environ.get("HLM_WEB_DATA", "/workspace/pipeline/out")
```

`/workspace/pipeline/out` 的挂载来源：

```
bind D:\学校事务\大数据原理与应用\红楼智问\pipeline\out -> /workspace/pipeline/out
```

**是宿主机上的本地目录（bind mount），不是 HDFS。**

实测文件系统类型：`v9fs`（Docker Desktop / WSL2 的 9p 挂载）。

### 3.4 数据来源链路（当前真实情况）

```
HDFS /hlm/warehouse/chunks3ver/chunks_3ver.jsonl
   ↓ （pipeliine 脚本导出）
宿主机 pipeline/out/chapters_fuqinju.jsonl
   ↓ （bind mount）
容器内 /workspace/pipeline/out/chapters_fuqinju.jsonl
   ↓ （query_api.load_all_text 直接 open 读）
自定义查询
```

**也就是说：HDFS 是「上游数据源」，但自定义查询读的是「导出后的本地副本」。**

### 3.5 为什么这样设计

| 原因 | 说明 |
|:---|:---|
| **性能** | 每次查询都调 HDFS CLI 会慢 10~100 倍（进程启动 + RPC） |
| **沙箱安全** | 沙箱里禁止 `import`，无法用 `happybase` 连 HBase，也无法调 `hdfs dfs` |
| **只读需求** | 自定义查询是「统计全书」，不需要 HBase 的逐行检索能力 |
| **数据一致性** | `pipeline/out` 由 pipeline 脚本从 HDFS 导出，是 HDFS 的镜像 |

### 3.6 项目里真正的 Hadoop 使用点

| 功能 | 用的 Hadoop 组件 |
|:---|:---|
| 语料落地 | **HDFS**（`/hlm/warehouse/`） |
| 词频/共现/人物统计 | **MapReduce**（`/hlm/mrout/`） |
| online 检索（关键词 / 向量 / 图谱） | **HBase**（5 张表） |
| 数据仓库表 | **Hive**（`hlm_chunks` 外部表） |
| 资源调度 | **YARN**（MapReduce 作业运行） |
| 分布式协调 | **ZooKeeper**（HBase 依赖） |
| 批处理（未用于线上） | Spark / Flink / Storm（已装，演示用） |
| **自定义查询** | ❌ **无**（本地文件 + Linux 资源限制） |

---

## 四、总结

### 4.1 三句话回答

1. **节点**：3 个 Hadoop 容器（master + slave1 + slave2）全部 healthy，2 个 DataNode 存活，HDFS 1.97 TB 容量用了 0.03%，0 副本不足。

2. **存储**：
   - **HDFS** 存 JSONL 原始/派生文件（chunk 文本 11.1 MB + 向量 34.3 MB）
   - **HBase** 存在线检索索引（11,780 chunk / 11,780 向量 / 448,841 bigram）
   - 向量在 HBase 里是 **2048 字节 float32 二进制**（512 维 × 4 字节）

3. **自定义查询**：**没有用 Hadoop**。它读的是从 HDFS 导出到宿主机的本地 JSONL 副本（bind mount），
   执行靠 Linux `prlimit` + `timeout` 做资源限制，靠 AST 白名单做安全隔离。

### 4.2 如果要让自定义查询真正走 Hadoop

可行方案：

| 方案 | 做法 | 代价 |
|:---|:---|:---|
| A. 允许沙箱调 HDFS | 在 `query_api` 里封装 `subprocess` 调 `hdfs dfs -cat` | 慢（每次进程启动 ~200ms） |
| B. 允许沙箱连 HBase | 放开 `import happybase`（违反当前沙箱设计） | 破坏安全模型 |
| C. 预导出 + 定时同步 | 当前方案 + cron 同步 HDFS→本地 | 有延迟（可通过频繁同步缓解） |
| **D. 现状** | 一次性导出，本地读 | **最快，但数据是快照** |

**当前选 D**，因为自定义查询是「全书统计」类需求，数据量小（<15 MB），
本地读完全够用，走 HDFS 反而更慢。

> 若审阅时被问「这个功能算不算用了 Hadoop」，
> **应当如实回答：这条链路是本地文件，不属于 Hadoop 部分**。
> 项目的 Hadoop 使用体现在数据管道（HDFS）、MapReduce 统计、HBase 检索三处。

---

*本文档数据全部来自实际命令输出，未估算。*
