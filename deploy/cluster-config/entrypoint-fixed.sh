#!/usr/bin/env bash
# =============================================================
# 集群启动脚本（S40 修复版）
# =============================================================
# 原版缺陷：
#   wait -n "${children[@]}"   ← 任意一个子进程退出就 kill 整个容器
#
# 导致的问题（实测）：
#   master 的 HBase Master 因 WAL 块缺失崩溃
#     → master 容器退出
#     → slave 的 RegionServer 解析不到 master 崩溃
#     → slave 容器退出
#     → 0 个 DataNode → HDFS 全块 MISSING
#     → master 的 HBase 又崩溃 ……无限循环
#
# 修复策略：
#   1. **区分致命与非致命**：只有 NameNode/HMaster 这类核心服务退出才让容器退出，
#      Spark Worker / YARN NodeManager 等边缘服务挂掉只记日志、不杀容器
#   2. **自动重启边缘服务**：挂了就重拉，最多 3 次
#   3. **启动前等待依赖**：slave 启动前确认 master 的 9000/2181 真的可连
#      （原来只有 nc 循环，DNS 未就绪时 nc 返回非 0 不会退循环，但会空转）
# =============================================================
set -uo pipefail

export HADOOP_LOG_DIR=/logs/hadoop HBASE_LOG_DIR=/logs/hbase
export HADOOP_PID_DIR=/tmp/hadoop-pids HBASE_PID_DIR=/tmp/hbase-pids
export HBASE_HEAPSIZE=512

mkdir -p "$HADOOP_LOG_DIR" "$HBASE_LOG_DIR" "$HADOOP_PID_DIR" "$HBASE_PID_DIR" /data

log() { echo "[cluster] $(date '+%H:%M:%S') $*"; }

# -------------------------------------------------------------
# 工具函数
# -------------------------------------------------------------
# 等待主机端口可连（带 DNS 重试，最多 180 秒）
wait_for() {
    local host=$1 port=$2 name=$3 timeout=${4:-180}
    local n=0
    while [ $n -lt "$timeout" ]; do
        # 先确认 DNS 能解析（容器刚起时 Docker DNS 可能未就绪）
        if getent hosts "$host" >/dev/null 2>&1; then
            if nc -z "$host" "$port" 2>/dev/null; then
                log "  $name 就绪（${host}:${port}，${n}s）"
                return 0
            fi
        fi
        n=$((n+2)); sleep 2
    done
    log "  ⚠ $name 等待超时（${host}:${port}）"
    return 1
}

# 启动一个服务并记录 PID 到全局数组
declare -a CRIT_PIDS=()    # 核心服务：死了容器要退
declare -a OPT_PIDS=()     # 边缘服务：死了自动重拉
declare -a OPT_CMDS=()

start_crit() {
    local name=$1; shift
    log "启动核心服务: $name"
    "$@" >> "/logs/${name}.log" 2>&1 &
    CRIT_PIDS+=($!)
}

start_opt() {
    local name=$1; shift
    log "启动边缘服务: $name"
    "$@" >> "/logs/${name}.log" 2>&1 &
    OPT_PIDS+=($!)
    OPT_CMDS+=("$*")
}

cleanup() {
    log "收到退出信号，停止所有服务"
    kill "${CRIT_PIDS[@]}" "${OPT_PIDS[@]}" 2>/dev/null || true
    wait 2>/dev/null || true
    exit 0
}
trap cleanup TERM INT

# =============================================================
# MASTER
# =============================================================
if [ "$HOSTNAME" = "master" ]; then
    # ---- HDFS ----
    if [ ! -f /data/namenode/current/VERSION ]; then
        log "首次启动，格式化 NameNode ..."
        hdfs namenode -format -nonInteractive > /logs/format.log 2>&1 || {
            log "格式化失败"; cat /logs/format.log; exit 1
        }
    fi
    start_crit namenode hdfs namenode
    for i in $(seq 1 60); do
        if hdfs dfsadmin -safemode get 2>/dev/null | grep -q OFF; then
            log "HDFS 退出 safe mode（$((i*2))s）"; break
        fi
        sleep 2
    done
    hdfs dfs -mkdir -p /tmp /user/root /spark-events 2>/dev/null || true
    hdfs dfs -chmod 1777 /tmp 2>/dev/null || true

    # ---- YARN / JobHistory ----
    start_opt yarn-resourcemanager yarn resourcemanager
    start_opt mapred-historyserver mapred historyserver

    # ---- ZooKeeper ----
    mkdir -p /data/zookeeper
    printf 'tickTime=2000\ndataDir=/data/zookeeper\nclientPort=2181\nadmin.enableServer=false\n' \
        > /opt/zookeeper/conf/zoo.cfg
    start_crit zookeeper /opt/zookeeper/bin/zkServer.sh start-foreground

    # 等 ZK 就绪再起 HBase
    wait_for localhost 2181 "ZooKeeper" 60

    # ---- HBase Master ----
    start_crit hbase-master hbase master start

    # ---- Spark Master ----
    start_opt spark-master /opt/spark/bin/spark-class \
        org.apache.spark.deploy.master.Master \
        --host master --port 7077 --webui-port 8080

    # ---- HBase Thrift（9090）----
    # 重要：Thrift 不是 HBase Master 的子进程，容器重启后**不会自动启动**。
    #   项目后端靠 happybase 连 9090，没它就全部检索失败。
    #   这里加一个后台守护：每 30 秒检查一次，不在就拉起。
    (
        while true; do
            sleep 30
            if ! nc -z localhost 9090 2>/dev/null; then
                log "  Thrift 9090 未监听，自动拉起"
                nohup hbase thrift start > /logs/hbase-thrift.log 2>&1 &
                sleep 25
            fi
        done
    ) &
    log "  HBase Thrift 守护已启动（30 秒检查一次）"

# =============================================================
# SLAVE
# =============================================================
else
    # 关键：必须等 master 的 HDFS + ZK 真的可连
    #   原版只有 `until nc -z master 9000`，DNS 未就绪时空转但不出错；
    #   现在显式检查 DNS + 端口，且分两步等（先 9000 再 2181）
    wait_for master 9000 "master HDFS" 180 || {
        log "⚠ 等不到 master HDFS，仍尝试启动（HDFS 会自动重连）"
    }

    start_crit datanode hdfs datanode
    start_opt yarn-nodemanager yarn nodemanager

    wait_for master 2181 "master ZooKeeper" 180 || {
        log "⚠ 等不到 master ZK，跳过 HBase RegionServer"
    }

    # RegionServer 依赖 ZK，ZK 通才启
    if nc -z master 2181 2>/dev/null; then
        start_crit hbase-regionserver hbase regionserver start
    fi

    # Spark Worker（边缘服务）
    if nc -z master 7077 2>/dev/null; then
        start_opt spark-worker /opt/spark/bin/spark-class \
            org.apache.spark.deploy.worker.Worker \
            --cores 1 --memory 1g --webui-port 8081 spark://master:7077
    fi
fi

log "所有服务已启动，进入监控循环"
log "  核心服务 PID: ${CRIT_PIDS[*]:-无}"
log "  边缘服务 PID: ${OPT_PIDS[*]:-无}"

# -------------------------------------------------------------
# 监控循环（替换原来的 wait -n）
# -------------------------------------------------------------
# 行为：
#   · 核心服务退出 → 容器退出（让 Docker restart policy 重启整个容器）
#   · 边缘服务退出 → 记录日志 + 尝试重拉（最多 3 次）
# -------------------------------------------------------------
declare -A RESTART_COUNT=()

while true; do
    sleep 5

    # 检查核心服务
    for pid in "${CRIT_PIDS[@]}"; do
        if ! kill -0 "$pid" 2>/dev/null; then
            log "✗ 核心服务（PID $pid）已退出 → 容器退出，等待 Docker 重启"
            kill "${CRIT_PIDS[@]}" "${OPT_PIDS[@]}" 2>/dev/null || true
            exit 1
        fi
    done

    # 检查边缘服务并重拉
    for i in "${!OPT_PIDS[@]}"; do
        pid="${OPT_PIDS[$i]}"
        if ! kill -0 "$pid" 2>/dev/null; then
            cmd="${OPT_CMDS[$i]}"
            cnt="${RESTART_COUNT[$i]:-0}"
            if [ "$cnt" -lt 3 ]; then
                log "⚠ 边缘服务退出（PID $pid），第 $((cnt+1)) 次重拉: $cmd"
                eval "$cmd" >> "/logs/opt-restart-$i.log" 2>&1 &
                OPT_PIDS[$i]=$!
                RESTART_COUNT[$i]=$((cnt+1))
            else
                log "⚠ 边缘服务（$cmd）已重试 3 次，放弃"
                OPT_PIDS[$i]=0    # 标记为已放弃，不再检查
            fi
        fi
    done
done
