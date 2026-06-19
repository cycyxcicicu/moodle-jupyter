#!/bin/bash
set -euo pipefail

# Navigate to the directory containing this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Copy .env from .env.example if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Đã tạo file .env từ .env.example."
fi

# Load variables from .env
set -a
. ./.env
set +a

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

echo "========================================================="
echo "Đang khởi dựng và chạy các container QA Tools..."
$DOCKER_CMD compose up -d --build

# Chờ PostgreSQL sẵn sàng kết nối
echo "Đang chờ cơ sở dữ liệu PostgreSQL khởi động..."
until $DOCKER_CMD exec -i qa-postgres pg_isready -U "${QA_DB_USER:-admin}" -d "${POSTGRES_DB:-qa_default_db}" >/dev/null 2>&1; do
    echo -n "."
    sleep 1
done
echo " CSDL đã sẵn sàng!"

# Tự động nạp các hàm UDF PostgreSQL cho TestLink
echo "Đang tự động nạp các hàm UDF PostgreSQL cho TestLink..."
$DOCKER_CMD exec -i testlink-app cat /var/www/html/install/sql/postgres/testlink_create_udf0.sql | $DOCKER_CMD exec -i qa-postgres psql -U "${QA_DB_USER:-admin}" -d "${POSTGRES_DB:-qa_default_db}" >/dev/null 2>&1 || true
echo "✅ Đã nạp thành công các hàm UDF cho PostgreSQL!"


echo "========================================================="
echo "   Khởi chạy thành công hệ thống QA Tools!"
echo "---------------------------------------------------------"
echo " Mantis Bug Tracker: http://localhost:${MANTIS_PORT:-18081}"
echo " TestLink:           http://localhost:${TESTLINK_PORT:-18082}"
echo " PostgreSQL Host:    localhost:${POSTGRES_PORT:-15433}"
echo "---------------------------------------------------------"
echo " Chạy chẩn đoán hệ thống bằng doctor.sh..."
./doctor.sh


