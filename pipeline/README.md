# 红楼智问 · 数据管道（S2 / S3）

> 本目录是 **Hadoop 落地**的全部代码。
> 所有脚本都实测跑通，数字均来自 2026-10-24 的 `hadoop-lab:2026.10` 集群。

---

## 一、这条管道做了什么

```
                    ┌──────────── 事实源（HDFS）────────────┐
hlm_corpus     →  切分  →  chunks.jsonl  ──┐
（3 版本正文）      │                        │
                    │                        ├→ /hlm/warehouse/chunks/fuqinju/
                    ↓                        │
              L1 按回 / L2 场景段 / L3 滑窗  │
                                             ↓
                                    向量化（ONNX bge-small-zh 512 维）
                                             │
                                             ├→ /hlm/derived/vectors/fuqinju/model=bge-small-zh-v1.5/
                                             │
                    ┌──────────── 派生索引（HBase）──────────┐
                    │  hlm_chunks   1,035 行  chunk 正文+元数据
                    │  hlm_vectors  1,035 行  512 维 float32
                    │  hlm_terms  133,673 行  bigram 倒排
                    └───────────────────────────────────────┘
                                             │
                    ┌──────────── 统计（MapReduce）──────────┐
                    │  job1 wordfreq    133,673 词项
                    │  job2 personfreq       41 人物
                    │  job3 cooccur      10,742 组
                    │  job4 chunkstat        80 回
                    └───────────────────────────────────────┘
                                             │
                    ┌──────────── 结果落地 ──────────────────┐
                    │  HBase  hlm_wordfreq(133,673) / hlm_stats(709)
                    │  Hive   4 张外部表，可 SQL 查询
                    └───────────────────────────────────────┘
```

---

## 二、文件清单

| 文件 | 作用 | 运行位置 |
|:---|:---|:---|
| `s2_1_chunk.py` | Chunk 三级切分（L1 按回 / L2 场景段 / L3 滑窗） | **Windows 本地** |
| `s2_2_index.py` | 上传 HDFS + 向量化 + 写 HBase | **master 容器** |
| `s2_3_search.py` | 三路混合检索验证（关键词 + 向量 + RRF） | **master 容器** |
| `s2_4_partition_compare.py` | 分区方案对比（章节范围 vs chunk_id 哈希） | **Windows 本地** |
| `s3_mapreduce.py` | 4 个 Hadoop Streaming 统计作业 | **master 容器** |
| `s3_to_hbase_hive.py` | 统计结果写 HBase + 建 Hive 外部表 | **master 容器** |
| `run_all.sh` | 一键跑完整 S2/S3 流水线 | **master 容器** |

---

## 三、快速复现

### 3.1 本地切分（Windows）

```powershell
cd D:\学校事务\大数据原理与应用\红楼智问\pipeline
python s2_1_chunk.py
# → out/chunks.jsonl              1,035 chunk  2.0 MB
# → out/chunks_manifest.json      分区 manifest + sha256
# → out/chunk_stats.json          切分统计
```

### 3.2 上传 + 向量化 + 索引（容器内）

```powershell
# 把整个 pipeline 目录送进容器
$root = "D:\学校事务\大数据原理与应用\红楼智问"
docker exec master mkdir -p /workspace/pipeline/out
docker cp "$root\pipeline" master:/workspace/

# 分步执行
docker exec master bash -lc "cd /workspace/pipeline && python3 s2_2_index.py --stage upload"
docker exec master bash -lc "cd /workspace/pipeline && python3 s2_2_index.py --stage embed"
docker exec master bash -lc "cd /workspace/pipeline && python3 s2_2_index.py --stage hbase"
```

### 3.3 MapReduce 统计

```powershell
docker exec master bash -lc "cd /workspace/pipeline && python3 s3_mapreduce.py"
docker exec master bash -lc "cd /workspace/pipeline && python3 s3_to_hbase_hive.py"
```

### 3.4 检索验证

```powershell
docker exec master bash -lc "cd /workspace/pipeline && python3 s2_3_search.py --q '贾宝玉的丫鬟有谁' --topk 3"
```

### 3.5 一键全跑

```powershell
docker exec master bash -lc "cd /workspace/pipeline && bash run_all.sh"
```

---

## 四、实测结果（可复现的基准数字）

### 4.1 切分

| 指标 | 值 |
|:---|---:|
| chunk 数 | 1,035 |
| 块大小 min/mean/p50/p95/max | 151 / 589.3 / 597 / 786 / 879 |
| 总字数 | 609,947 |
| L3 滑窗触发 | 67 次 |
| 尾块合并 | 1,052 → 1,035（并入 17） |
| 分区负载比 max/mean | **1.229** |

### 4.2 HDFS

| 项 | 值 |
|:---|:---|
| chunks.jsonl | 2,099,616 B / 1 块 / 2 副本 / HEALTHY |
| sha256 | `887d66f699e433f97ff7e852f2bd19ddee739c3454bff783f4b986caa5dac884` |
| 向量快照 | 2.82 MB（`/hlm/derived/vectors/fuqinju/model=bge-small-zh-v1.5/`） |

### 4.3 HBase

| 表 | 行数 | 耗时 |
|:---|---:|---:|
| `hlm_chunks` | 1,035 | 2.5 s |
| `hlm_vectors` | 1,035 | 2.1 s |
| `hlm_terms` | 133,673 | 142.7 s |
| `hlm_wordfreq` | 133,673 | 118.2 s |
| `hlm_stats` | 709 | 1.3 s |

### 4.4 MapReduce（4 个作业全成功）

| 作业 | 输出行数 | Reduce 输入组 | Combiner 效果 |
|:---|---:|---:|:---|
| wordfreq | 133,673 | 133,673 | — |
| personfreq | 41 | 41 | 15,000 → **72**（压缩 99.5%） |
| cooccur | 10,742 | 10,742 | 11,562 → 927（压缩 92%） |
| chunkstat | 80 | 80 | 1,035 → **81**（压缩 92%） |

> combiner 效果这么显著，是因为 key 重复度高：15,000 次人名提及只对应 41 个人物。
> 这正是教材讲的「combiner 在同 key 重复多时收益最大」。

### 4.5 检索

查询 `贾宝玉的丫鬟有谁`：

| 环节 | 结果 | 耗时 |
|:---|:---|---:|
| L1 关键词 | 30 命中（38/45 bigram） | 44.0 ms |
| L2 向量 | 1,035 候选，top-1 sim=0.6285 | 383.5 ms |
| RRF 融合 | 58 候选 | 0.1 ms |
| 总计 | — | **427.6 ms** |

### 4.6 索引可重建（R-510 已验证）

```
1. 基线：查询「林黛玉为什么进贾府」→ 保存 Top-5（RRF / rowkey / 位置）
2. disable + drop hlm_chunks / hlm_vectors / hlm_terms
3. 确认 HDFS 事实源完好（chunks.jsonl + vectors.jsonl 仍在）
4. hdfs dfs -get 拉回事实源 → --stage hbase 重建
5. 再查同一问题 → 与基线逐条对拍
   → ✅ 15/15 行完全一致，零差异
```

### 4.7 副本故障恢复（R-509 已验证）

```
1. 基线：checksum = 889a5aef3dcb7cb2de2d5de2fc31a439
         块 blk_1073742639_1839  Live_repl=2（slave1 + slave2）
2. pkill DataNode（slave1）→ Dead datanodes 1
3. 验证：hdfs dfs -cat 仍读出 1,035 行；checksum 完全一致
   → 2 副本下停 1 个节点，服务不中断
4. 重启 DataNode → Live datanodes 回到 2
   fsck：Under-replicated 0 / Missing blocks 0 / Status HEALTHY
   校验值仍为 889a5aef...
```

### 4.8 分区方案对比（R-508）

| 方案 | 分区数 | 记录 max/mean | 字节 max/mean | 判定 |
|:---|---:|---:|---:|:---|
| **A 章节范围（每10回）** | 8 | **1.229** | 1.240 | 均衡 ✅ |
| B chunk_id 哈希（8 区） | 8 | 1.105 | 1.110 | 均衡 ✅ |
| B' chunk_id 哈希（16 区） | 16 | 1.268 | 1.292 | 均衡 ✅ |
| C 按版本（1 版本退化） | 1 | 1.000 | 1.000 | — |

**为什么范围分区的负载比（1.229）反而比哈希（1.105）差，但两者都远低于阈值 2？**

因为切分单元是「场景段」而非「整回」，块大小被约束在 151–879 字（mean=589），
**块本身就均匀**，范围分区的热点前提被消除了。

这是「**切分策略决定分区均衡性**」的直接证据——
教材说范围分区易热点，但前提是「单元大小不均」。
若按整回分区，第 67 回 21,728 字 = 均值的 2.9 倍 → max/mean ≈ 2.9，**就必须解释**。

**最终选择方案 A**：① 负载比已达标 ② 保留回次顺序，查某回只扫 1 个分区 ③ 语义清晰

---

## 五、踩过的坑（详细版见 `docs/03-S2数据管道与Hadoop落地.md`）

| # | 坑 | 一句话 |
|:---|:---|:---|
| 1 | `hdfs dfs -fsck` 不存在 | fsck 是 `hdfs fsck`，不在 `hdfs dfs` 里 |
| 2 | MapReduce 输入目录 `_` 前缀被忽略 | `_`/`.` 开头路径被 FileInputFormat 静默过滤 |
| 3 | happybase 表名前缀 | `table_prefix=b""` 仍会加 `_`；必须同时 `table_prefix_separator=b""` |
| 4 | HBase DDL 被 bash 展开 | `echo "{VERSIONS=>1}" \| hbase shell` 会被 brace expansion 破坏；改用临时文件 |
| 5 | `hbase shell describe` 太慢 | 每次启动 JVM 约 240 秒，不能放进轮询 |
| 6 | 表存在性判断误报 | 用 `name in output` 子串匹配会撞上；要解析 `=> [...]` |
| 7 | `scan(filter=...)` 不是 Python 函数 | happybase 的 filter 是 Thrift 服务端过滤器语法 |
| 8 | 中文路径可 bind mount | Docker Desktop on Windows 支持，代码可放项目目录 |

---

## 六、依赖

| 项 | 版本 | 说明 |
|:---|:---|:---|
| Hadoop | 3.3.6 | HDFS 3.3.6 + YARN + MR |
| HBase | 2.5.10-hadoop3 | Thrift 9090 必须启动 |
| Hive | 3.1.3 | HiveServer2 10000（`/workspace/start-hive.sh`） |
| Python（容器） | 3.10.12 | |
| happybase | 1.3.0 | 注意表前缀坑 |
| onnxruntime | 1.23.2 | `pip install onnxruntime` |
| numpy | 2.2.6 | |
| bge-small-zh-v1.5 | ONNX 94.8 MB | `/data/opt/models/bge-small-zh-v1.5/` |

启动 Thrift（容器重启后需重跑）：
```bash
docker exec -d master bash -lc "hbase thrift start > /logs/hbase-thrift.log 2>&1"
```

启动 HiveServer2：
```bash
docker exec -d master bash -lc "bash /workspace/start-hive.sh > /logs/hiveserver2.log 2>&1"
```
