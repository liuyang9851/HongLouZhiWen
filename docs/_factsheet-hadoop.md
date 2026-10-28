# 红楼智问 · Hadoop 侧事实清单

> 用途：撰写课程项目技术文档的取材底稿。
> 规则：只收录文档中真实写出的内容，每条数字后括号注明出处文件。文档间互相矛盾处单独标注。
> 生成日期：2026-10-28

## 来源文件代号

| 代号 | 文件 |
|:---|:---|
| **03** | `docs/03-S2数据管道与Hadoop落地.md` |
| **25** | `docs/25-S23-存储架构与Hadoop归属.md` |
| **29** | `docs/29-S26-建库流程追踪.md` |
| **40** | `docs/40-S39-Hadoop版本兼容改造.md` |
| **41** | `docs/41-S40-Docker单节点HDFS方案.md` |
| **44** | `docs/44-S40-Spark与MapReduce实测对比.md` |
| **词频** | `Hadoop词频分析报告.md`（工作目录根） |
| **V3B** | `V3实验B-HDFS报告.md`（工作目录根） |
| **V3A** | `V3分区实验报告.md`（工作目录根） |
| **01** | `docs/01-需求分析说明书.md` |
| **06** | `docs/06-项目审阅文档.md` |
| **07** | `docs/07-课堂演示文档.md` |
| **41-部署** | `docs/38-部署指南.md` |
| **hadoop/** | `红楼智问/hadoop/` 下 `core-site.xml`、`hdfs-site.xml`、`hadoop-env.sh`、`Dockerfile.hdfs`、`entrypoint.sh` |

---

## 1. 集群与版本

### 1.1 环境分为两套，不要混为一谈

| 环境 | 时间 | 形态 | 出处 |
|:---|:---|:---|:---|
| **V3 实验集群** | 2026-09-27 | WSL2 Ubuntu 24.04 + Docker，1×NameNode + **3×DataNode**（slave1/2/3），副本数 3 | V3B |
| **红楼智问主集群**（`hadoop-lab`） | 2026-10-24 起 | Docker Compose，**3 容器** master + slave1 + slave2，副本数 **2** | 01、06、25 |

> 两套环境的 DataNode 数与副本数不同（3 副本 vs 2 副本），写文档时必须指明是哪一套。

### 1.2 主集群（hadoop-lab）版本

| 组件 | 版本 | 出处 |
|:---|:---|:---|
| Hadoop | **3.3.6**（HDFS + YARN + MapReduce） | 01、06、07 |
| HBase | **2.5.10-hadoop3** | 01、06 |
| Hive | **3.1.3** | 01、06、07 |
| ZooKeeper | **3.8.4**（**单机模式**，`zoo.cfg` 无 `server.N`） | 01、06 |
| Spark | **3.5.8**（Standalone，master + 2 worker） | 01、06 |
| Flink | 1.20.3（按需启动） | 01 |
| Storm | 1.2.4（按需安装） | 01 |
| Java | 11（默认）/ 8（Hive MR 专用，否则 `NoSuchFieldException: parentOffset`） | 01、06 |
| Python（容器内） | 3.10.12（numpy 2.2.6 / pandas 2.3.3 / happybase 1.3.0 / pyspark 3.5.8） | 01 |
| Spark 附注 | Scala 2.12.18 / OpenJDK 11 / `spark-submit` 在 `/opt/spark/bin/` | 44 |

### 1.3 节点与角色

**主集群 3 个 Hadoop 容器 + 1 个应用容器**（25）：

| 容器 | 角色 |
|:---|:---|
| `master` | NameNode / ResourceManager / HBase Master / Spark Master / Hive / JobHistory |
| `slave1` | DataNode / NodeManager / RegionServer |
| `slave2` | DataNode / NodeManager / RegionServer |
| `hlm-api` | RAG 后端（FastAPI），**不是 Hadoop 节点** |

- **master 不存放数据块**，纯管理节点；数据分布在 slave1 / slave2（25）。
- master IP `172.19.0.2`（01）；容器状态 3 个均 `Up (healthy)`（25、01）。
- Docker 网络 `hadoop-lab_cluster`（bridge）（01、25）。
- 编排目录 `D:\Programming\hadoop\cluster`，`compose.yaml`，project name `hadoop-lab`（01）。
- 镜像 **`hadoop-lab:2026.10`**，单一镜像 3 节点共用，约 **6.89 GB**（01、41）。
- 数据持久化：命名卷 `hadoop-lab_{master,slave1,slave2}-{data,logs}` + 绑定挂载 `./workspace:/workspace`（01）。

### 1.4 集群实测参数

| 项 | 值 | 出处 |
|:---|:---|:---|
| `fs.defaultFS` | `hdfs://master:9000` | 01、06 |
| Live DataNode | **2**（slave1、slave2） | 01、06、25 |
| `dfs.replication` | **2** | 01、06 |
| `dfs.blocksize` | **134,217,728 B（128 MB）** | 01、06、hadoop/hdfs-site.xml |
| YARN NodeManager | **2**（slave1:40183、slave2:43755，RUNNING） | 01 |
| HBase 状态 | 1 active master / 2 RegionServer / 0 dead | 01 |
| HBase Thrift | **9090 已启动**，happybase 可连 | 01 |
| Safe mode | **OFF** | 01、06 |
| HDFS 容量 | **1.97 TB** 配置 / 1.82 TB Remaining / **550.46 MB** 已用（0.03%） | 25 |
| HDFS 容量（另一处） | **2.0 TB**，已用 **366.3 MB**（0%） | 06 |
| HDFS 根目录 | `/hbase` `/hlm` `/spark-events` `/tmp` `/user` | 01 |

### 1.5 端口重映射（Windows Hyper-V 保留 8064–8163、7020–7119）

YARN UI **18088**（非 8088）· Spark Master UI **18080**（非 8080）· Spark RPC 17077 · Flink REST 18082 · Storm UI 18085 · ZK 12181 · HDFS UI 9870 · JobHistory 19888 · HBase Master UI 16010 · HDFS RPC 9000 · HiveServer2 10000（01、06）。容器内端口不变，仅宿主侧映射（01）。

### 1.6 单节点 HDFS 方案（41，给只有 Docker 的同学）

- 镜像 `hlm-hdfs`：**2.54 GB**，对比完整镜像 6.89 GB，**减少 63%**（41、41-部署）。
- 基础镜像 `eclipse-temurin:11-jre-jammy`，Hadoop tar.gz **730 MB**（41、hadoop/Dockerfile.hdfs）。
- 单容器同时跑 NameNode + DataNode（伪分布式）（41、hadoop/entrypoint.sh）。
- 关键参数：`dfs.replication=1`、`dfs.namenode.http-address=0.0.0.0:9870`、`dfs.permissions.enabled=false`、`dfs.blocksize=134217728`、`hadoop.tmp.dir=/data/tmp`（41、hadoop/hdfs-site.xml、core-site.xml）。
- 堆设置：`HDFS_NAMENODE_OPTS=-Xmx512m -Xms256m`、`HDFS_DATANODE_OPTS=-Xmx384m -Xms192m`（hadoop/hadoop-env.sh）。
- 宿主映射 **19870:9870 / 19000:9000**（因开发机 9870/9000 已被真实集群占用）（41）。
- 启动耗时：**首次 29 秒**（含格式化 + 导入 43 MB 数据）/ **二次约 12 秒**（41）。
- 资源占用：HDFS 镜像 2.54 GB + 容器内存约 700 MB；后端镜像 665 MB + 容器内存约 1.2 GB；数据 volume 约 43 MB；**合计内存约 2 GB**（41）。
- 完整镜像体积构成：`/opt/hadoop` 1.4 GB、`/opt/hbase` 405 MB、`/opt/spark` 429 MB、`/opt/hive` 356 MB、`/opt/flink` 504 MB、`/opt/zookeeper` 44 MB，可砍约 4.3 GB（41）。

### 1.7 版本兼容性结论（40、41-部署）

| 组件 | 版本敏感度 | 结论 |
|:---|:---|:---|
| HDFS（文件 + WebHDFS） | 低 | 文件格式与 REST API 从 1.x 到 3.x 稳定 |
| **HBase（Thrift1）** | **极高** | 3.x 已移除 Thrift1，`happybase` 完全不可用；2.x 需手动启动 Thrift |
| MapReduce | 低 | 只消费输出文件，不调 API |
| Hive | 低 | 同上 |
| Spark / Flink / Storm | 无 | 项目未调用 |

- **只在 Hadoop 3.3.6 上验证过**；2.x 的 50070 端口探测逻辑写了但**未实测**（40、41-部署）。
- 数据源三层降级 `hdfs → local → hbase`，由 `backend/data_source.py` 实现（40）。
- 环境变量：`HLM_DATA_SOURCE`（hdfs/local/hbase/auto）、`HLM_WEBHDFS_URL`、`HLM_HDFS_BASE`（40）。
- 启动耗时：本地文件约 **8 秒**，HDFS（WebHDFS 流式读 43 MB）约 **55 秒**（40）。
- 三种数据源实测 chunk 均为 11,780，检索命中 5/5（40）。

---

## 2. HDFS

### 2.1 目录结构（`/hlm/` 下）

| 路径 | 用途 | 出处 |
|:---|:---|:---|
| `/hlm/warehouse/` | 事实源：chunk 正文 | 25、06 |
| `/hlm/derived/` | 派生：向量 JSONL 快照 | 25、06 |
| `/hlm/mrin/` | MapReduce 输入 | 25、06 |
| `/hlm/mrout/` | MapReduce 输出 | 25、06 |
| `/hlm/logs/` | 问答日志 | 25、06 |
| `/hlm/manifest/` | 分区清单 + sha256 | 06 |

单节点方案启动时自动创建 `/hlm/warehouse/chunks3ver`、`/hlm/warehouse/pizhu3src`、`/hlm/derived/vectors3ver/model=bge-small-zh-v1.5`、`/hlm/logs`（hadoop/entrypoint.sh）。

### 2.2 实际文件清单（25 的记法，单位为 MB/MiB 混用见 §7 说明）

| 路径 | 大小 | 状态 |
|:---|---:|:---|
| `/hlm/warehouse/chunks/fuqinju/chunks.jsonl` | 2.10 MB | 单版本正文（旧） |
| `/hlm/warehouse/pizhu/fuqinju/pizhu_chunks.jsonl` | 1.31 MB | 单版本脂批（旧） |
| `/hlm/warehouse/chunks3ver/chunks_3ver.jsonl` | **6.33 MB** | **三版本正文（当前）** |
| `/hlm/warehouse/pizhu3src/pizhu_3src.jsonl` | **4.79 MB** | **三源脂批（当前）** |
| `/hlm/mrin/chunks/chunks.jsonl` | 2.10 MB | MapReduce 输入 |
| `/hlm/derived/vectors/fuqinju/.../vectors.jsonl` | 2.96 MB | 单版本正文向量（旧） |
| `.../pizhu_vectors.jsonl` | 9.27 MB | 单版本脂批向量（旧） |
| `/hlm/derived/vectors3ver/.../vectors_3ver.jsonl` | **9.44 MB** | **三版本正文向量（当前）** |
| `.../pizhu_3src_vectors.jsonl` | **24.82 MB** | **三源脂批向量（当前）** |
| `/hlm/mrout/wordfreq/part-00000` | 1.21 MB | 词频 |
| `/hlm/mrout/cooccur/part-00000` | 210 KB | 人物共现 |
| `/hlm/mrout/personfreq/part-00000` | 502 B | 人物频次 |
| `/hlm/mrout/chunkstat/part-00000` | 1.3 KB | 逐回统计 |
| `/hlm/logs/qa/qa_20261024.jsonl` | 20 KB | 问答日志 |

- HDFS 总占用：**约 65 MB**（25）。
- 当前 4 个主文件合计：chunk 文本 **11.1 MB**（6.33+4.79）+ 向量 **34.3 MB**（9.44+24.82）（25）。
- 06 给出精确字节数：`chunks.jsonl` 2,099,616 B、`pizhu_chunks.jsonl` 1,310,943 B、`vectors.jsonl` 2,957,930 B、`pizhu_vectors.jsonl` 9,268,644 B、`wordfreq` 1,210,673 B、`personfreq` 502 B、`cooccur` 210,164 B、`chunkstat` 1,344 B（06）。

### 2.3 块与副本（单版本 chunks.jsonl，S2 实测）

| 项 | 值 | 出处 |
|:---|:---|:---|
| 文件大小 | 2,099,616 B（2.0 MB） | 03、06 |
| sha256 | `887d66f699e433f97ff7e852f2bd19ddee739c3454bff783f4b986caa5dac884`（本地与 HDFS 取回一致） | 03、06 |
| 块数 | **1** | 03、06 |
| 块 ID | `blk_1073742639_1839`，len=2,099,616 | 03、06 |
| 副本 | **Live_repl=2**，分布 `172.19.0.4:9866` 与 `172.19.0.3:9866` | 03、06 |
| fsck | **HEALTHY**，Missing 0，Corrupt 0，Under-replicated 0 | 03、06 |
| DataNode 数 / rack 数 | 2 / 1 | 03、06 |
| **块利用率** | **1.56%**（2,099,616 ÷ 134,217,728） | 03、06 |
| HDFS checksum | `MD5-of-0MD5-of-512CRC32C 000002000000000000000000889a5aef3dcb7cb2de2d5de2fc31a439` | 06 |

- 副本故障注入（R-509）：停 slave1 的 DataNode → Dead datanodes 1，`cat` 仍读出 1,035 行，checksum 与基线一致；重启后归位（03、06）。
- 索引可重建（R-510）：drop 3 张表 → 从 HDFS 重建 → 检索结果 **15/15 逐条一致**（03、06）。
- **「业务分区 ≠ HDFS Block」**：chunk 业务分区 8 个（hui_001_010…），HDFS 只有 1 个 Block，两者独立（03）。

### 2.4 副本/块大小对照实验（V3B，3 DataNode 集群）

| 上传方式 | Block 数 | 平均块大小 |
|:---|:---:|:---|
| 默认 128 MB 块 | **1** | 5,723,270 B |
| `-Ddfs.blocksize=1048576`（1 MB） | **6** | 5×1,048,576 B + 1×480,390 B |

- 34 个分区文件（5,723,270 B / 5.46 MB）→ 34 个 Block，avg 168,331 B，块利用率 **0.13%**，`Default replication factor: 3`，`Number of data-nodes: 3`，`Number of racks: 1`（V3B）。
- 尾块不补齐：480,390 B 为最后一块（V3B）。
- DataNode 负载：slave1 47 块 / 22.52 MB，slave2 47 / 22.52 MB，slave3 47 / 22.52 MB，合计 **141 副本块 / 67.55 MB**，**47/47/47 完全均衡**（V3B）。
- 三层放大实测：原始 1.62 MB（3,234 条）→ 实验A 落地 5.72 MB（11,140 条，2 副本）= 3.53× → 上传 HDFS（每块 3 副本）**17.17 MB = 10.6×**（V3B）。

---

## 3. MapReduce

### 3.1 红楼智问的 4 个作业（Hadoop Streaming + Python，输入均为 1,035 chunk）

| 作业 | Map 输入 | Map 输出 | Combine 输出 | Reduce 输出 | 耗时 | 出处 |
|:---|---:|---:|---:|---:|---:|:---|
| `wordfreq` | 1,035 chunk | 大量 bigram | — | **133,673** 词项 | ~18 s | 03 |
| `personfreq` | 1,035 chunk | 15,000 | **72** | **41** 人物 | ~12 s | 03 |
| `cooccur` | 1,035 chunk | 11,562 | **927** | **10,742** 组 | ~13 s | 03 |
| `chunkstat` | 1,035 chunk | 1,035 | **81** | **80** 回 | ~13 s | 03 |

- Mapper / Reducer 脚本：`job1_mapper.py`/`job1_reducer.py` … `job4_mapper.py`/`job4_reducer.py`（29）。
- 真实 Job ID：`job_1792812494375_0021`（wordfreq）/`_0022`（personfreq）/`_0023`（cooccur）/`_0024`（chunkstat），全部 `completed successfully`（06）。
- 输入路径 `/hlm/mrin/chunks/chunks.jsonl`，输出 `/hlm/mrout/{wordfreq,personfreq,cooccur,chunkstat}`（03、06、25）。

**Combiner 效果**（03、06）：

| 作业 | Combine 前 | Combine 后 | 压缩率 |
|:---|---:|---:|---:|
| personfreq | 15,000 | 72 | **99.5%** |
| cooccur | 11,562 | 927 | **92.0%** |
| chunkstat | 1,035 | 81 | **92.2%** |

**结果校验**（MR vs 本地 `web/data/stats.json`，03、06）：

| 人物 | MR personfreq | 本地统计 | 差异 |
|:---|---:|---:|:---|
| 贾宝玉 | 2,746 | 2,717 | +1.1% |
| 贾母 | 1,734 | 1,713 | +1.2% |
| 王熙凤 | 1,348 | 1,340 | +0.6% |
| 林黛玉 | 1,060 | 1,047 | +1.2% |
| 袭人 | 779 | 773 | +0.8% |

### 3.2 wordfreq 的 shuffle 计数器（44，与 03 同一作业）

```
Map input records   = 1,035
Map output records  = 489,668
Combine input       = 489,668
Combine output      = 164,866   ← combiner 压掉 66%
Reduce input groups = 133,673
Reduce output       = 133,673
```

### 3.3 独立的中文词频作业（词频报告，2026-09-27，jieba 分词，非 bigram）

- 输入：抚琴居校本前 80 回正文，**80 个文件 / 1,812,222 字节 / 5,081 非空行**（词频）。
- 输出：**34,214 个不同词 / 141,985 词次**（词频）。
- **作业1** wordcount：`-D mapreduce.job.reduces=3`，**80 map / 3 reduce**，耗时 **1 分 33 秒**；`Data-local map tasks = 80`（100% 本地性）（词频）。
- **作业2** top-N：`-D mapreduce.job.reduces=1`，**3 map / 1 reduce**，耗时 **17 秒**（词频）。
- Combiner：Map output 141,985 → Combine output **89,689**，减少 52,296 条（**36.8%**）（词频）。
- Shuffle：`Shuffled Maps = 240`（= 80 map × 3 reduce）（词频）。
- 三个 part 文件：112,097 / 114,084 / 112,602 字节（词频）。
- 总 GC 时间 **444 秒**，峰值单 map 物理内存 **557 MB**（词频）。
- Top30 节选：宝玉 2,571、贾母 779、凤姐 722、那里 709、如今 681（词频）。
- 对拍：单机与 Hadoop 不同词数均 34,214，只在单机出现 0、只在 Hadoop 出现 0、计数不同 0，词次总和均 141,985（词频）。
- 单机 Python 十几秒完成，Hadoop 1 分 33 秒——文档明确结论「1.7 MB 数据量上分布式是净亏损」（词频）。

### 3.4 MapReduce 的教学价值定位（44）

- 4 个作业都是**一次性批统计、无迭代**，Spark 内存缓存优势用不上。
- 输入 2 MB，产出 1.2 MB，**连一个 HDFS block（128 MB）都没填满**。
- 用 MapReduce 是**课程要求**（对应教材第 5 章分布式批处理），且 map/combiner/reducer 三段式透明可讲。

---

## 4. HBase

### 4.1 五张表（当前状态，06、25、07）

| 表 | 行数 | 列族 | 列限定符 | rowkey |
|:---|---:|:---|:---|:---|
| `hlm_chunks` | **11,780** | `d`、`m` | `d:text` `d:char_count` `d:source` `m:hui` `m:huimu` `m:para_start` `m:para_end` `m:seg` `m:version` `m:partition` `m:pizhu_versions` | `<ver>\|<source>\|<hui:03d>\|<chunk_id>` |
| `hlm_vectors` | **11,780** | `v`、`m` | `v:emb`（2048 B float32）、`m:model_id`、`m:dim`、`m:hui` | 同上 |
| `hlm_terms` | **448,841** | `p` | `p:postings`、`p:df`、`p:source` | `<ver>\|[pizhu\|]<bigram>` |
| `hlm_wordfreq` | **133,673** | `f` | `f:cnt` | `<ver>\|<bigram>` |
| `hlm_stats` | **709** | `s` | `s:cnt` `s:n_chunk` `s:n_bytes` `s:n_chars` | `<metric>\|<key>` |

- 五表合计 **606,783 行**（07、`docs/42-S38-建库顺序图.md`；11,780+11,780+448,841+133,673+709 = 606,783）。
- 预分区：`hlm_chunks` / `hlm_vectors` 建表时 `SPLITS => ['1'..'7']`，按 rowkey 首字符切 **8 个 region**；`hlm_terms` **无预分区**（03、07）。
- 写入耗时（S2，1,035 行规模）：`hlm_chunks` **2.5 s**（约 414 行/秒，ADR-04 给出的口径）、`hlm_vectors` **2.1 s**、`hlm_terms` **142.7 s**（03）。
- 单条点查 `< 20 ms`（03、06）。

### 4.2 rowkey 设计（三段/四段式）

```
rowkey = <version> | <source> | <hui:03d> | <chunk_id>
          fuqinju  |  text    |  003      |  bde3ef…
          fuqinju  |  pizhu   |  003      |  001190a5…
```

- **版本在前** → `scan(row_prefix=b"fuqinju|")` 一次扫完，不同版本同位置 chunk 天然隔离（03、06、07）。
- **回次补零三位** → 字典序 = 数值序，`001..080` 可按回次范围扫描（03）。
- **chunk_id 在尾** → 同回内按 ID 排序稳定（03）。
- 向量 rowkey 实例：`chenggao|text|001|0c9755f441a2e2e1`（25）。
- 倒排 rowkey 实例：`chenggao|text|\xE4\xB8\x80\xE4\xB8\x87`（即「一万」），列 `p:df=16`、`p:postings=[["594077df1cfe314a",1], …]`、`p:source=text`（25）。

### 4.3 向量的存储格式

- `v:emb` 存 **`numpy.float32.tobytes()` 原始字节，2,048 B/行**（= 512 × 4），非 base64、非 JSON 数组（03、25、06）。
- 抽样回读 `len(v:emb) == 2048`，11,780 条全部一致（03、06）。
- 若 base64 则 2.7 MB；若 JSON 数组约 20 MB（按 1,035 条口径）（03）。
- 11,780 条 × 512 维 = **8.3 MB**；base64 要 11.1 MB，JSON 要 83 MB（07）。
- 06 另一处口径：11,780 条 float32 bytes = **23.2 MB**（06 §向量方案对比表）。

### 4.4 单条 chunk 的完整 11 个 cell（29，实例 `517c6120437ffe24`）

| cell | 值 |
|:---|:---|
| `d:char_count` | 660 |
| `d:source` | text |
| `d:text` | 660 字正文 |
| `m:hui` | 3 |
| `m:huimu` | 第3回 贾雨村夤缘复旧职 林黛玉抛父进京都 |
| `m:para_end` | 25 |
| `m:para_start` | 17 |
| `m:partition` | hui_001_010 |
| `m:seg` | 9 |
| `m:strategy_rev` | v2-3ver |
| `m:version` | chenggao |

### 4.5 表行数的历史演进（写文档时须选定一个口径）

| 时点 | `hlm_chunks` | `hlm_terms` | 出处 |
|:---|:---|:---|:---|
| S2（仅单版本正文） | 1,035 | 133,673 | 03 |
| 中间态 | 4,269 | 160,371 | `docs/15-S14-前缀噪音清理与文档更新.md` |
| 补脂批后 | 11,846 → 11,780 | 449,395 | `docs/15-S14…` |
| **当前正式值** | **11,780** | **448,841** | 06、25、07、`docs/27-S24-欢迎页架构图.md` |
| 一次索引污染中间态 | 13,124 | 452,460 | `docs/32-S29-彻底修复脂批索引.md`、`docs/30-S28-人物关系图扩充.md` |
| 污染回滚后 | 11,780 | 448,841 | `docs/35-S31-2-架构图再放大与索引污染排查.md` |

---

## 5. Hive / Spark / Flink / Storm / ZooKeeper 的真实使用程度

| 组件 | 用到什么程度 | 证据 |
|:---|:---|:---|
| **Hive 3.1.3** | **真实跑过**：4 张 EXTERNAL 表 `hlm_word_freq`/`hlm_person_freq`/`hlm_cooccur`/`hlm_chunk_stat`，`LOCATION` 指向 `/hlm/mrout/*`，通过 HiveServer2 + beeline 执行 SQL，底层跑 MapReduce。**HiveServer2 未常驻**，按需启动 | 03、06、01 |
| **Spark 3.5.8** | **装了 + 跑过对比实验**：Standalone（master + 2 worker），`local[*]` 与 on YARN 均跑通；但**正式管道不用 Spark**，Spark 版作业只作为教学对照 | 01、44、25 |
| **YARN** | **真实使用**：MapReduce 作业的运行载体，2 个 NodeManager | 01、07 |
| **ZooKeeper 3.8.4** | **单机模式**（非集群，`zoo.cfg` 无 `server.N`），仅作 HBase 的协调服务；宿主映射 12181 | 01、06、07 |
| **Flink 1.20.3** | **只装没用**：端口 18082「⚠️ 未启动（按需）」，项目无任何调用 | 01、25、40 |
| **Storm 1.2.4** | **只装没用**：端口 18085「⚠️ 未启动（按需）」，项目无任何调用 | 01、25、40 |

- 25 的原话口径：「批处理（未用于线上）→ Spark / Flink / Storm（已装，演示用）」；「分布式协调 → ZooKeeper（HBase 依赖）」。
- 40 的原话口径：「Spark / Flink / Storm → **未使用** → 项目里没有调用」。
- 41-部署 的原话口径：「Spark / Flink / Storm → 无 → 项目未使用（见 README 说明）」。
- 07 的课堂口径：「集群里有 Spark 3.5.8，但 MapReduce 对教学更直观（能明确看到 map/shuffle/reduce 三阶段），且 4 个作业数据量小，MR 够用」。

### 5.1 Hive SQL 实测输出（03、06）

```sql
SELECT bigram, cnt FROM hlm_word_freq ORDER BY cnt DESC LIMIT 10;
→ 2703 / 2276 / 1269 / 1179 / 1167 / 1116 / 979 / 949 / 868 / 812

SELECT a, b, cnt FROM hlm_cooccur ORDER BY cnt DESC LIMIT 10;
→ 正确解析出三列（a / b / cnt），Top 共现 15 次

SELECT hui, n_chunk, n_chars FROM hlm_chunk_stat ORDER BY n_chunk DESC LIMIT 5;
→ 67:39/22349  78:21/11457  77:20/10758  62:20/11988  74:19/11159
```

### 5.2 自定义查询沙箱与 Hadoop 的关系（两阶段，口径不同，须分清）

- **S23（25）**：自定义查询**没有用 Hadoop 任何组件**——读的是 `bind D:\...\pipeline\out → /workspace/pipeline/out` 的本地副本（文件系统类型 `v9fs`），执行靠 `prlimit --cpu=5 --as=536870912 --nproc=8 --fsize=1048576 --nofile=32` + `timeout 12`，`grep -nE 'hdfs|hadoop|hbase|happybase'` 无匹配。
- **S24（`docs/26-S24-Hadoop自定义查询.md`）**：放宽沙箱，提供 **8 个封装 API**（`hdfs_ls`/`hdfs_cat`/`hdfs_count_lines`/`hdfs_du`/`hbase_count`/`hbase_get`/`hbase_scan`/`hadoop_status`），真调 `/opt/hadoop/bin/hdfs dfs` 走 HDFS RPC、真用 happybase 连 Thrift 9090。
- S24 的安全约束：路径白名单 `/hlm/`、`/user/root/`；拒绝含 `..`；表名白名单仅 5 张 `hlm_*`；全只读；`hdfs_cat` 单次 ≤ 8 MB；`hbase_scan` ≤ 2000 行；网络仅放行 172.16–20/127/10/192.168。

---

## 6. Spark vs MapReduce 实测对比（44，2026-10-27）

### 6.1 实验设置

| 项 | 设置 |
|:---|:---|
| 输入 | `/hlm/mrin/chunks/chunks.jsonl`（**1,035 行 / 2.0 MB**） |
| 作业 | 中文 bigram 词频统计（= `s3_mapreduce.py` job1） |
| 输出 | `/hlm/mrout/wordfreq{,_cmp,_spark}` |
| 口径对齐 | 必须「先剔除全部非汉字，再取相邻两字（跨标点也连）」，且双字都在 22 字停用词表则跳过 |

> 第一版对比**失败**：Spark 出 **107,529** 个 key，MapReduce 出 **133,673** 个，差 **26,144**——原因是切分口径不一致（44）。

### 6.2 耗时（各跑两遍）

| 引擎 | 第 1 次 | 第 2 次 | 平均 |
|:---|---:|---:|---:|
| MapReduce Streaming | 16.68 s | 16.50 s | **16.6 s** |
| Spark `local[*]` | 8.86 s | 7.24 s | **8.1 s** |
| Spark on YARN（2 executor） | 7.28 s | — | **7.3 s** |

- **Spark 约快 2.0 ~ 2.3 倍**（44）。
- Spark 端到端 7.2 ~ 8.9 s，其中 **JVM/Context 启动 ~4.2 ~ 6.2 s**，**实际计算 1.25 ~ 2.71 s**（44）。
- 集群资源：master 28 core / 15 GB（44）。

### 6.3 结果一致性

```
MR 行数   : 133,673
Spark 行数: 133,673
MR    md5: dd58834b69d2db0dbe19c3b8f9625388
Spark md5: dd58834b69d2db0dbe19c3b8f9625388   ← 逐字节相同
```

Top5 bigram 两边一致：宝玉 2,703 / 笑道 2,276 / 凤姐 1,269 / 什么 1,179 / 太太 1,167（44）。

### 6.4 代码量

| 项 | 行数 |
|:---|---:|
| MR mapper | 21 |
| MR reducer | 20 |
| **MR 合计** | **41** |
| Spark 作业 | 约 40（去掉注释和对比框架） |

### 6.5 结论与定位（44）

- 规模不匹配：2 MB 输入、1.2 MB 产出，**连一个 128 MB block 都没填满**，Spark 启动开销吃掉优势。
- 四个作业都是一次性扫描，无迭代，Spark 内存缓存优势用不上。
- 建议：**保留 MapReduce 作正式管道**，`s40_spark_wordfreq.py` 留作教学对照实验。
- 若要上 Spark DataFrame + HBase Connector，需额外 `hbase-spark` connector jar——当前镜像只有 `hbase-client`。

---

## 7. 关键数字总表

### 7.1 语料与切分

| 数字 | 含义 | 出处 |
|:---|:---|:---|
| 1,446,786 汉字 | 三版本正文合计 | 01 |
| 526,673 汉字 / 83 文件 | 抚琴居校本正文 | 01 |
| 730,730 汉字 / 120 文件 | 程高本正文 | 01 |
| 189,383 汉字 / 29 文件 | 癸酉本正文 | 01 |
| 81 条 / 2,512 段 / 600,163 字符 | `chapters.jsonl`（凡例=0 + 1–80 回） | 01 |
| 609,947 字 | chunk 切分覆盖的总字数 | 03 |
| 1,035 | S2 阶段 chunk 数（单版本正文） | 03、06 |
| 80 回 | 覆盖回次 | 03 |
| 151 / 589.3 / 597 / 786 / 879 | chunk 字数的 min / mean / p50 / p95 / max | 03 |
| 67 次 | 单段超长触发 L3 滑窗次数 | 03 |
| 1,052 → 1,035 | 尾块合并（并入 17 个碎块） | 03 |
| 8 | 业务分区数（按回次每 10 回一区） | 03 |
| 300–800 字 | 目标块区间（v1 与 v2-3ver 同） | 29、07 |
| 500 / 100 | L3 滑窗窗口 / 重叠字数 | 03、29 |
| `v1` / `v2-3ver` | 两代 `strategy_rev` | 03、29 |
| 8,564 字符 | 单回原始语料（第3回） | 29 |
| 32 段落 | 单回结构化后段落数 | 29 |
| 660 字 | 示例 chunk `517c6120437ffe24` 的字数 | 29 |
| 552 汉字 → 527 bigram（545 次） | 该 chunk 的 bigram 切分 | 29 |
| 22 字 | 停用字表大小 | 29 |
| 3,234 条 / 1.54 MB | 脂批（抚琴居），`clean/pizhu.jsonl` | V3A |
| 3,234 条 / 1.62 MB | 脂批，`pizhu_redecoded.jsonl` | V3B |
| 9,156 条（去重 7,215） | 脂批（脂批大全） | 01 |
| 8,510 条 | 三源脂批进索引总量 | 07、`docs/16-S15…` |
| 3,219 / 222 / 5,069 | 三源拆分：抚琴居 / 癸酉本 / 脂批大全 | 07 |
| 11,780 | 当前索引 chunk 总数（正文 3,270 + 脂批 8,510） | 06、07、25、40 |

### 7.2 存储

| 数字 | 含义 | 出处 |
|:---|:---|:---|
| 2,099,616 B（2.0 MB） | `chunks.jsonl` 大小 | 03、06 |
| 2,957,930 B（2.82 MB） | `vectors.jsonl` 大小 | 03、06 |
| 2.82 MB / 34.7 秒 / 29.8 chunk/s | 向量化产出与吞吐（1,035 条） | 03 |
| 94.8 MB | bge-small-zh-v1.5 ONNX 模型 | 03 |
| 512 | 向量维数 | 03、25、29 |
| 2,048 B | `v:emb` 单行字节数（512 × 4） | 03、25、29 |
| 6.33 MB / 4.79 MB | 三版本正文 / 三源脂批 chunk（当前） | 25 |
| 9.44 MB / 24.82 MB | 三版本正文 / 三源脂批向量（当前） | 25 |
| 11.1 MB + 34.3 MB | HDFS 上 chunk 文本 / 向量合计（当前） | 25 |
| 约 65 MB | HDFS 总占用 | 25 |
| 约 43 MB | 单节点方案的种子数据量 | 40、41 |
| 1.56% | chunks.jsonl 的块利用率 | 03、06 |
| 0.13% | V3B 34 个分区文件的块利用率 | V3B |
| 134,217,728 B | 块大小 128 MB | 01、06、hadoop/hdfs-site.xml |
| 2 | 主集群 `dfs.replication` | 01、06 |
| 3 | V3 实验集群 `dfs.replication` | V3B |
| 1 | 单节点方案 `dfs.replication` | 41、hadoop/hdfs-site.xml |

### 7.3 HBase

| 数字 | 含义 | 出处 |
|:---|:---|:---|
| 11,780 / 11,780 | `hlm_chunks` / `hlm_vectors` 行数 | 06、25、07 |
| 448,841 | `hlm_terms` 行数 | 06、25、07 |
| 133,673 | `hlm_wordfreq` 行数 | 03、06 |
| 709 | `hlm_stats` 行数 | 03、06 |
| 606,783 | 五表合计行数 | 07、42 |
| 8 | `hlm_chunks`/`hlm_vectors` 预分区 region 数 | 03 |
| 2.5 s / 2.1 s / 142.7 s | 三表写入耗时（1,035 行/133,673 词项） | 03 |
| < 20 ms | 单条点查延迟 | 03、06 |
| 11 个 cell | 单条 chunk 的 cell 数 | 29 |

### 7.4 MapReduce / Hive / Spark

| 数字 | 含义 | 出处 |
|:---|:---|:---|
| 4 | MapReduce 作业数（词频/人物/共现/逐回） | 03、06 |
| 133,673 | wordfreq Reduce 输出词项 | 03、06 |
| 15,000 → 72 → 41 | personfreq 的 map 输出 / combine 输出 / reduce 输出 | 03 |
| 11,562 → 927 → 10,742 | cooccur 同上 | 03 |
| 1,035 → 81 → 80 | chunkstat 同上 | 03 |
| 99.5% / 92.0% / 92.2% | 三个作业的 combiner 压缩率 | 03 |
| 489,668 / 164,866 / 133,673 | wordfreq 的 map 输出 / combine 输出 / reduce 组数 | 44 |
| 66% | combiner 对该作业的网络传输压缩 | 44 |
| ~18 / ~12 / ~13 / ~13 s | 四作业耗时 | 03 |
| 16.68 / 16.50 / 16.6 s | wordfreq 的 MR 实测两遍与平均 | 44 |
| 8.86 / 7.24 / 8.1 s | Spark `local[*]` 两遍与平均 | 44 |
| 7.28 / 7.3 s | Spark on YARN（2 executor） | 44 |
| 2.0 ~ 2.3 倍 | Spark 相对 MR 加速比 | 44 |
| ~4.2 ~ 6.2 s / 1.25 ~ 2.71 s | Spark 启动 / 实际计算耗时 | 44 |
| 107,529 vs 133,673（差 26,144） | 口径不一致时 Spark vs MR 的 key 数 | 44 |
| `dd58834b69d2db0dbe19c3b8f9625388` | 两边输出 md5（相同） | 44 |
| 21 / 20 / 41 | MR mapper / reducer / 合计行数 | 44 |
| 4 张 | Hive 外部表数 | 03、06 |
| 2,703 / 2,276 / 1,269 / 1,179 / 1,167 | Hive 查出的 Top5 bigram 频次 | 03、44 |
| 15 次 | Top 共现次数 | 03 |
| 22,349 字 / 39 chunk | 第 67 回（Hive 逐回统计首位） | 03 |
| 80 | 独立词频作业的 map 任务数 | 词频 |
| 34,214 / 141,985 | 独立词频作业的不同词数 / 总词次 | 词频 |
| 1,812,222 B / 5,081 行 / 80 文件 | 独立词频作业的输入 | 词频 |
| 141,985 → 89,689（36.8%） | 独立词频作业的 combiner 压缩 | 词频 |
| 1 分 33 秒 / 17 秒 | 独立词频作业两作业耗时 | 词频 |
| 444 秒 / 557 MB | 总 GC 时间 / 峰值单 map 物理内存 | 词频 |

### 7.5 V3 实验（分区与 HDFS）

| 数字 | 含义 | 出处 |
|:---|:---|:---|
| 3,234 → **5,569**（×1.72） | 批语多归属语义冗余后的物理记录数（`负载比统计.txt` 逐字核对） | `V3分区实验/负载比统计.txt` |
| 11,140 | 2 副本落地后的物理记录数 | V3A、V3B |
| 3.44× | 存储倍率（1.72 × 2） | V3A |
| 17 / 34 | 逻辑分区数 / 物理文件数 | V3B |
| 13 / 11 / 10 | nodeA / nodeB / nodeC 的分区数 | V3A、V3B |
| 3,905 / 4,097 / 3,138 | nodeA / nodeB / nodeC 的记录数 | V3A |
| 1.10 | 节点级 max/mean | V3A |
| 5,723,270 B（5.46 MB） | 34 个分区文件总字节 | V3B |
| 34 / 168,331 B | Block 数 / 平均块大小 | V3B |
| 1 vs 6 | 同一文件在 128MB / 1MB 块下的 Block 数 | V3B |
| 47 / 47 / 47 / 141 | 各 DataNode 块数 / 合计副本块 | V3B |
| 22.52 MB / 67.55 MB | 各节点 DFS Used / 合计 | V3B |
| 1.62 → 5.72 → 17.17 MB（10.6×） | 三层放大后的实测数据量 | V3B |
| 1,745 ms / 1,235 ms | V3B wordcount 的 map / reduce 时间 | V3B |
| 11,140 / 432,736 / 5,052 | V3B wordcount 的 map 输入 / map 输出 / reduce 输出 | V3B |
| 2.34 / 1.13 / 3.03 / 2.60 / 10.38 / 1.85 | V3A 六种分区方案的记录比 | V3A |
| 2.61（6 分区）/ 2.60（9 分区） | 旧标注 vs 新标注的 max/mean | V3A |

### 7.6 部署与资源

| 数字 | 含义 | 出处 |
|:---|:---|:---|
| 6.89 GB | 完整镜像 `hadoop-lab:2026.10` | 01、41、41-部署 |
| 2.54 GB | 单节点 HDFS 镜像 `hlm-hdfs` | 41 |
| 63% | 镜像体积减少比例 | 41 |
| 730 MB | Hadoop 3.3.6 tar.gz | 41 |
| 665 MB | 后端镜像 | 40、41 |
| 29 s / 12 s | 单节点 HDFS 首次 / 二次启动耗时 | 41 |
| 8 s / 55 s | 后端加载耗时（本地文件 / HDFS WebHDFS） | 40 |
| 约 700 MB / 约 1.2 GB / 约 2 GB | HDFS 容器内存 / 后端容器内存 / 合计 | 41 |
| 3 + 1 | Hadoop 节点数 + 应用容器数 | 25 |
| 2 | Live DataNode 数 / NodeManager 数 | 01、06、25 |
| 15/15 | 索引重建后检索结果逐条一致数 | 03、06 |
| 5/5 | 三种数据源模式的检索命中数 | 40、41 |
| 9/10 | 版本兼容改造后的检索命中 | 40 |
| 114.4 MB | 部署包体积 | 40 |

---

## 8. 文档间的数字矛盾（必须二选一或加注）

| # | 冲突项 | 文件 A 说 | 文件 B 说 | 说明 |
|:--:|:---|:---|:---|:---|
| 1 | **第 67 回字数** | **21,728 字**，均值 7,490 的 2.9 倍（03 ADR-04、06 §4.3.6） | **22,349 字**，39 个 chunk（03 §6.3、Hive 查询结果） | 同一文档 03 内自相矛盾；21,728 按「整回」口径、22,349 按 chunk 累计口径也未在文中说明 |
| 2 | **cooccur 组数** | **10,742** 组（03 §6.1、§6.5 注明「重跑后已同步」） | **588** 行（03 §6.5 表内、29 §四、`docs/28-S25-项目原理页.md`） | 588 是重跑前的旧值，10,742 是重跑后；正文与表格混用了两个口径 |
| 3 | **二级分区负载比** | **10.38**（V3A §二方案⑤；`V3分区实验/负载比统计.txt` 现行 6 方案口径） | **12.57**（06 §4.3.6；01 §资产清单引「5 种方案 2.31 / 1.16 / 4.05 / 12.57 / 5.96」） | 12.57 出自**旧版 5 方案统计**（那套口径的首项是 2.31，现行是 2.34），06 把旧值说成「与 V3分区实验文档一致」，实际现行文件写的是 10.38 |
| 4 | **HDFS 容量与已用量** | 1.97 TB，已用 **550.46 MB**（0.03%）（25） | 2.0 TB，已用 **366.3 MB**（0%）（06） | 两次快照时点不同，不能混用 |
| 5 | **chunk 正文/脂批拆分** | 3,270 + 8,510 = **11,780**（07、40 §十、41-部署） | 3,295 + 8,662 = **11,957**（40 §6.2、41 §6.3 的启动日志） | 后者与「向量矩阵 11,957」自洽，但与 chunk 总数 11,780 不符 |
| 6 | **向量条数** | **11,780**（`hlm_vectors` 行数，06/25/07） | **11,957**（`载入向量矩阵: (11957, 512)`，40/41/41-部署） | 同一系统内 chunk 数与向量数不等，文档未解释差异来源 |
| 7 | **hlm_terms 行数** | **448,841**（06、25、07 正式值） | **449,395**（`docs/15-S14…`）、**452,460**（`docs/32-S29…`，对应 13,124 chunk 的污染态） | 应为索引污染与回滚的历史值，写文档须取 448,841 |
| 8 | **独立词频作业的输入行数** | **5,081 个非空行**（词频 §一、§三） | **2,580 行**（词频 §八对拍表） | 同一文档内自相矛盾，2,580 疑为 5,080 的笔误，文档未说明 |
| 9 | **HDFS 上三版本文件大小** | 6.33 / 4.79 / 9.44 / 24.82 MB（25） | 6.03 / 4.56 / 9.01 / 23.67 MB（40） | **不是真冲突**：后者为十进制 MB、前者为 MiB，×1.0486 后两组完全对应（6.03×1.0486≈6.32） |
| 10 | **脂批原始体量** | 3,234 条 / **1.54 MB**（V3A） | 3,234 条 / **1.62 MB**（V3B） | 条数一致、字节数不同，两份报告的同一数据集口径未对齐 |
| 11 | **单版本向量总大小** | 2.82 MB（03，1,035 条实测）／ **8.3 MB**（07、06 §5.3 第 3 条，11,780 条） | **23.2 MB**（06 §4.3.5 对比表「11,780 条」列） | 三处口径打架：03 是 1,035 条实测；8.3 MB 与「HDFS 快照 2.82 MB」矛盾（快照含 base64 膨胀，应大于裸字节）；23.2 MB 与 8.3 MB 相差 2.8 倍且文档未解释。**建议只引用 03 的 1,035 条实测值** |
| 12 | **多归属后总记录数** | **5,563**（×1.72）（V3A §五、V3B §三） | **5,569**（`V3分区实验/负载比统计.txt` 原文「总记录 5569 (×1.72)」） | 报告与其引用的原始统计文件差 6 条，两份报告都沿用了 5,563 |

---

## 9. 只出现在单一文档、无法交叉验证的数字（引用时建议标注「单源」）

- `hlm_stats` 的 709 行及其构成 `personfreq(41) + cooccur(588→10,742) + chunkstat(80)`（03）——41+588+80 = 709，说明**表里存的仍是 588 条旧共现值**，而 03 正文宣称「10,742 已同步」，两者不自洽。
- V3A 的 `max/mean` 六方案数据可由 `V3分区实验/负载比统计.txt` 原样复核（①2.34 ②1.13 ③3.03 ④2.60 ⑤10.38 ⑥1.85），该 txt 与报告完全一致，可信度高。
- `负载比统计.txt` 记多归属后总记录 **5,569**，而 V3A 报告写 **5,563**、V3B 也写 **5,563**；1.72 倍率对 3,234 应为 5,562–5,569 区间，**报告与原始统计文件差 6 条**。
- `hlm_chunks` 写入「约 414 行/秒」（03 ADR-04）——由 1,035 行 / 2.5 秒推算。
- 索引可重建的「15/15」（03、06）——两次文档引用同一实验结果。
- Spark 的「28 core / 15 GB」（44）。
- 单节点方案启动日志中的时间戳链（41）——11:23:18 → 11:23:47，合计 29 秒。
