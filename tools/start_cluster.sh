#!/usr/bin/env bash
# =============================================================
# 红楼智问 · 完整集群模式一键启动
# =============================================================
# 用途：重启 Hadoop 集群（master/slave1/slave2）+ hlm-api + 前端
#
# 为什么需要这个脚本：
#   每次容器重启后有两个**必做**的额外步骤，手动跑容易忘：
#     1. HBase Thrift（9090）**不会自动启动** → hlm-api 连不上 HBase
#     2. 宿主机端口 9000 上的旧连接会导致 hlm-api 网络异常
#
# 用法：
#   ./tools/start_cluster.sh              # 完整启动
#   ./tools/start_cluster.sh --status     # 只看状态
#   ./tools/start_cluster.sh --stop       # 停止全部
#
# 前置：
#   · D:\Programming\hadoop\cluster\compose.yaml（集群编排）
#   · backend/.env（DEEPSEEK_API_KEY）
# =============================================================
set -e

PROJ_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# 自动探测集群目录
#   WSL2   → /mnt/d/Programming/hadoop/cluster
#   Git Bash → /d/Programming/hadoop/cluster
#   Linux/macOS 原生 → 用 HLM_CLUSTER_DIR 覆盖
if [ -n "${HLM_CLUSTER_DIR:-}" ]; then
    CLUSTER_DIR="$HLM_CLUSTER_DIR"
elif [ -d /mnt/d/Programming/hadoop/cluster ]; then
    CLUSTER_DIR=/mnt/d/Programming/hadoop/cluster
elif [ -d /d/Programming/hadoop/cluster ]; then
    CLUSTER_DIR=/d/Programming/hadoop/cluster
else
    CLUSTER_DIR=""
fi

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; DIM='\033[2m'; NC='\033[0m'
info()  { echo -e "${GREEN}▸${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }
dim()   { echo -e "${DIM}$1${NC}"; }

# 探测容器内某端口是否可连（netstat 在镜像里不存在，用 python）
port_ok() {
    local ctr=$1 port=$2
    docker exec "$ctr" python3 -c "
import socket,sys
s=socket.socket(); s.settimeout(2)
sys.exit(0 if s.connect_ex(('127.0.0.1',$port))==0 else 1)
" 2>/dev/null
}

status() {
    echo "=== 容器状态 ==="
    docker ps -a --format '  {{.Names}}\t{{.Status}}' | grep -E 'master|slave|hlm-' || echo "  无"
    echo
    echo "=== 关键服务 ==="
    for p in 9870 16010 18088 19888; do
        if curl -sf --max-time 3 "http://127.0.0.1:$p" >/dev/null 2>&1; then
            echo "  ✅ 宿主机 :$p"
        else
            echo "  ❌ 宿主机 :$p"
        fi
    done
    if port_ok master 9090; then echo "  ✅ HBase Thrift :9090"; else echo "  ❌ HBase Thrift :9090"; fi
    echo
    echo "=== RAG 后端 ==="
    curl -s --max-time 5 http://127.0.0.1:8000/health 2>/dev/null \
        | grep -o '"n_chunks":[0-9-]*\|"storage":"[a-z]*"\|"hbase":"[a-z]*"' \
        | sed 's/^/  /' || echo "  ❌ 后端未响应"
    echo
    echo "=== 前端 ==="
    # 注意：若本脚本在 WSL 里跑，WSL 因 NAT 隔离访问不到 Windows 侧的 5173，
    #   所以不能只靠 curl 判断。这里改为「curl 通 或 Windows 侧有 node 进程」。
    if curl -sf --max-time 3 http://127.0.0.1:5173/api/health >/dev/null 2>&1; then
        echo "  ✅ http://127.0.0.1:5173（可访问）"
    elif tasklist.exe 2>/dev/null | grep -qi 'node.exe'; then
        echo "  ⚠ node 进程存在（WSL 内无法验证端口，请在 Windows 浏览器打开）"
        echo "     http://127.0.0.1:5173"
    else
        echo "  ❌ 未运行"
    fi
}

case "${1:-}" in
    --status) status; exit 0 ;;
    --repair-hbase)
        warn "HBase 修复流程较重，见 docs/42-S40H-HBase元数据损坏修复.md"
        echo "        简要步骤："
        echo "          1. docker stop master slave1 slave2"
        echo "          2. docker run --rm -v hadoop-lab_master-data:/data hadoop-lab:2026.10 -c 'rm -rf /data/zookeeper'"
        echo "          3. 用 HLM_NO_HBASE=1 起 master，HLM_NO_REGIONSERVER=1 起 slave"
        echo "          4. hdfs dfs -rm -r -f -skipTrash /hbase"
        echo "          5. 完整模式重启，等 90 秒"
        echo "          6. python3 pipeline/s40_rebuild_tables.py"
        echo "          7. python3 pipeline/s7_2_index_versions.py"
        echo "          8. python3 pipeline/s40_import_pizhu.py"
        echo "          9. python3 pipeline/s3_to_hbase_hive.py"
        exit 0 ;;
    --stop)
        info "停止服务 ..."
        docker rm -f hlm-api 2>/dev/null || true
        pkill -f 'node server.js' 2>/dev/null || true
        if [ -n "$CLUSTER_DIR" ] && [ -d "$CLUSTER_DIR" ]; then
            (cd "$CLUSTER_DIR" && docker compose -f compose.yaml -f compose.override.yaml down) 2>&1 \
                | grep -E 'Removed|Stopped' | sed 's/^/  /' || true
        else
            warn "找不到集群目录，跳过 compose down"
        fi
        info "已停止"
        exit 0 ;;
esac

# -------------------------------------------------------------
# 1) 启动集群
# -------------------------------------------------------------
if [ -z "$CLUSTER_DIR" ] || [ ! -d "$CLUSTER_DIR" ]; then
    error "找不到 Hadoop 集群目录"
    echo "        尝试过的路径："
    echo "          /mnt/d/Programming/hadoop/cluster   (WSL2)"
    echo "          /d/Programming/hadoop/cluster       (Git Bash)"
    echo
    echo "        请用环境变量指定："
    echo "          HLM_CLUSTER_DIR=/your/cluster ./tools/start_cluster.sh"
    exit 1
fi
info "集群目录: $CLUSTER_DIR"

info "启动 Hadoop 集群 ..."
cd "$CLUSTER_DIR"
docker compose -f compose.yaml -f compose.override.yaml up -d 2>&1 \
        | grep -E 'Started|Running' | sed 's/^/  /' || true

# -------------------------------------------------------------
# 2) 等 HDFS 退出 safemode
# -------------------------------------------------------------
info "等待 HDFS 就绪 ..."
for i in $(seq 1 40); do
    if docker exec master bash -lc "hdfs dfsadmin -safemode get 2>/dev/null | tail -1" 2>/dev/null | grep -q OFF; then
        info "HDFS 就绪（$((i*5)) 秒）"
        break
    fi
    [ "$i" -eq 40 ] && { error "HDFS 超时未就绪"; docker logs master --tail 20; exit 1; }
    sleep 5
done

# -------------------------------------------------------------
# 3) 等 HMaster
# -------------------------------------------------------------
info "等待 HBase Master ..."
for i in $(seq 1 30); do
    if docker exec master bash -lc "jps 2>/dev/null" 2>/dev/null | grep -q HMaster; then
        info "HMaster 就绪（$((i*5)) 秒）"
        break
    fi
    [ "$i" -eq 30 ] && { error "HMaster 超时"; exit 1; }
    sleep 5
done

# -------------------------------------------------------------
# 4) 启动 HBase Thrift（关键：不会自动启动）
# -------------------------------------------------------------
info "检查 HBase Thrift ..."
if port_ok master 9090; then
    dim "  已在运行"
else
    info "启动 HBase Thrift（容器重启后不会自动起）..."
    docker exec -d master bash -lc "hbase thrift start > /logs/hbase-thrift.log 2>&1"
    for i in $(seq 1 20); do
        sleep 5
        if port_ok master 9090; then
            info "Thrift 就绪（$((i*5)) 秒）"
            break
        fi
        [ "$i" -eq 20 ] && { error "Thrift 启动超时"; docker exec master tail -20 /logs/hbase-thrift.log; exit 1; }
    done
fi

# -------------------------------------------------------------
# 5) 重启 RAG 后端（HBase 模式）
# -------------------------------------------------------------
info "启动 RAG 后端（HBase 模式）..."
docker rm -f hlm-api >/dev/null 2>&1 || true
QA_DIR="$PROJ_DIR/qa_logs"; mkdir -p "$QA_DIR"

docker run -d --name hlm-api \
    -p 8000:8000 \
    --network hadoop-lab_cluster \
    --env-file "$PROJ_DIR/backend/.env" \
    -e HLM_STORAGE=hbase \
    -e HLM_HBASE_HOST=master \
    -e HLM_HBASE_PORT=9090 \
    -e HLM_QA_LOG=/app/qa_logs \
    -v "$PROJ_DIR/pipeline/out:/workspace/pipeline/out" \
    -v "$QA_DIR:/app/qa_logs" \
    --restart unless-stopped \
    hlm-rag-api:12.0 >/dev/null

info "等待后端加载 11,932 chunk ..."
for i in $(seq 1 40); do
    N=$(curl -s --max-time 3 http://127.0.0.1:8000/health 2>/dev/null | grep -o '"n_chunks":[0-9-]*' | grep -o '[0-9-]*$')
    if [ "$N" = "11932" ]; then
        info "后端就绪（$((i*3)) 秒，11932 chunk）"
        break
    fi
    [ "$i" -eq 40 ] && { error "后端超时（n_chunks=$N）"; docker logs hlm-api --tail 20; exit 1; }
    sleep 3
done

# -------------------------------------------------------------
# 6) 启动前端
# -------------------------------------------------------------
info "启动前端 ..."
pkill -f 'node server.js' 2>/dev/null || true
sleep 1
(cd "$PROJ_DIR/web" && HLM_WEB_PORT=5173 nohup node server.js > "$PROJ_DIR/web.log" 2>&1 &)
sleep 4
if curl -sf --max-time 3 http://127.0.0.1:5173/api/health >/dev/null 2>&1; then
    info "前端就绪"
elif tasklist.exe 2>/dev/null | grep -qi 'node.exe'; then
    info "前端已启动（WSL 内无法验证端口，请在浏览器确认）"
else
    warn "前端可能未就绪，查看 web.log"
fi

# -------------------------------------------------------------
# 完成
# -------------------------------------------------------------
echo
echo "=============================================="
echo -e "${GREEN}✅ 完整集群模式已启动${NC}"
echo "=============================================="
echo
echo "  前端界面    http://127.0.0.1:5173"
echo "  后端 API    http://127.0.0.1:8000/docs"
echo
echo "  HDFS UI     http://127.0.0.1:9870"
echo "  HBase UI    http://127.0.0.1:16010"
echo "  YARN UI     http://127.0.0.1:18088"
echo "  JobHistory  http://127.0.0.1:19888"
echo
echo "  存储模式    HBase（完整集群）"
echo "  状态检查    ./tools/start_cluster.sh --status"
echo "  停止全部    ./tools/start_cluster.sh --stop"
echo "=============================================="
