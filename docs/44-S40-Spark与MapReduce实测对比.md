# S40 · Spark 能做吗？与 MapReduce 实测对比

> 日期：2026-10-27
> 问题：项目里的 MapReduce 能不能换成 Spark？效果如何？

## 结论先说

**能换，而且结果完全一样、速度约快 2.3 倍。**
但**本项目的规模下没必要换** —— 理由见第四节，那才是课堂要讲的点。

---

## 一、实验设计（同输入、同口径、逐字节比对）

光讲理论没说服力，做了一次实测。

| 项 | 设置 |
|:---|:---|
| 输入 | `/hlm/mrin/chunks/chunks.jsonl`（1,035 行 / 2.0 MB）|
| 作业 | 中文 bigram 词频统计（= `s3_mapreduce.py` job1）|
| 输出 | `/hlm/mrout/wordfreq{,_cmp,_spark}` |

### 口径必须完全一致，否则对比无意义

第一版对比**失败了**：Spark 出 107,529 个 key，MapReduce 出 133,673 个。
差 26,144 —— 原因是我按「连续汉字段」切分 bigram，
而原 mapper 是**先把非汉字全部剔除、再取相邻两字**（跨标点也会连成 bigram），
并且有 22 字停用词过滤（双字都在停用词表则跳过）。

对齐这三条后，两边 key 数才一致。

> **这是复现实验最容易被忽略的地方**：不是"都是 bigram 统计"就可比，
> 预处理口径差一点，结果差几万个 key。

---

## 二、实测结果

### 2.1 耗时（各跑两遍）

| 引擎 | 第 1 次 | 第 2 次 | 平均 |
|:---|---:|---:|---:|
| MapReduce Streaming | 16.68 s | 16.50 s | **16.6 s** |
| Spark `local[*]` | 8.86 s | 7.24 s | **8.1 s** |
| Spark on YARN（2 executor）| 7.28 s | — | **7.3 s** |

**Spark 约快 2.0 ~ 2.3 倍。**

拆开看 Spark 的时间构成：

```
端到端        7.2 ~ 8.9 s
  含 JVM/Context 启动    ~4.2 ~ 6.2 s   ← 占大头
  实际计算               1.25 ~ 2.71 s
```

也就是说 **Spark 的时间有一半以上花在启动上**。
这直接解释了为什么小数据量下差距只有 2 倍，而不是 10 倍以上。

### 2.2 结果一致性：md5 完全相同

```
MR 行数   : 133,673
Spark 行数: 133,673
MR    md5: dd58834b69d2db0dbe19c3b8f9625388
Spark md5: dd58834b69d2db0dbe19c3b8f9625388
✅ 内容完全一致（逐字节相同）
```

高频 bigram Top5 两边也完全一致：

| bigram | 次数 |
|:---|---:|
| 宝玉 | 2,703 |
| 笑道 | 2,276 |
| 凤姐 | 1,269 |
| 什么 | 1,179 |
| 太太 | 1,167 |

**结论：两个引擎在这个作业上是等价的。** 换引擎不影响数据正确性。

### 2.3 MapReduce 的 shuffle 细节（对照组）

```
Map input records   = 1,035
Map output records  = 489,668
Combine input       = 489,668
Combine output      = 164,866   ← combiner 压掉 66%
Reduce input groups = 133,673
Reduce output       = 133,673
```

combiner 把 48.9 万条中间记录压到 16.5 万条，**网络传输减少 66%**。
Spark 的 `reduceByKey` 内部做的是同一件事（map-side combine），
所以两者在这一步的优化思路一致。

### 2.4 代码量

| 项 | 行数 |
|:---|---:|
| MR mapper | 21 |
| MR reducer | 20 |
| **MR 合计** | **41** |
| Spark 作业 | 约 40（去掉注释和对比框架）|

代码量相当。但性质不同：
MR 是「两个独立脚本 + `hadoop jar` 一串参数」，
Spark 是「一个 Python 文件，`rdd.flatMap().reduceByKey()`」。

---

## 三、Spark 在这台机器上的可用性

已确认可用，无需额外安装：

| 项 | 状态 |
|:---|:---|
| Spark 版本 | 3.5.8 / Scala 2.12.18 / OpenJDK 11 |
| `spark-submit` | `/opt/spark/bin/spark-submit` |
| local 模式 | ✅ 已跑通 |
| **on YARN** | ✅ 已跑通（YARN 2 节点 RUNNING）|
| 集群资源 | master 28 core / 15 GB |

---

## 四、那为什么本项目还用 MapReduce？

这是课堂真正要讲的部分。**不是 Spark 不好，是规模不匹配。**

### 4.1 数据量太小，Spark 的启动开销吃掉优势

```
输入 2 MB，Spark 启动 4~6 秒，计算 1.3~2.7 秒
```

真要体现 Spark 优势，数据量得大到让「计算时间 >> 启动时间」。
本项目全部中间数据 2 MB、产出 1.2 MB —— **连一个 HDFS block（128 MB）都没填满**。

### 4.2 四个作业都是一次性批统计，没有迭代

Spark 最大的优势是**内存缓存复用**，适合：

- 机器学习迭代（同一份数据扫 100 遍）
- 交互式查询（同一份数据反复查）
- 流式计算

本项目的 wordfreq / personfreq / cooccur / chunkstat 都是**扫一遍就完**，
没有一轮需要复用上一轮的内存数据 —— Spark 的核心优势用不上。

### 4.3 教材对齐

`docs/03-S2数据管道与Hadoop落地.md` 里写明了作业对应「教材第 5 章（分布式批处理）」，
用 MapReduce 是**课程要求**，也是理解 shuffle 语义最直接的教具。

### 4.4 MapReduce 的教学价值反而更高

讲 shuffle 时，MapReduce 的 mapper / combiner / reducer 三段式是**透明的**：

```
Map output 489,668 → Combine 164,866 → Reduce groups 133,673
```

这三个数字直接摆在作业日志里，学生能看到「combiner 压掉 66%」这件事。
Spark 里这一步藏在 `reduceByKey` 内部，反而不容易讲透。

---

## 五、如果真要用 Spark，怎么改

技术上完全可行，改动量不大：

### 5.1 最小改动：只换计算层

```
s3_mapreduce.py（Streaming + hadoop jar）
    ↓ 替换为
s40_spark_wordfreq.py（Spark RDD）
```

HDFS 输入输出不变，HBase 写入不变（`s3_to_hbase_hive.py` 照旧读 TSV）。
**下游零改动**，因为产出格式一样。

### 5.2 进阶：用 DataFrame + HBase Connector

```python
spark.read.format("org.apache.hadoop.hbase.spark") \
     .option("hbase.table", "hlm_chunks") \
     .load()
```

但这需要额外的 `hbase-spark` connector jar。
当前镜像里只有 `hbase-client`，**没有 hbase-spark connector** —— 要用得先补 jar。

### 5.3 建议的定位：并存，不替换

| 作业 | 建议引擎 | 理由 |
|:---|:---|:---|
| 4 个批统计（wordfreq 等）| **MapReduce** | 一次性扫描，规模小，教材对齐 |
| 自定义查询沙箱 | 都行 | 走 happybase 直连，不涉及引擎 |
| 未来若加向量聚类/训练 | **Spark MLlib** | 迭代计算，正是 Spark 强项 |

我倾向于**保留 MapReduce 作为正式管道，把 `s40_spark_wordfreq.py` 留着做教学对照实验** ——
一次实验就能让学生直观看到「同样的活，两个引擎怎么写、快多少、结果一不一样」。

---

## 六、课堂上怎么讲这段

推荐这个顺序：

1. **先给数字**：同样 1,035 行输入，MR 16.6 秒，Spark 8.1 秒
2. **再给 md5**：`dd58834b…` 两边完全一样 —— 引擎不影响正确性
3. **追问为什么只快 2 倍**：拆开 Spark 耗时，发现启动占 4~6 秒
4. **引出结论**：Spark 优势在**大数据量 + 迭代计算**，不在小批量统计
5. **点题**：选型不是选"更先进"的，是选**匹配规模**的

第 3 步是关键 —— 如果只报"快 2 倍"，学生会以为 Spark 不过如此；
拆开看到启动占比，才理解规模决定选型。

---

## 七、改动文件

| 文件 | 说明 |
|:---|:---|
| `pipeline/s40_spark_wordfreq.py` | **新增** Spark 版 wordfreq（含实测结论注释）|

实验产出已清理，正式数据未受影响（`/hlm/mrout/wordfreq` 仍为 133,673 行）。

---

*本文档记录 Spark 与 MapReduce 的同口径实测对比。*
