#!/bin/bash
set -euo pipefail

# Navigate to the directory containing this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Tự động phát hiện xem có cần dùng sudo cho docker hay không
DOCKER_CMD="docker"
if ! docker info >/dev/null 2>&1; then
    if sudo docker info >/dev/null 2>&1; then
        DOCKER_CMD="sudo docker"
    else
        echo "❌ Lỗi: Không thể kết nối tới Docker daemon."
        exit 1
    fi
fi

if [ $# -eq 0 ]; then
    echo "Đang xem log của tất cả dịch vụ QA Tools (Nhấn Ctrl+C để thoát)..."
    $DOCKER_CMD compose logs -f
else
    SERVICE=$1
    # Standardize names to service names in docker-compose.yml
    case "$SERVICE" in
        mantis|mantis-app)
            TARGET="mantis"
            ;;
        testlink|testlink-app)
            TARGET="testlink"
            ;;
        postgres|qa-postgres)
            echo "ℹ️  PostgreSQL hiện tại đang chạy chung trên container 'infra-postgres' thuộc stack infra-data."
            echo "Xem log bằng cách: docker logs -f infra-postgres"
            exit 0
            ;;
        *)
            echo "Lỗi: Dịch vụ '$SERVICE' không tồn tại."
            echo "Cách dùng: ./logs.sh [mantis | testlink | postgres]"
            exit 1
            ;;
    esac
    echo "Đang xem log của dịch vụ: $TARGET (Nhấn Ctrl+C để thoát)..."
    $DOCKER_CMD compose logs -f "$TARGET"
fi
