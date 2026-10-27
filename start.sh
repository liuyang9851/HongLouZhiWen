#!/usr/bin/env bash
# =============================================================
# 红楼智问 · 一键启动
# =============================================================
# 用法：
#   ./start.sh                    # 自动选择最佳方案
#   ./start.sh --with-hdfs        # 强制启动单节点 HDFS（Docker 用户推荐）
#   ./start.sh --local-only       # 纯本地文件（最快，不用 HDFS）
#
# 自动选择逻辑：
#   ① 检测到已有 Hadoop 集群 → 用它（HDFS 模式）
#   ② 没有 Hadoop，但有 Docker → 起单节点 HDFS 容器（--with-hdfs 或交互确认）
#   ③ 都不想 → 本地文件模式
#
# 为什么推荐 HDFS 模式：
#   项目的数据源层从 HDFS 读 JSONL（WebHDFS HTTP，跨版本稳定）。
#   纯本地模式虽然能跑，但体现不出 Hadoop 的使用。
# =============================================================
set -e

cd "$(dirname "$0")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; DIM='\033[2m'; NC='\033[0m'
info()  { echo -e "${GREEN}▸${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }
dim()   { echo -e "${DIM}$1${NC}"; }

MODE_AUTO=1
FORCE_HDFS=0
FORCE_LOCAL=0
for arg in "$@"; do
    case "$arg" in
        --with-hdfs)  FORCE_HDFS=1; MODE_AUTO=0 ;;
        --local-only) FORCE_LOCAL=1; MODE_AUTO=0 ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \?//'
            exit 0 ;;
    esac
done

# -------------------------------------------------------------
# 1) 基础环境
# -------------------------------------------------------------
info "检查基础环境 ..."
command -v docker >/dev/null 2>&1 || { error "未安装 Docker"; exit 1; }
command -v node   >/dev/null 2>&1 || { error "未安装 Node.js（需 >= 20）"; exit 1; }
NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
[ "$NODE_MAJOR" -ge 20 ] || { error "Node.js 版本过低（当前 $(node -v)）"; exit 1; }
info "Docker ✓  Node $(node -v) ✓"

# -------------------------------------------------------------
# 2) .env
# -------------------------------------------------------------
if [ ! -f backend/.env ]; then
    cp backend/.env.example backend/.env
    error "已从模板创建 backend/.env，请填入 DEEPSEEK_API_KEY 后重跑"
    echo "        nano backend/.env"
    exit 1
fi
if grep -q "your_deepseek_api_key_here" backend/.env; then
    error "backend/.env 里的 DEEPSEEK_API_KEY 还是占位符"
    exit 1
fi

# -------------------------------------------------------------
# 3) 决定数据源
# -------------------------------------------------------------
info "选择部署方案 ..."

DATA_SOURCE="local"
WEBHDFS_URL=""
HDFS_BASE="/hlm"
DOCKER_NET=""
HDFS_MANAGED=0     # 1 = 我们自己起的 HDFS 容器

# ---- 3a) 找已有 Hadoop 集群 ----
HADOOP_CONTAINER=""
for name in master namenode hdfs-namenode hdfs; do
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$name"; then
        HADOOP_CONTAINER="$name"; break
    fi
done

if [ "$FORCE_LOCAL" -eq 0 ] && [ -n "$HADOOP_CONTAINER" ]; then
    dim "  发现已有 Hadoop 容器: $HADOOP_CONTAINER"
    NET=$(docker inspect "$HADOOP_CONTAINER" \
        --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null \
        | tr ' ' '\n' | grep -v '^$' | grep -Ei 'cluster|hadoop|lab' | head -1)
    [ -z "$NET" ] && NET=$(docker inspect "$HADOOP_CONTAINER" \
        --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null \
        | tr ' ' '\n' | grep -v '^$' | head -1)

    for port in 9870 50070; do
        if docker exec "$HADOOP_CONTAINER" bash -lc \
            "curl -sf --max-time 5 'http://localhost:$port/webhdfs/v1/?op=LISTSTATUS' >/dev/null 2>&1"; then
            if docker exec "$HADOOP_CONTAINER" bash -lc \
                "hdfs dfs -test -e ${HDFS_BASE}/warehouse/chunks3ver/chunks_3ver.jsonl" 2>/dev/null; then
                DATA_SOURCE="hdfs"
                WEBHDFS_URL="http://${HADOOP_CONTAINER}:${port}/webhdfs/v1"
                DOCKER_NET="$NET"
                info "方案: 使用已有 Hadoop 集群（$HADOOP_CONTAINER:$port）"
            else
                dim "  已有 Hadoop 但没有项目数据，可用 tools/import_to_hdfs.sh 导入"
            fi
            break
        fi
    done
fi

# ---- 3b) 没有可用 Hadoop → 考虑起单节点 HDFS ----
if [ "$DATA_SOURCE" = "local" ] && [ "$FORCE_LOCAL" -eq 0 ]; then
    # 强制模式 or 交互确认
    START_HDFS=0
    if [ "$FORCE_HDFS" -eq 1 ]; then
        START_HDFS=1
    elif [ "$MODE_AUTO" -eq 1 ] && [ -t 0 ]; then
        echo
        echo "  未检测到可用的 Hadoop 集群。"
        echo "  可以直接读本地文件（快，但不体现 Hadoop），"
        echo "  也可以启动一个单节点 HDFS（约 2.5 GB 镜像，首次构建 3~5 分钟）。"
        echo
        read -r -p "  启动单节点 HDFS? [Y/n] " ans
        case "$ans" in
            ""|y|Y|yes|YES) START_HDFS=1 ;;
            *) START_HDFS=0 ;;
        esac
    fi

    if [ "$START_HDFS" -eq 1 ]; then
        info "启动单节点 HDFS ..."

        # 构建镜像（若不存在）
        if ! docker image inspect hlm-hdfs >/dev/null 2>&1; then
            info "构建 HDFS 镜像（首次约 3~5 分钟）..."
            docker build -f hadoop/Dockerfile.hdfs -t hlm-hdfs .
        else
            dim "  镜像已存在，跳过构建"
        fi

        # 创建网络（幂等）
        docker network inspect hlm-net >/dev/null 2>&1 || docker network create hlm-net >/dev/null
        DOCKER_NET="hlm-net"

        # 启动容器（幂等：已存在则复用）
        if docker ps -a --format '{{.Names}}' | grep -qx 'hlm-hdfs'; then
            if [ "$(docker inspect -f '{{.State.Running}}' hlm-hdfs)" = "true" ]; then
                dim "  HDFS 容器已在运行"
            else
                dim "  启动已存在的 HDFS 容器"
                docker start hlm-hdfs >/dev/null
            fi
        else
            # 端口：宿主机映射用 19870/19000，避免和已有集群冲突
            docker run -d --name hlm-hdfs \
                --network hlm-net \
                -p 127.0.0.1:19870:9870 \
                -p 127.0.0.1:19000:9000 \
                -v hlm-hdfs-data:/data \
                -v "$(pwd)/deploy/data:/seed:ro" \
                --restart unless-stopped \
                hlm-hdfs >/dev/null
        fi

        # 等 HDFS 就绪
        info "等待 HDFS 就绪（格式化 + 导入数据，约 30~60 秒）..."
        for i in $(seq 1 60); do
            if docker exec hlm-hdfs bash -lc \
                "hdfs dfs -test -e /hlm/warehouse/chunks3ver/chunks_3ver.jsonl" 2>/dev/null; then
                break
            fi
            sleep 2; printf "."
        done
        echo

        DATA_SOURCE="hdfs"
        WEBHDFS_URL="http://hlm-hdfs:9870/webhdfs/v1"
        HDFS_MANAGED=1
        info "方案: 单节点 HDFS（容器 hlm-hdfs）"
        dim "  WebHDFS UI: http://127.0.0.1:19870"
    fi
fi

# ---- 3c) 最终兜底：本地 ----
if [ "$DATA_SOURCE" = "local" ]; then
    if [ -f deploy/data/chunks_3ver.jsonl ]; then
        info "方案: 本地文件（deploy/data/）"
        dim "  注意：此模式不体现 Hadoop 的使用"
    else
        error "本地数据也不完整：deploy/data/chunks_3ver.jsonl 不存在"
        exit 1
    fi
fi

# -------------------------------------------------------------
# 4) 构建后端镜像
# -------------------------------------------------------------
info "构建后端镜像（首次约 1 分钟）..."
docker build -f backend/Dockerfile.standalone -t hlm-api ./backend

# -------------------------------------------------------------
# 5) 启动后端
# -------------------------------------------------------------
info "启动后端 ..."
docker rm -f hlm-api >/dev/null 2>&1 || true
QA_DIR="$(pwd)/qa_logs"; mkdir -p "$QA_DIR"

NET_ARGS=""
[ -n "$DOCKER_NET" ] && NET_ARGS="--network $DOCKER_NET"

MOUNT_ARGS="-v $QA_DIR:/app/qa_logs"
[ "$DATA_SOURCE" = "local" ] && MOUNT_ARGS="$MOUNT_ARGS -v $(pwd)/deploy/data:/app/data:ro"

# shellcheck disable=SC2086
docker run -d \
    --name hlm-api \
    -p 8000:8000 \
    $NET_ARGS \
    --env-file backend/.env \
    -e HLM_STORAGE=local \
    -e HLM_DATA_SOURCE="$DATA_SOURCE" \
    -e HLM_WEBHDFS_URL="$WEBHDFS_URL" \
    -e HLM_HDFS_BASE="$HDFS_BASE" \
    -e HLM_QA_LOG=/app/qa_logs \
    $MOUNT_ARGS \
    --restart unless-stopped \
    hlm-api >/dev/null

info "等待后端就绪（加载 11,780 chunk + 11,957 向量）..."
for _ in $(seq 1 120); do
    if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then break; fi
    sleep 2; printf "."
done
echo

HEALTH=$(curl -sf http://127.0.0.1:8000/health 2>/dev/null || echo "")
[ -z "$HEALTH" ] && { error "后端启动失败"; echo "  查看: docker logs hlm-api"; exit 1; }

NCHUNK=$(echo "$HEALTH" | grep -o '"n_chunks":[[:space:]]*[0-9-]*' | grep -o '[0-9-]*$')
if [ "$NCHUNK" != "11780" ]; then
    error "数据未正确加载（n_chunks=$NCHUNK，应为 11780）"
    echo
    echo "  排查："
    echo "    1. docker logs hlm-api | head -30"
    echo "    2. docker exec hlm-api python3 -c \"import data_source;print(data_source.describe())\""
    if [ "$DATA_SOURCE" = "local" ]; then
        echo "    3. docker exec hlm-api ls -la /app/data   # 应有 7 个 .jsonl"
    fi
    exit 1
fi

info "后端就绪（$NCHUNK chunk，数据源 $DATA_SOURCE）"

# -------------------------------------------------------------
# 6) 前端
# -------------------------------------------------------------
info "启动前端 ..."
command -v lsof >/dev/null 2>&1 && { lsof -ti:5173 | xargs -r kill -9 2>/dev/null || true; }
export HLM_WEB_PORT=5173
(cd web && nohup node server.js > ../web.log 2>&1 &)
sleep 3
curl -sf http://127.0.0.1:5173/api/health >/dev/null 2>&1 \
    && info "前端就绪" || warn "前端可能未就绪，查看 web.log"

# -------------------------------------------------------------
# 完成
# -------------------------------------------------------------
echo
echo "=============================================="
echo -e "${GREEN}✅ 启动完成${NC}"
echo "=============================================="
echo
echo "  前端界面   http://127.0.0.1:5173"
echo "  后端 API   http://127.0.0.1:8000/docs"
echo
echo "  数据源     $DATA_SOURCE"
[ "$DATA_SOURCE" = "hdfs" ] && echo "             $WEBHDFS_URL"
[ "$DATA_SOURCE" = "local" ] && echo "             deploy/data/（本地文件）"
[ "$HDFS_MANAGED" -eq 1 ] && echo "  HDFS UI    http://127.0.0.1:19870"
echo
echo "  查看日志   docker logs -f hlm-api"
[ "$HDFS_MANAGED" -eq 1 ] && echo "             docker logs -f hlm-hdfs"
echo "  停止服务   docker rm -f hlm-api$( [ "$HDFS_MANAGED" -eq 1 ] && echo ' hlm-hdfs' ) "
echo "=============================================="
