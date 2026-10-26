#!/bin/bash
# =============================================================
# S4 · 启动 RAG 后端容器
# =============================================================
# 后端必须运行在 hadoop-lab_cluster 网络内，才能访问：
#   - HDFS RPC   master:9000
#   - HBase Thrift master:9090
#   - HiveServer2 master:10000
#
# 用法（Windows PowerShell）：
#   cd D:\学校事务\大数据原理与应用\红楼智问\backend
#   docker build -t hlm-rag-api:1.0 .
#   docker run -d --name hlm-api --network hadoop-lab_cluster \
#     -p 127.0.0.1:8000:8000 \
#     -e DEEPSEEK_API_KEY=xxx \
#     -v "D:\学校事务\大数据原理与应用\红楼智问\pipeline\out:/workspace/pipeline/out" \
#     hlm-rag-api:1.0
# =============================================================
set -e

echo "=== 1. 检查网络与依赖 ==="
docker network inspect hadoop-lab_cluster >/dev/null 2>&1 || {
  echo "❌ 网络 hadoop-lab_cluster 不存在，请先启动 Hadoop 集群"; exit 1; }
echo "  网络 OK"

echo "=== 2. 构建镜像 ==="
docker build -t hlm-rag-api:1.0 .

echo "=== 3. 停止旧容器 ==="
docker rm -f hlm-api 2>/dev/null || true

echo "=== 4. 启动 ==="
docker run -d --name hlm-api \
  --network hadoop-lab_cluster \
  -p 127.0.0.1:8000:8000 \
  --restart unless-stopped \
  -v "/d/学校事务/大数据原理与应用/红楼智问/pipeline/out:/workspace/pipeline/out" \
  hlm-rag-api:1.0

echo "=== 5. 等待就绪 ==="
for i in $(seq 1 40); do
  if curl -s --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "  ✅ 后端已就绪"
    curl -s http://127.0.0.1:8000/health | head -c 600
    echo ""
    exit 0
  fi
  sleep 2
done
echo "  ⚠ 超时，查看日志: docker logs hlm-api"
docker logs --tail 30 hlm-api
exit 1
