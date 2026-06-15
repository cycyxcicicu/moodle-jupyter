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

POSTGRES_ADMIN_USER=${POSTGRES_ADMIN_USER:-postgres}
MOODLE_DB_NAME=${MOODLE_DB_NAME:-moodle}
JUPYTERHUB_DB_NAME=${JUPYTERHUB_DB_NAME:-jupyterhub}

echo "=== Đang chờ PostgreSQL service sẵn sàng ==="

# Chờ pg_isready trả về thành công
until docker compose exec -T postgres pg_isready -U "$POSTGRES_ADMIN_USER" -d postgres >/dev/null 2>&1; do
    echo "Đang đợi PostgreSQL khởi động socket kết nối..."
    sleep 2
done

echo "PostgreSQL socket đã mở. Đang chờ khởi tạo các database riêng biệt..."

# Chờ cho đến khi cả 2 database moodle và jupyterhub được tạo bởi init script
until [ "$(docker compose exec -T postgres psql -U "$POSTGRES_ADMIN_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$MOODLE_DB_NAME'" 2>/dev/null)" = "1" ] && \
      [ "$(docker compose exec -T postgres psql -U "$POSTGRES_ADMIN_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$JUPYTERHUB_DB_NAME'" 2>/dev/null)" = "1" ]; do
    echo "Đang chờ script 01-create-databases.sql hoàn tất khởi tạo các DB..."
    sleep 2
done

echo "PostgreSQL đã sẵn sàng. Các database '$MOODLE_DB_NAME' và '$JUPYTERHUB_DB_NAME' đã được tạo."
