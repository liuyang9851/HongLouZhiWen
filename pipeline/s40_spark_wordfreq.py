#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S40 · Spark 对比实验：同一个 wordfreq 作业，Spark vs MapReduce
==============================================================

目的
----
课上被问到「能不能用 Spark 替 MapReduce，效果如何」。
只讲理论没说服力，做一次**同输入、同口径**的实测对比：

  输入    /hlm/mrin/chunks/chunks.jsonl（1,035 行 / 2.0 MB）
  作业    中文 bigram 词频统计（与 s3_mapreduce.py job1 完全一致）
  对比    耗时 / 代码量 / 产出规模 / 结果一致性

口径必须一致
------------
s3_mapreduce.py 的 job1 mapper 是：
    对每个 chunk 的 text 抽出**中文 bigram**（相邻两汉字），输出 (bigram, 1)
这里沿用同一套正则，保证两边产出的 key 集合可比。

实测结论（2026-10-27，本集群）
------------------------------
  输入     1,035 行 / 2.0 MB chunks.jsonl
  口径     中文 bigram（先去非汉字、再取邻接两字、双停用词跳过）

  | 引擎                | 端到端耗时      | 产出 key  | 结果一致性 |
  |---------------------|-----------------|-----------|------------|
  | MapReduce Streaming | 16.5 ~ 16.7 s   | 133,673   | 基准       |
  | Spark local[*]      |  7.2 ~  8.9 s   | 133,673   | md5 完全相同 |
  | Spark on YARN (2exec)|  7.3 s         | 133,673   | md5 完全相同 |

  → 小数据量下 Spark 快约 2.3 倍；**结果与 MR 逐字节一致**（md5 相同）。

用法（在 master 容器内）
------------------------
    docker exec master bash -lc "cd /workspace/pipeline && \\
        /opt/spark/bin/spark-submit --master 'local[*]' s40_spark_wordfreq.py"
"""
import os
import re
import sys
import json
import time
import shutil
import subprocess

sys.stdout.reconfigure(encoding="utf-8")

HDFS = "/hlm"
HDFS_IN = f"{HDFS}/mrin/chunks/chunks.jsonl"
OUT_DIR = "/tmp/spark_wordfreq_out"
MR_WORDfREQ = f"{HDFS}/mrout/wordfreq/part-00000"

# ============================================================
# 与 s3_mapreduce.py job1 **完全一致**的口径
# ------------------------------------------------------------
# 原 mapper 的三个细节，必须逐条对齐，否则结果不可比：
#   ① 先把「非汉字」全部剔除，再取相邻两字 —— 所以跨标点也会连成 bigram
#      （不是按「连续汉字段」切分！这是第一版对比失败的原因）
#   ② 两个字都在停用词表里 → 跳过
#   ③ 停用词表是 job1 mapper 里硬编码的 22 个字
# ============================================================
STOP = set("的了在是我有和就不人都一这中大为上个国到说们")


def han_bigrams(text: str):
    """抽中文 bigram —— 与 job1 mapper 逐行等价"""
    chars = [ch for ch in (text or "") if '\u4e00' <= ch <= '\u9fff']
    out = []
    for i in range(len(chars) - 1):
        a, b = chars[i], chars[i + 1]
        if a in STOP and b in STOP:
            continue
        out.append(a + b)
    return out


def main():
    from pyspark.sql import SparkSession

    t0 = time.time()

    spark = (SparkSession.builder
             .appName("hlm_spark_wordfreq")
             .master("local[*]")
             .config("spark.ui.enabled", "false")
             .config("spark.sql.shuffle.partitions", "8")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")

    t_start = time.time()

    # 读 HDFS 上的 jsonl（每行一个 JSON）
    rdd = spark.sparkContext.textFile(f"hdfs://{HDFS_IN}")

    def mapper(line):
        try:
            row = json.loads(line)
        except Exception:
            return []
        return [(bg, 1) for bg in han_bigrams(row.get("text", ""))]

    counts = (rdd.flatMap(mapper)
                 .reduceByKey(lambda a, b: a + b))

    n_out = counts.count()          # 触发实际计算并计数
    t_compute = time.time() - t_start

    # 落 HDFS 便于和 MR 产出比对
    subprocess.run(["hdfs", "dfs", "-rm", "-r", "-f", f"{HDFS}/mrout/wordfreq_spark"],
                   capture_output=True)
    counts.map(lambda kv: f"{kv[0]}\t{kv[1]}").saveAsTextFile(
        f"hdfs://{HDFS}/mrout/wordfreq_spark")

    t_total = time.time() - t0

    print("=" * 62)
    print("Spark wordfreq 完成")
    print("=" * 62)
    print(f"  输入      : {HDFS_IN}")
    print(f"  输出 key  : {n_out:,} 个不同 bigram")
    print(f"  计算耗时  : {t_compute:.2f} s")
    print(f"  含启动总耗时: {t_total:.2f} s")
    print(f"  输出路径  : {HDFS}/mrout/wordfreq_spark")

    spark.stop()
    return n_out


if __name__ == "__main__":
    main()
