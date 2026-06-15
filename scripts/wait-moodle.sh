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
MOODLE_HOST_PORT=${MOODLE_HOST_PORT:-18080}

echo "=== Đang chờ Moodle hoàn tất cài đặt và sẵn sàng ==="

# 1. Chờ Moodle container khởi tạo các bảng CSDL (mdl_config)
echo "Chờ CSDL Moodle được thiết lập xong qua CLI..."
until [ "$(docker compose exec -T postgres psql -U "$POSTGRES_ADMIN_USER" -d "$MOODLE_DB_NAME" -tAc "SELECT 1 FROM pg_tables WHERE tablename='mdl_config'" 2>/dev/null)" = "1" ]; do
    echo "CSDL Moodle đang được cài đặt (chạy install_database.php trong container)..."
    sleep 5
done
echo "CSDL Moodle đã được cài đặt hoàn tất."

# 2. Chờ Apache Web Server phản hồi trên port host
echo "Chờ máy chủ Apache Moodle phản hồi trên port $MOODLE_HOST_PORT..."
until curl -s -o /dev/null -w "%{http_code}" "http://localhost:$MOODLE_HOST_PORT" >/dev/null 2>&1; do
    echo "Moodle Web Server chưa phản hồi HTTP..."
    sleep 3
done

echo "Moodle Web Server đã phản hồi và sẵn sàng hoạt động!"
