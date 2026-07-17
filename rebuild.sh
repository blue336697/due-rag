#!/bin/bash
# RAG Service 重建脚本
# 用法: ./rebuild.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/docker"

echo "=== RAG Service 重建 ==="

# 停止 -> 构建 -> 启动
docker-compose down
docker-compose build rag-service
docker-compose up -d

# 等就绪
echo -n "等待服务启动"
for i in {1..30}; do
    if curl -s http://localhost:8020/health > /dev/null 2>&1; then
        echo ""
        echo "✅ 重建完成: http://localhost:8020"
        echo "查看日志: docker logs rag-service -f"
        exit 0
    fi
    echo -n "."
    sleep 1
done

echo ""
echo "❌ 启动超时，检查日志: docker logs rag-service --tail 50"
exit 1
