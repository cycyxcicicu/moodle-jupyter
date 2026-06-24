#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Load biến môi trường
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

POSTGRES_ADMIN_USER=${POSTGRES_ADMIN_USER:-postgres_user}
POSTGRES_DB=${POSTGRES_DB:-postgres_app_db}

echo "=== Đang chờ PostgreSQL dùng chung (infra-postgres) sẵn sàng ==="

# Chờ pg_isready trả về thành công
timeout_count=0
until docker exec -i infra-postgres pg_isready -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
    echo "Đang đợi PostgreSQL (infra-postgres) khởi động socket kết nối..."
    sleep 2
    timeout_count=$((timeout_count + 2))
    if [ $timeout_count -ge 30 ]; then
        echo "❌ Lỗi: Timeout 30s không thể kết nối tới PostgreSQL (infra-postgres)."
        exit 1
    fi
done

echo "=== PostgreSQL (infra-postgres) đã sẵn sàng kết nối ==="
