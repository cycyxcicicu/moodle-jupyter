#!/usr/bin/env bash
# =========================================================================
# Script Khởi tạo Open WebUI (Kết nối Database infra-postgres dùng chung)
# =========================================================================

set -euo pipefail

# Xác định thư mục chứa script này
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

echo "========================================================================="
# Check if .env exists
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "Đang tạo file .env từ .env.example..."
        cp .env.example .env
    else
        echo "❌ Lỗi: Không tìm thấy tệp .env hoặc .env.example!"
        exit 1
    fi
fi

# Load variables
export $(grep -v '^#' .env | xargs)

# Tìm thư mục infra-data
INFRA_DATA_DIR=""
if [ -d "../infra-data" ]; then
    INFRA_DATA_DIR="../infra-data"
elif [ -d "../../infra-data" ]; then
    INFRA_DATA_DIR="../../infra-data"
fi

echo "=== Kiểm tra PostgreSQL dùng chung (infra-postgres) ==="
if ! docker ps --filter name=infra-postgres --filter status=running -q | grep -q . ; then
    echo "⚠️ Phát hiện container 'infra-postgres' chưa khởi chạy."
    if [ -n "${INFRA_DATA_DIR}" ]; then
        echo "Đang tự động khởi chạy stack infra-data..."
        docker compose -f "${INFRA_DATA_DIR}/docker-compose.yml" up -d postgres
    else
        echo "❌ Lỗi: Không tìm thấy thư mục infra-data để khởi động database."
        exit 1
    fi
fi

echo "Đang chờ infra-postgres sẵn sàng kết nối (healthy)..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' infra-postgres 2>/dev/null)" = "healthy" ]; do
    echo -n "."
    sleep 2
done
echo " infra-postgres đã sẵn sàng!"

# Lấy cấu hình superuser của infra-postgres
INFRA_POSTGRES_USER="postgres"
INFRA_POSTGRES_DB="postgres"
if [ -n "${INFRA_DATA_DIR}" ] && [ -f "${INFRA_DATA_DIR}/.env" ]; then
    INFRA_POSTGRES_USER=$(grep -E '^POSTGRES_USER=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
    INFRA_POSTGRES_DB=$(grep -E '^POSTGRES_DB=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
fi
INFRA_POSTGRES_USER=${INFRA_POSTGRES_USER:-postgres}
INFRA_POSTGRES_DB=${INFRA_POSTGRES_DB:-postgres}

# Khởi tạo database và user cho Open WebUI
echo "Đang khởi tạo database và tài khoản người dùng cho Open WebUI..."
docker exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" <<SQL
-- Tạo role/user nếu chưa có
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE "${DB_USER}" LOGIN PASSWORD '${DB_PASSWORD}';
  ELSE
    ALTER ROLE "${DB_USER}" WITH LOGIN PASSWORD '${DB_PASSWORD}';
  END IF;
END
\$\$;

-- Tạo database nếu chưa có
SELECT 'CREATE DATABASE "${DB_NAME}" OWNER "${DB_USER}"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}')\gexec

-- Cấp quyền hạn truy cập database
ALTER DATABASE "${DB_NAME}" OWNER TO "${DB_USER}";
GRANT ALL PRIVILEGES ON DATABASE "${DB_NAME}" TO "${DB_USER}";
SQL

echo "✅ Đã cấu hình và kiểm tra xong Database & User."

# Khởi tạo mạng Docker dùng chung nếu chưa tồn tại
docker network inspect infra-data-net >/dev/null 2>&1 || {
    echo "Đang tạo mạng dùng chung infra-data-net..."
    docker network create infra-data-net
}

# Khởi chạy container Open WebUI
echo "Đang khởi động Open WebUI..."
docker compose up -d

echo "========================================================================="
echo "🎉 KHỞI TẠO HOÀN TẤT THÀNH CÔNG!"
echo "Truy cập Open WebUI tại: http://localhost:${WEBUI_PORT}"
echo "========================================================================="
