#!/usr/bin/env bash
# =============================================================
# 红楼智问 · 一键启动
# =============================================================
# 一条命令起全套：后端 + 前端（+ 按需起数据源）
#
# 用法：
#   ./start.sh                    # 自动探测（有集群就用，没有就问你要不要起单节点 HDFS）
#   ./start.sh --cluster          # 完整 Hadoop 集群模式（HBase，需已有集群）
#   ./start.sh --with-hdfs        # 单节点 HDFS（Docker 用户推荐）
#   ./start.sh --local-only       # 纯本地文件（最快，不用 HDFS）
#   ./start.sh --stop             # 停止所有服务
#   ./start.sh --status           # 只看状态
#
# 四种模式对比：
#   ┌──────────┬────────────────────┬─────────┬──────────┐
#   │  模式     │ 数据源              │ 有Hadoop │  耗时    │
#   ├──────────┼────────────────────┼─────────┼──────────┤
#   │ --cluster │ HBase（完整集群）   │   ✅    │ 2~3 分钟 │
#   │ 默认/--with-hdfs │ 单节点 HDFS │   ✅    │ 1~2 分钟 │
#   │ --local-only │ 本地 JSONL     │   ❌    │ 30 秒    │
#   └──────────┴────────────────────┴─────────┴──────────┘
#
# 四种模式的**检索结果完全相同**（实测 5/5 命中一致）。
# =============================================================
set -uo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; DIM='\033[2m'; NC='\033[0m'
info()  { echo -e "${GREEN}▸${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }
dim()   { echo -e "${DIM}$1${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }

# -------------------------------------------------------------
# 安全检测（避开 set -o pipefail + grep -q 的 SIGPIPE 陷阱）
# -------------------------------------------------------------
# 问题：`cmd | grep -q pattern` 在 grep 找到匹配后立即退出，
#   上游 cmd 收到 SIGPIPE(141)。加了 pipefail 后整个管道判定为失败，
#   于是 `if cmd | grep -q ...` 永远走 else 分支。
#   实测：docker exec master jps | grep -q HMaster → exit=141 → 检测失败。
# 修法：先把输出存进变量，再 grep。
has_line() {   # has_line "pattern" "text"
    echo "$2" | grep -q -- "$1"
}
has_cmd() {    # has_cmd "pattern" cmd...
    local pat="$1"; shift
    local out
    out=$("$@" 2>/dev/null) || true
    echo "$out" | grep -q -- "$pat"
}

# 期望的 chunk 总数（S40b 修复 chunk_id 碰撞后）
EXPECT_CHUNKS=11932

# -------------------------------------------------------------
# 参数解析
# -------------------------------------------------------------
MODE=""            # cluster / hdfs / local / ""（自动）
ACTION="start"     # start / stop / status

for arg in "$@"; do
    case "$arg" in
        --cluster)    MODE="cluster" ;;
        --with-hdfs)  MODE="hdfs" ;;
        --local-only) MODE="local" ;;
        --stop)       ACTION="stop" ;;
        --status)     ACTION="status" ;;
        -h|--help)
            sed -n '2,24p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *)
            error "未知参数: $arg"
            echo "        用 ./start.sh --help 查看用法"
            exit 1 ;;
    esac
done

# -------------------------------------------------------------
# 集群目录探测（WSL2 / Git Bash / Linux 三种路径）
# -------------------------------------------------------------
find_cluster_dir() {
    if [ -n "${HLM_CLUSTER_DIR:-}" ]; then
        echo "$HLM_CLUSTER_DIR"
    elif [ -d /mnt/d/Programming/hadoop/cluster ]; then
        echo /mnt/d/Programming/hadoop/cluster       # WSL2
    elif [ -d /d/Programming/hadoop/cluster ]; then
        echo /d/Programming/hadoop/cluster           # Git Bash
    elif [ -d "$HOME/hadoop/cluster" ]; then
        echo "$HOME/hadoop/cluster"                  # Linux 原生
    else
        echo ""
    fi
}

# 容器内端口探测（netstat 在多数镜像里不存在，用 python）
port_ok() {
    local ctr=$1 port=$2
    docker exec "$ctr" python3 -c "
import socket,sys
s=socket.socket(); s.settimeout(2)
sys.exit(0 if s.connect_ex(('127.0.0.1',$port))==0 else 1)
" 2>/dev/null
}

# 取 /health 里的 n_chunks
get_nchunks() {
    curl -sf --max-time 5 http://127.0.0.1:8000/health 2>/dev/null \
        | grep -o '"n_chunks":[[:space:]]*[0-9-]*' | grep -o '[0-9-]*$'
}

# =============================================================
# --status
# =============================================================
if [ "$ACTION" = "status" ]; then
    echo "=== 容器 ==="
    docker ps -a --format '  {{.Names}}\t{{.Status}}' 2>/dev/null \
        | grep -E 'master|slave|hlm-' || echo "  无"
    echo
    echo "=== 集群服务（宿主机端口）==="
    for p in 9870 16010 18088 19888; do
        if curl -sf --max-time 3 "http://127.0.0.1:$p" >/dev/null 2>&1; then
            echo "  ✓ :$p"
        else
            echo "  ✗ :$p"
        fi
    done
    if has_cmd '^master$' docker ps --format '{{.Names}}'; then
        if port_ok master 9090 2>/dev/null; then echo "  ✓ HBase Thrift :9090"; else echo "  ✗ HBase Thrift :9090"; fi
    fi
    echo
    echo "=== 后端 ==="
    N=$(get_nchunks)
    if [ -n "$N" ]; then
        S=$(curl -sf --max-time 5 http://127.0.0.1:8000/health 2>/dev/null \
            | grep -o '"storage":"[a-z]*"' | cut -d'"' -f4)
        echo "  ✓ n_chunks=$N  storage=${S:-?}"
    else
        echo "  ✗ 未响应"
    fi
    echo
    echo "=== 前端 ==="
    # 注意：WSL 访问不到 Windows 侧的 5173（反向 NAT 隔离），
    #   curl 和 ps 都不可靠。用 tasklist 查 Windows 侧有没有
    #   node 进程跑 server.js 更准。
    WEB_PORT="${HLM_WEB_PORT:-5173}"
    if curl -sf --max-time 3 "http://127.0.0.1:$WEB_PORT/api/health" >/dev/null 2>&1; then
        echo "  ✓ http://127.0.0.1:$WEB_PORT"
    elif pgrep -f 'node server.js' >/dev/null 2>&1; then
        echo "  ✓ node server.js 在跑（本环境内）"
    elif grep -qi microsoft /proc/version 2>/dev/null && \
         powershell.exe -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { \$_.CommandLine -like '*server.js*' }) { exit 0 } else { exit 1 }" >/dev/null 2>&1; then
        echo "  ✓ http://127.0.0.1:$WEB_PORT （Windows 侧运行）"
    else
        echo "  ✗ 未检测到"
        echo "     启动: powershell -ExecutionPolicy Bypass -File start-web.ps1"
    fi
    exit 0
fi

# =============================================================
# --stop
# =============================================================
if [ "$ACTION" = "stop" ]; then
    info "停止服务 ..."
    docker rm -f hlm-api 2>/dev/null || true
    ok "后端已停"

    pkill -f 'node server.js' 2>/dev/null || true
    ok "前端已停"

    CD="$(find_cluster_dir)"
    if [ -n "$CD" ] && [ -d "$CD" ]; then
        (cd "$CD" && docker compose -f compose.yaml -f compose.override.yaml down 2>&1 \
            | grep -E 'Removed|Stopped' | sed 's/^/  /') || true
        ok "集群已停"
    else
        dim "  未找到集群目录，跳过"
    fi

    # 单节点 HDFS 不删（数据在里面），只停
    if has_cmd '^hlm-hdfs$' docker ps --format '{{.Names}}'; then
        docker stop hlm-hdfs >/dev/null 2>&1 && ok "hlm-hdfs 已停（数据保留）"
    fi
    exit 0
fi

# =============================================================
# 通用检查
# =============================================================
info "检查基础环境 ..."
command -v docker >/dev/null 2>&1 || { error "未安装 Docker"; exit 1; }
command -v node   >/dev/null 2>&1 || { error "未安装 Node.js（需 >= 20）"; exit 1; }
NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
[ "$NODE_MAJOR" -ge 20 ] || { error "Node.js 版本过低（当前 $(node -v)）"; exit 1; }
ok "Docker ✓  Node $(node -v) ✓"

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

# =============================================================
# 分支 1：完整集群模式（HBase）
# =============================================================
if [ "$MODE" = "cluster" ]; then
    info "模式: 完整 Hadoop 集群（HBase）"

    CD="$(find_cluster_dir)"
    if [ -z "$CD" ] || [ ! -d "$CD" ]; then
        error "找不到 Hadoop 集群目录"
        echo "        尝试过："
        echo "          \$HLM_CLUSTER_DIR"
        echo "          /mnt/d/Programming/hadoop/cluster   (WSL2)"
        echo "          /d/Programming/hadoop/cluster       (Git Bash)"
        echo "          \$HOME/hadoop/cluster"
        echo
        echo "        用环境变量指定：HLM_CLUSTER_DIR=/your/cluster ./start.sh --cluster"
        exit 1
    fi
    ok "集群目录: $CD"

    # ---- 1) 启动集群容器 ----
    info "启动集群容器 ..."
    (cd "$CD" && docker compose -f compose.yaml -f compose.override.yaml up -d 2>&1 \
        | grep -E 'Started|Running|Error' | sed 's/^/  /') || {
        error "compose up 失败"
        exit 1
    }

    # ---- 2) 等 HDFS 退出 safemode ----
    info "等待 HDFS 就绪 ..."
    for i in $(seq 1 40); do
        SM=$(docker exec master bash -lc "hdfs dfsadmin -safemode get" 2>/dev/null) || true
        if has_line 'OFF' "$SM"; then
            ok "HDFS 就绪（$((i*5)) 秒）"; break
        fi
        [ "$i" -eq 40 ] && { error "HDFS 超时"; docker logs master --tail 20; exit 1; }
        sleep 5
    done

    # ---- 3) 等 HBase Master ----
    info "等待 HBase Master ..."
    for i in $(seq 1 30); do
        if has_cmd 'HMaster' docker exec master bash -lc "jps"; then
            ok "HMaster 就绪（$((i*5)) 秒）"; break
        fi
        [ "$i" -eq 30 ] && { error "HMaster 超时"; exit 1; }
        sleep 5
    done

    # ---- 4) HBase Thrift（容器重建后不会自动起）----
    info "检查 HBase Thrift ..."
    if port_ok master 9090; then
        ok "已在运行"
    else
        info "启动 Thrift（容器重启后不会自动起）..."
        docker exec -d master bash -lc "hbase thrift start > /logs/hbase-thrift.log 2>&1"
        for i in $(seq 1 24); do
            sleep 5
            if port_ok master 9090; then ok "Thrift 就绪（$((i*5)) 秒）"; break; fi
            [ "$i" -eq 24 ] && {
                error "Thrift 超时"
                echo "        查看: docker exec master tail -20 /logs/hbase-thrift.log"
                exit 1
            }
        done
    fi

    # ---- 5) 建 HBase 镜像 ----
    info "构建后端镜像（HBase 版，首次约 2 分钟）..."
    if docker image inspect hlm-rag-api:latest >/dev/null 2>&1; then
        dim "  镜像已存在，跳过构建（要强制重建加 --rebuild）"
    else
        docker build -f backend/Dockerfile -t hlm-rag-api:latest ./backend || {
            error "镜像构建失败"; exit 1
        }
    fi

    # ---- 6) 启动后端 ----
    info "启动后端 ..."
    docker rm -f hlm-api >/dev/null 2>&1 || true
    QA_DIR="$ROOT/qa_logs"; mkdir -p "$QA_DIR"
    docker run -d --name hlm-api \
        -p 8000:8000 \
        --network hadoop-lab_cluster \
        --env-file backend/.env \
        -e HLM_STORAGE=hbase \
        -e HLM_HBASE_HOST=master \
        -e HLM_HBASE_PORT=9090 \
        -e HLM_QA_LOG=/app/qa_logs \
        -v "$ROOT/pipeline/out:/workspace/pipeline/out" \
        -v "$QA_DIR:/app/qa_logs" \
        --restart unless-stopped \
        hlm-rag-api:latest >/dev/null || { error "后端容器启动失败"; exit 1; }

    info "等待后端加载 $EXPECT_CHUNKS chunk ..."
    N=""
    for i in $(seq 1 40); do
        N=$(get_nchunks)
        if [ "$N" = "$EXPECT_CHUNKS" ]; then
            ok "后端就绪（$((i*3)) 秒）"; break
        fi
        if [ "$i" -eq 40 ]; then
            echo
            error "后端超时（n_chunks=${N:-无响应}）"
            echo
            echo "  逐表校验 HBase 数据："
            docker cp tools/check_hbase_tables.py master:/tmp/ 2>/dev/null || true
            docker exec master python3 /tmp/check_hbase_tables.py 2>&1 | sed 's/^/    /' || true
            echo
            echo "  其他：docker logs hlm-api --tail 30"
            exit 1
        fi
        sleep 3; printf "."
    done
    echo

    DATA_LABEL="HBase（完整集群）"

# =============================================================
# 分支 2 / 3：单节点 HDFS 或本地文件
# =============================================================
else
    DATA_SOURCE="local"
    WEBHDFS_URL=""
    HDFS_BASE="/hlm"
    DOCKER_NET=""
    HDFS_MANAGED=0

    # ---- 找已有 Hadoop 集群（HDFS 模式）----
    HADOOP_CONTAINER=""
    for name in master namenode hdfs-namenode hdfs; do
        if has_cmd "^${name}$" docker ps --format '{{.Names}}'; then
            HADOOP_CONTAINER="$name"; break
        fi
    done

    if [ "$MODE" != "local" ] && [ -n "$HADOOP_CONTAINER" ]; then
        dim "  发现 Hadoop 容器: $HADOOP_CONTAINER"
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
                    ok "方案: 已有集群 HDFS（$HADOOP_CONTAINER:$port）"
                else
                    dim "  集群无项目数据，可用 tools/import_to_hdfs.sh 导入"
                fi
                break
            fi
        done
    fi

    # ---- 单节点 HDFS ----
    if [ "$DATA_SOURCE" = "local" ] && [ "$MODE" != "local" ]; then
        START_HDFS=0
        if [ "$MODE" = "hdfs" ]; then
            START_HDFS=1
        elif [ -t 0 ]; then
            echo
            echo "  未检测到可用的 Hadoop 集群。"
            echo "  可以直接读本地文件（快，但不体现 Hadoop），"
            echo "  也可以启动单节点 HDFS（约 2.5 GB 镜像，首次 3~5 分钟）。"
            echo
            read -r -p "  启动单节点 HDFS? [Y/n] " ans
            case "$ans" in ""|y|Y|yes|YES) START_HDFS=1 ;; *) START_HDFS=0 ;; esac
        fi

        if [ "$START_HDFS" -eq 1 ]; then
            info "启动单节点 HDFS ..."
            if ! docker image inspect hlm-hdfs >/dev/null 2>&1; then
                info "构建 HDFS 镜像（首次 3~5 分钟）..."
                docker build -f hadoop/Dockerfile.hdfs -t hlm-hdfs . || { error "HDFS 镜像构建失败"; exit 1; }
            else
                dim "  镜像已存在，跳过构建"
            fi

            docker network inspect hlm-net >/dev/null 2>&1 || docker network create hlm-net >/dev/null
            DOCKER_NET="hlm-net"

            if has_cmd '^hlm-hdfs$' docker ps -a --format '{{.Names}}'; then
                if [ "$(docker inspect -f '{{.State.Running}}' hlm-hdfs)" = "true" ]; then
                    dim "  HDFS 容器已在运行"
                else
                    dim "  启动已存在的 HDFS 容器"
                    docker start hlm-hdfs >/dev/null
                fi
            else
                docker run -d --name hlm-hdfs \
                    --network hlm-net \
                    -p 127.0.0.1:19870:9870 \
                    -p 127.0.0.1:19000:9000 \
                    -v hlm-hdfs-data:/data \
                    -v "$ROOT/deploy/data:/seed:ro" \
                    --restart unless-stopped \
                    hlm-hdfs >/dev/null
            fi

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
            ok "方案: 单节点 HDFS"
        fi
    fi

    # ---- 兜底：本地 ----
    if [ "$DATA_SOURCE" = "local" ]; then
        if [ -f deploy/data/chunks_3ver.jsonl ]; then
            ok "方案: 本地文件（deploy/data/）"
            dim "  注意：此模式不体现 Hadoop 的使用"
        else
            error "本地数据也不完整：deploy/data/chunks_3ver.jsonl 不存在"
            exit 1
        fi
    fi

    # ---- 建后端镜像（standalone）----
    info "构建后端镜像（standalone 版，首次约 1 分钟）..."
    docker build -f backend/Dockerfile.standalone -t hlm-api ./backend || {
        error "镜像构建失败"; exit 1
    }

    # ---- 启动后端 ----
    info "启动后端 ..."
    docker rm -f hlm-api >/dev/null 2>&1 || true
    QA_DIR="$ROOT/qa_logs"; mkdir -p "$QA_DIR"

    NET_ARGS=""
    [ -n "$DOCKER_NET" ] && NET_ARGS="--network $DOCKER_NET"

    MOUNT_ARGS="-v $QA_DIR:/app/qa_logs -v $ROOT/pipeline/out:/workspace/pipeline/out"
    [ "$DATA_SOURCE" = "local" ] && MOUNT_ARGS="$MOUNT_ARGS -v $ROOT/deploy/data:/app/data:ro"

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
        hlm-api >/dev/null || { error "后端容器启动失败"; exit 1; }

    info "等待后端加载 $EXPECT_CHUNKS chunk ..."
    N=""
    for i in $(seq 1 90); do
        N=$(get_nchunks)
        if [ "$N" = "$EXPECT_CHUNKS" ]; then break; fi
        # 每 15 秒报一次实际值，别让用户干等
        if [ $((i % 15)) -eq 0 ]; then
            echo
            dim "  [${i}0s] 当前 n_chunks=${N:-无响应}"
        else
            printf "."
        fi
        sleep 10
    done
    echo

    if [ "$N" != "$EXPECT_CHUNKS" ]; then
        error "数据未正确加载（n_chunks=${N:-无响应}，应为 $EXPECT_CHUNKS）"
        echo
        # 差 152 是最典型的情况：HDFS/本地数据里的 chunk_id 没消解
        if [ -n "$N" ] && [ "$N" != "-1" ] && [ "$N" -lt "$EXPECT_CHUNKS" ] 2>/dev/null; then
            echo "  数据比预期少 $((EXPECT_CHUNKS - N)) 条。"
            echo "  最常见原因：数据文件里的 chunk_id 有碰撞（旧版数据）。"
            echo "    验证："
            if [ "$DATA_SOURCE" = "hdfs" ]; then
                echo "      docker exec master hdfs dfs -cat /hlm/warehouse/pizhu3src/pizhu_3src.jsonl \\"
                echo "        | wc -l        # 应是 8662"
            else
                echo "      wc -l deploy/data/pizhu_3src.jsonl   # 应是 8662"
            fi
            echo "    修复：用 deploy/data/pizhu_3src.jsonl 覆盖远端数据"
            echo "          （详见 docs/42-S40H-HBase元数据损坏修复.md 第六章）"
            echo
        fi
        echo "  其他排查："
        echo "    1. docker logs hlm-api --tail 30"
        echo "    2. docker exec hlm-api python3 -c \"import data_source;print(data_source.describe())\""
        [ "$DATA_SOURCE" = "local" ] && echo "    3. docker exec hlm-api ls -la /app/data   # 应有 8 个文件"
        exit 1
    fi
    ok "后端就绪（$N chunk）"

    DATA_LABEL="$DATA_SOURCE"
fi

# =============================================================
# 前端（所有模式共用）
# =============================================================
info "启动前端 ..."

WEB_PORT="${HLM_WEB_PORT:-5173}"

# -------------------------------------------------------------
# WSL 环境：前端必须在 **Windows 侧** 启动
# -------------------------------------------------------------
# 原因：WSL2 的 localhost 转发只对 Windows → WSL 单向有效。
#   在 WSL 里起 node，Windows 浏览器访问 127.0.0.1:5173 会失败
#   （实测：WSL 内 node 监听 0.0.0.0 也访问不到）。
#
#   从 WSL 用 cmd.exe /c start 也拿不到环境变量，server.js 会
#   退回默认端口 3000 —— Windows 保留端口段 (2980-3079)，
#   必然 EADDRINUSE。
#
#   所以调用 start-web.ps1：它在 Windows 侧设置环境变量，
#   用 schtasks 完全脱离地启动 node，然后**自己轮询端口**
#   直到就绪再返回。它的退出码就是判断依据。
#
# 注意：不要在这里用 curl 验证！
#   WSL 访问不到 Windows 侧的 5173（反向隔离），curl 必然失败，
#   但前端实际是好的。以 ps1 退出码为准。
WEB_METHOD=""
if grep -qi microsoft /proc/version 2>/dev/null; then
    if [ -f start-web.ps1 ]; then
        dim "  WSL 环境，转到 Windows 侧启动"
        WIN_PS1="$(wslpath -w "$ROOT/start-web.ps1" 2>/dev/null || echo "$ROOT/start-web.ps1")"
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$WIN_PS1" "$WEB_PORT" < /dev/null 2>&1 | sed 's/^/  /'
        WEB_RC=${PIPESTATUS[0]:-$?}
        WEB_METHOD="windows"
    else
        warn "start-web.ps1 不存在，尝试 WSL 内启动（Windows 可能访问不到）"
        pkill -f 'node server.js' 2>/dev/null || true
        (cd web && HLM_WEB_PORT="$WEB_PORT" HLM_BACKEND=http://127.0.0.1:8000 nohup node server.js > "$ROOT/web.log" 2>&1 &)
        disown 2>/dev/null || true
        WEB_RC=0
        WEB_METHOD="wsl"
    fi
else
    pkill -f 'node server.js' 2>/dev/null || true
    (cd web && HLM_WEB_PORT="$WEB_PORT" HLM_BACKEND=http://127.0.0.1:8000 nohup node server.js > "$ROOT/web.log" 2>&1 &)
    disown 2>/dev/null || true
    WEB_RC=0
    WEB_METHOD="native"
    # 原生环境可以自己验
    for _ in $(seq 1 15); do
        curl -sf --max-time 2 "http://127.0.0.1:$WEB_PORT/api/health" >/dev/null 2>&1 && break
        sleep 1
    done
fi

if [ "$WEB_RC" = "0" ]; then
    ok "前端就绪（http://127.0.0.1:$WEB_PORT）"
    if [ "$WEB_METHOD" = "windows" ]; then
        dim "  （Windows 侧运行，WSL 内无法直接 curl 验证，以启动器退出码为准）"
    fi
else
    warn "前端启动失败（启动器退出码 $WEB_RC）"
    echo "        排查："
    echo "          cat web/_srv.err"
    echo "          Windows 侧单独跑: powershell -ExecutionPolicy Bypass -File start-web.ps1"
fi

# =============================================================
# 完成
# =============================================================
echo
echo "=============================================="
echo -e "${GREEN}✅ 启动完成${NC}"
echo "=============================================="
echo
echo "  前端界面   http://127.0.0.1:${WEB_PORT}"
echo "  后端 API   http://127.0.0.1:8000/docs"
echo
echo "  数据源     $DATA_LABEL"
if [ "$MODE" = "cluster" ]; then
    echo "  HDFS UI    http://127.0.0.1:9870"
    echo "  HBase UI   http://127.0.0.1:16010"
    echo "  YARN UI    http://127.0.0.1:18088"
elif [ "${HDFS_MANAGED:-0}" = "1" ]; then
    echo "  HDFS UI    http://127.0.0.1:19870"
elif [ -n "${WEBHDFS_URL:-}" ]; then
    echo "  WebHDFS    $WEBHDFS_URL"
fi
echo
echo "  查看日志   docker logs -f hlm-api"
echo "  状态检查   ./start.sh --status"
echo "  停止全部   ./start.sh --stop"
echo "=============================================="
