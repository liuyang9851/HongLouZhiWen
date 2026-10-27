#!/usr/bin/env bash
# =============================================================
# 单节点 HDFS 启动脚本
# =============================================================
# 在同一个容器里同时跑 NameNode + DataNode（伪分布式）。
#
# 启动顺序：
#   1. 首次启动 → format namenode
#   2. 启动 namenode
#   3. 等 namenode 退出 safe mode
#   4. 启动 datanode
#   5. 创建项目需要的 HDFS 目录
#   6. 前台 wait，保持容器存活
# =============================================================
set -euo pipefail

export HADOOP_LOG_DIR=/logs/hadoop
export HADOOP_PID_DIR=/tmp/hadoop-pids
mkdir -p "$HADOOP_LOG_DIR" "$HADOOP_PID_DIR" /data

log() { echo "[hdfs] $(date '+%H:%M:%S') $*"; }

# ---- 1) 首次启动要 format ----
if [ ! -f /data/namenode/current/VERSION ]; then
    log "首次启动，格式化 NameNode ..."
    mkdir -p /data/namenode /data/datanode
    hdfs namenode -format -nonInteractive >/logs/format.log 2>&1 \
        || { log "格式化失败，见 /logs/format.log"; cat /logs/format.log; exit 1; }
    log "格式化完成"
else
    log "已有 NameNode 数据，跳过格式化"
fi

mkdir -p /data/datanode

# ---- 2) 启动 NameNode ----
log "启动 NameNode ..."
nohup hdfs namenode > /logs/namenode.log 2>&1 &
NN_PID=$!

# ---- 3) 等 safe mode 退出 ----
log "等待 NameNode 退出 safe mode ..."
for i in $(seq 1 60); do
    if hdfs dfsadmin -safemode get 2>/dev/null | grep -q "OFF"; then
        log "safe mode OFF（等待 $((i*2)) 秒）"
        break
    fi
    if ! kill -0 $NN_PID 2>/dev/null; then
        log "NameNode 进程已退出，见 /logs/namenode.log"
        tail -40 /logs/namenode.log
        exit 1
    fi
    sleep 2
done

# 二次确认：WebHDFS 可访问
for i in $(seq 1 30); do
    if curl -sf --max-time 3 "http://127.0.0.1:9870/webhdfs/v1/?op=LISTSTATUS" >/dev/null 2>&1; then
        log "WebHDFS 就绪"
        break
    fi
    sleep 2
done

# ---- 4) 启动 DataNode ----
log "启动 DataNode ..."
nohup hdfs datanode > /logs/datanode.log 2>&1 &
DN_PID=$!

sleep 5

# 检查 DataNode 是否注册上
LIVE=$(hdfs dfsadmin -report 2>/dev/null | grep -c "Live datanodes" || echo 0)
log "DataNode 状态: $LIVE 项"

# ---- 5) 创建项目需要的目录 ----
log "创建 HDFS 目录 ..."
hdfs dfs -mkdir -p /tmp 2>/dev/null || true
hdfs dfs -chmod 1777 /tmp 2>/dev/null || true
hdfs dfs -mkdir -p \
    /hlm/warehouse/chunks3ver \
    /hlm/warehouse/pizhu3src \
    /hlm/derived/vectors3ver/model=bge-small-zh-v1.5 \
    /hlm/logs 2>/dev/null || true

# ---- 6) 可选：自动导入数据（挂载 /seed 时生效）----
if [ -d /seed ] && [ -f /seed/chunks_3ver.jsonl ]; then
    if ! hdfs dfs -test -e /hlm/warehouse/chunks3ver/chunks_3ver.jsonl 2>/dev/null; then
        log "检测到 /seed 数据，自动导入 ..."
        [ -f /seed/chunks_3ver.jsonl ] && \
            hdfs dfs -put -f /seed/chunks_3ver.jsonl /hlm/warehouse/chunks3ver/ && \
            log "  chunks_3ver.jsonl ✓"
        [ -f /seed/pizhu_3src.jsonl ] && \
            hdfs dfs -put -f /seed/pizhu_3src.jsonl /hlm/warehouse/pizhu3src/ && \
            log "  pizhu_3src.jsonl ✓"
        VDIR=/hlm/derived/vectors3ver/model=bge-small-zh-v1.5
        [ -f /seed/vectors_3ver.jsonl ] && \
            hdfs dfs -put -f /seed/vectors_3ver.jsonl "$VDIR/" && \
            log "  vectors_3ver.jsonl ✓"
        [ -f /seed/pizhu_3src_vectors.jsonl ] && \
            hdfs dfs -put -f /seed/pizhu_3src_vectors.jsonl "$VDIR/" && \
            log "  pizhu_3src_vectors.jsonl ✓"
        log "数据导入完成"
    else
        log "HDFS 已有数据，跳过导入"
    fi
fi

log "HDFS 就绪"
log "  WebHDFS : http://localhost:9870/webhdfs/v1"
log "  RPC     : hdfs://localhost:9000"
hdfs dfs -ls /hlm 2>/dev/null || true

# ---- 7) 前台等待，容器保持运行 ----
trap 'log "收到退出信号"; kill $NN_PID $DN_PID 2>/dev/null || true; exit 0' TERM INT
wait -n $NN_PID $DN_PID
