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

# Tự động kiểm tra và khởi động database dùng chung
echo "=== Kiểm tra PostgreSQL dùng chung (infra-postgres) ==="
if ! $DOCKER_CMD ps --filter name=infra-postgres --filter status=running -q | grep -q . ; then
    echo "⚠️ Phát hiện container 'infra-postgres' chưa khởi chạy."
    if [ -d "../infra-data" ]; then
        echo "Đang tự động khởi chạy stack infra-data..."
        $DOCKER_CMD compose -f ../infra-data/docker-compose.yml up -d postgres
    else
        echo "❌ Lỗi: Không tìm thấy thư mục infra-data để khởi động database."
        exit 1
    fi
fi

# Chờ PostgreSQL sẵn sàng kết nối trước khi khởi động ứng dụng
echo "Đang chờ cơ sở dữ liệu PostgreSQL dùng chung (infra-postgres) khởi động..."
timeout_count=0
until $DOCKER_CMD exec -i infra-postgres pg_isready -U "${QA_DB_USER:-admin}" -d "${POSTGRES_DB:-qa_default_db}" >/dev/null 2>&1; do
    echo -n "."
    sleep 1
    timeout_count=$((timeout_count + 1))
    if [ $timeout_count -ge 30 ]; then
        echo "❌ Lỗi: Timeout 30s không thể kết nối tới PostgreSQL dùng chung (infra-postgres)."
        exit 1
    fi
done
echo " CSDL đã sẵn sàng!"

# Đảm bảo các database đã được khởi tạo trên infra-postgres
$DOCKER_CMD exec -i infra-postgres bash /docker-entrypoint-initdb.d/01-create-app-databases.sh >/dev/null 2>&1 || true

echo "========================================================="
echo "Đang khởi dựng và chạy các container QA Tools..."
$DOCKER_CMD compose up -d --build

# Chờ container TestLink sẵn sàng để nạp UDF SQL
echo "Đang chờ container testlink-app khởi động hoàn toàn..."
timeout_count=0
until [ "$($DOCKER_CMD inspect -f '{{.State.Status}}' testlink-app 2>/dev/null)" = "running" ]; do
    echo -n "."
    sleep 1
    timeout_count=$((timeout_count + 1))
    if [ $timeout_count -ge 30 ]; then
        echo "❌ Lỗi: Timeout 30s không thể khởi động container testlink-app."
        exit 1
    fi
done

# Tự động nạp các hàm UDF PostgreSQL cho TestLink
echo "Đang tự động nạp các hàm UDF PostgreSQL cho TestLink..."
$DOCKER_CMD exec -i testlink-app cat /var/www/html/install/sql/postgres/testlink_create_udf0.sql | $DOCKER_CMD exec -i infra-postgres psql -U "${QA_DB_USER:-admin}" -d "${TESTLINK_DB_NAME:-testlink_db}" >/dev/null 2>&1 || true
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


