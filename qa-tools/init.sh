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

# Đọc cấu hình superuser và db mặc định từ infra-data/.env để thực hiện kiểm tra và khởi tạo
INFRA_POSTGRES_USER="postgres"
INFRA_POSTGRES_DB="postgres"
if [ -f "../infra-data/.env" ]; then
    INFRA_POSTGRES_USER=$(grep -E '^POSTGRES_USER=' "../infra-data/.env" | cut -d= -f2 | xargs)
    INFRA_POSTGRES_DB=$(grep -E '^POSTGRES_DB=' "../infra-data/.env" | cut -d= -f2 | xargs)
fi
INFRA_POSTGRES_USER=${INFRA_POSTGRES_USER:-postgres}
INFRA_POSTGRES_DB=${INFRA_POSTGRES_DB:-postgres}

timeout_count=0
until $DOCKER_CMD exec -i infra-postgres pg_isready -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" >/dev/null 2>&1; do
    echo -n "."
    sleep 1
    timeout_count=$((timeout_count + 1))
    if [ $timeout_count -ge 30 ]; then
        echo "❌ Lỗi: Timeout 30s không thể kết nối tới PostgreSQL dùng chung (infra-postgres)."
        exit 1
    fi
done
echo " CSDL đã sẵn sàng!"

# Đảm bảo các database và user cho QA Tools đã được khởi tạo trên infra-postgres
echo "Đang khởi tạo database và tài khoản cho QA Tools..."
$DOCKER_CMD exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" <<SQL >/dev/null 2>&1 || true
-- Tạo role/user nếu chưa có
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${QA_DB_USER}') THEN
    CREATE ROLE "${QA_DB_USER}" LOGIN PASSWORD '${QA_DB_PASSWORD}';
  ELSE
    ALTER ROLE "${QA_DB_USER}" WITH LOGIN PASSWORD '${QA_DB_PASSWORD}';
  END IF;
END
\$\$;

-- Tạo các database nếu chưa có
SELECT 'CREATE DATABASE "${POSTGRES_DB}" OWNER "${QA_DB_USER}"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${POSTGRES_DB}')\gexec

SELECT 'CREATE DATABASE "${MANTIS_DB_NAME}" OWNER "${QA_DB_USER}"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${MANTIS_DB_NAME}')\gexec

SELECT 'CREATE DATABASE "${TESTLINK_DB_NAME}" OWNER "${QA_DB_USER}"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${TESTLINK_DB_NAME}')\gexec

-- Cấp quyền hạn truy cập database
ALTER DATABASE "${POSTGRES_DB}" OWNER TO "${QA_DB_USER}";
GRANT ALL PRIVILEGES ON DATABASE "${POSTGRES_DB}" TO "${QA_DB_USER}";

ALTER DATABASE "${MANTIS_DB_NAME}" OWNER TO "${QA_DB_USER}";
GRANT ALL PRIVILEGES ON DATABASE "${MANTIS_DB_NAME}" TO "${QA_DB_USER}";

ALTER DATABASE "${TESTLINK_DB_NAME}" OWNER TO "${QA_DB_USER}";
GRANT ALL PRIVILEGES ON DATABASE "${TESTLINK_DB_NAME}" TO "${QA_DB_USER}";
SQL

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


