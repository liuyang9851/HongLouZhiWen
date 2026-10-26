#!/bin/bash
# =============================================================
# S2/S3 一键流水线（在 master 容器内运行）
# =============================================================
# 用法：
#   docker cp 整个 pipeline 目录进 master:/workspace/pipeline
#   docker exec master bash -lc "cd /workspace/pipeline && bash run_all.sh"
#
# 阶段：
#   1. 上传 chunks 到 HDFS（含 sha256 + fsck 校验）
#   2. 向量化（bge-small-zh ONNX）→ HDFS 快照
#   3. 写 HBase（chunks / vectors / terms）
#   4. MapReduce 4 个统计作业
#   5. 统计结果写 HBase + Hive 外部表
#   6. 检索验证
# =============================================================
set -e
cd /workspace/pipeline

echo "############################################################"
echo "# 红楼智问 · S2/S3 流水线"
echo "# $(date '+%Y-%m-%d %H:%M:%S')"
echo "############################################################"

echo ""
echo ">>> [0/6] 环境自检"
hdfs dfsadmin -safemode get
hdfs dfsadmin -report 2>/dev/null | grep "Live datanodes"
yarn node -list 2>/dev/null | grep -E "Total Nodes"
python3 -c "import happybase, onnxruntime, numpy; print('happybase/onnxruntime/numpy OK')"

echo ""
echo ">>> [1/6] 上传 chunks 到 HDFS"
python3 s2_2_index.py --stage upload

echo ""
echo ">>> [2/6] 向量化"
python3 s2_2_index.py --stage embed

echo ""
echo ">>> [3/6] 写 HBase 索引"
python3 s2_2_index.py --stage hbase

echo ""
echo ">>> [4/6] MapReduce 统计作业"
python3 s3_mapreduce.py

echo ""
echo ">>> [5/6] 统计结果写 HBase + Hive"
python3 s3_to_hbase_hive.py

echo ""
echo ">>> [6/6] 检索验证"
python3 s2_3_search.py --q "贾宝玉的丫鬟有谁" --topk 3
echo ""
python3 s2_3_search.py --q "林黛玉为什么进贾府" --topk 3

echo ""
echo "############################################################"
echo "# 流水线完成 $(date '+%Y-%m-%d %H:%M:%S')"
echo "############################################################"
echo ""
echo "产出："
echo "  HDFS  /hlm/warehouse/chunks/fuqinju/chunks.jsonl"
echo "  HDFS  /hlm/derived/vectors/fuqinju/model=bge-small-zh-v1.5/vectors.jsonl"
echo "  HDFS  /hlm/mrout/{wordfreq,personfreq,cooccur,chunkstat}"
echo "  HBase hlm_chunks / hlm_vectors / hlm_terms / hlm_wordfreq / hlm_stats"
echo "  Hive  hlm_word_freq / hlm_person_freq / hlm_cooccur / hlm_chunk_stat"
