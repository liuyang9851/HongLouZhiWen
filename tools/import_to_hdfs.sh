#!/usr/bin/env bash
# =============================================================
# 红楼智问 · 数据导入 HDFS
# =============================================================
# 用途：把你的 Hadoop 集群接上本项目的数据。
#
# 场景：同学的 Hadoop 版本可能不一致（2.x / 3.x），
#      所以本脚本**只用最基础的 HDFS 命令**（mkdir / put / test），
#      这些命令从 Hadoop 1.x 到 3.x 语义完全一致。
#
# 用法：
#   ./tools/import_to_hdfs.sh                     # 用默认容器名 master
#   ./tools/import_to_hdfs.sh my-namenode         # 指定容器名
#   ./tools/import_to_hdfs.sh my-namenode /data   # 指定容器名 + 数据目录
#
# 前置：deploy/data/ 下有 7 个 .jsonl 文件
# =============================================================
set -e

cd "$(dirname "$0")/.."

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}▸${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }

CONTAINER="${1:-master}"
SRC_DIR="${2:-deploy/data}"

# ---- 检查 ----
if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    error "容器 $CONTAINER 未运行"
    echo "  当前运行的容器："
    docker ps --format '  {{.Names}}  {{.Image}}'
    exit 1
fi

if [ ! -f "$SRC_DIR/chunks_3ver.jsonl" ]; then
    error "数据目录 $SRC_DIR 下缺少 chunks_3ver.jsonl"
    exit 1
fi

info "目标容器: $CONTAINER"
info "数据目录: $SRC_DIR"

# ---- 检查 HDFS 可用 ----
if ! docker exec "$CONTAINER" bash -lc "hdfs dfs -ls / >/dev/null 2>&1"; then
    error "HDFS 不可用（hdfs dfs -ls / 失败）"
    echo "  可能原因：集群未启动、或 HDFS 处于 safe mode"
    echo "  检查：docker exec $CONTAINER hdfs dfsadmin -safemode get"
    exit 1
fi

if docker exec "$CONTAINER" bash -lc "hdfs dfsadmin -safemode get 2>/dev/null | grep -q 'Safe mode is ON'"; then
    warn "HDFS 处于 safe mode，先退出"
    docker exec "$CONTAINER" bash -lc "hdfs dfsadmin -safemode leave"
    sleep 3
fi

# ---- 创建目录 ----
info "创建 HDFS 目录 ..."
docker exec "$CONTAINER" bash -lc "
    hdfs dfs -mkdir -p /hlm/warehouse/chunks3ver
    hdfs dfs -mkdir -p /hlm/warehouse/pizhu3src
    hdfs dfs -mkdir -p /hlm/derived/vectors3ver/model=bge-small-zh-v1.5
    hdfs dfs -mkdir -p /hlm/logs
"

# ---- 拷贝到容器 ----
info "拷贝数据到容器 /tmp/hlm_import ..."
docker exec "$CONTAINER" bash -lc "rm -rf /tmp/hlm_import && mkdir -p /tmp/hlm_import"
for f in chunks_3ver.jsonl pizhu_3src.jsonl vectors_3ver.jsonl pizhu_3src_vectors.jsonl; do
    if [ -f "$SRC_DIR/$f" ]; then
        INFILE="$SRC_DIR/$f"
        docker cp "$INFILE" "$CONTAINER:/tmp/hlm_import/$f"
        SZ=$(du -h "$INFILE" | cut -f1)
        echo "    $f  ($SZ)"
    else
        warn "缺少 $f，跳过"
    fi
done

# ---- 上传 HDFS ----
info "上传到 HDFS ..."
docker exec "$CONTAINER" bash -lc "
    set -e
    [ -f /tmp/hlm_import/chunks_3ver.jsonl ] && \
        hdfs dfs -put -f /tmp/hlm_import/chunks_3ver.jsonl /hlm/warehouse/chunks3ver/
    [ -f /tmp/hlm_import/pizhu_3src.jsonl ] && \
        hdfs dfs -put -f /tmp/hlm_import/pizhu_3src.jsonl /hlm/warehouse/pizhu3src/
    [ -f /tmp/hlm_import/vectors_3ver.jsonl ] && \
        hdfs dfs -put -f /tmp/hlm_import/vectors_3ver.jsonl \
            /hlm/derived/vectors3ver/model=bge-small-zh-v1.5/
    [ -f /tmp/hlm_import/pizhu_3src_vectors.jsonl ] && \
        hdfs dfs -put -f /tmp/hlm_import/pizhu_3src_vectors.jsonl \
            /hlm/derived/vectors3ver/model=bge-small-zh-v1.5/
    rm -rf /tmp/hlm_import
"

# ---- 验证 ----
info "验证上传结果 ..."
docker exec "$CONTAINER" bash -lc "hdfs dfs -ls -h /hlm/warehouse/chunks3ver/ /hlm/warehouse/pizhu3src/ /hlm/derived/vectors3ver/model=bge-small-zh-v1.5/ 2>/dev/null"

# 行数校验
LINES=$(docker exec "$CONTAINER" bash -lc \
    "hdfs dfs -cat /hlm/warehouse/chunks3ver/chunks_3ver.jsonl 2>/dev/null | wc -l" | tr -d '[:space:]')
info "chunks_3ver.jsonl 行数: $LINES（应为 3295）"

if [ "$LINES" != "3295" ]; then
    warn "行数与预期不符，请检查上传是否完整"
fi

# ---- WebHDFS 提示 ----
echo
info "完成。接下来："
echo "  1) 确认 WebHDFS 可用："
echo "       curl http://127.0.0.1:9870/webhdfs/v1/hlm?op=LISTSTATUS"
echo "     （Hadoop 2.x 端口可能是 50070）"
echo
echo "  2) 启动项目："
echo "       ./start.sh"
echo "     start.sh 会自动探测 HDFS 并使用它作为数据源。"
echo
echo "  3) 如需手动指定："
echo "       export HLM_DATA_SOURCE=hdfs"
echo "       export HLM_WEBHDFS_URL=http://<你的namenode>:9870/webhdfs/v1"
