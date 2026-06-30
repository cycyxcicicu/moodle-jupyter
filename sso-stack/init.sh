#!/usr/bin/env bash
# =========================================================================
# Script Khởi tạo SSO Stack (OpenLDAP + Keycloak + phpLDAPadmin)
# =========================================================================

set -euo pipefail

# Xác định thư mục chứa script này và đưa về root của sso-stack
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

echo "========================================================================="
echo "Bắt đầu khởi tạo môi trường SSO Stack (OpenLDAP & Keycloak)"
echo "========================================================================="

# 1. Tải các biến môi trường
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "Đang tạo file .env từ .env.example..."
        cp .env.example .env
    else
        echo "❌ Lỗi: Không tìm thấy tệp .env hoặc .env.example!"
        exit 1
    fi
fi

# Nạp các biến môi trường từ .env (hỗ trợ xuống dòng Windows \r\n và khoảng trắng)
set -a
source <(sed 's/\r$//' .env)
set +a

# 2. Tạo mạng Docker dùng chung nếu chưa tồn tại
echo "Đang kiểm tra mạng dùng chung infra-data-net..."
docker network inspect infra-data-net >/dev/null 2>&1 || {
    echo "Đang tạo mạng dùng chung infra-data-net..."
    docker network create infra-data-net
}

# 3. Kết nối PostgreSQL dùng chung (infra-postgres) và tạo database
# Xác định đường dẫn tương đối tới thư mục infra-data
INFRA_DATA_DIR=""
if [ -d "../infra-data" ]; then
    INFRA_DATA_DIR="../infra-data"
elif [ -d "../../infra-data" ]; then
    INFRA_DATA_DIR="../../infra-data"
fi

# Kiểm tra container infra-postgres
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

# Đọc cấu hình superuser và db mặc định từ infra-data/.env nếu có
INFRA_POSTGRES_USER="postgres"
INFRA_POSTGRES_DB="postgres"
if [ -n "${INFRA_DATA_DIR}" ] && [ -f "${INFRA_DATA_DIR}/.env" ]; then
    # Lọc biến loại bỏ ký tự rác
    INFRA_POSTGRES_USER=$(grep -E '^POSTGRES_USER=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
    INFRA_POSTGRES_DB=$(grep -E '^POSTGRES_DB=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
fi
INFRA_POSTGRES_USER=${INFRA_POSTGRES_USER:-postgres}
INFRA_POSTGRES_DB=${INFRA_POSTGRES_DB:-postgres}

# Kiểm tra và khởi tạo database cho Keycloak qua docker exec
echo "Đang khởi tạo database và tài khoản người dùng cho Keycloak (Sử dụng user: ${INFRA_POSTGRES_USER}, db: ${INFRA_POSTGRES_DB})...."
docker exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" <<SQL
-- Tạo role/user nếu chưa có
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${KEYCLOAK_DB_USER}') THEN
    CREATE ROLE "${KEYCLOAK_DB_USER}" LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
  ELSE
    ALTER ROLE "${KEYCLOAK_DB_USER}" WITH LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
  END IF;
END
\$\$;

-- Tạo database nếu chưa có
SELECT 'CREATE DATABASE "${KEYCLOAK_DB_NAME}" OWNER "${KEYCLOAK_DB_USER}"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${KEYCLOAK_DB_NAME}')\gexec

-- Cấp quyền hạn truy cập database
ALTER DATABASE "${KEYCLOAK_DB_NAME}" OWNER TO "${KEYCLOAK_DB_USER}";
GRANT ALL PRIVILEGES ON DATABASE "${KEYCLOAK_DB_NAME}" TO "${KEYCLOAK_DB_USER}";
SQL

# 4. Xây dựng và khởi chạy các container SSO
echo "=== Đang build các dịch vụ SSO từ Dockerfile local ==="
docker compose build

echo "=== Đang khởi động các dịch vụ SSO bằng Docker Compose ==="
docker compose up -d

# 5. Đợi các dịch vụ SSO đạt trạng thái healthy
echo "=== Đang chờ các dịch vụ SSO sẵn sàng (healthy)... ==="

# Chờ OpenLDAP
echo -n "Đang đợi OpenLDAP..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' sso-openldap 2>/dev/null)" = "healthy" ]; do
    echo -n "."
    sleep 2
done
echo " OpenLDAP đã sẵn sàng!"

# Chờ Keycloak
echo -n "Đang đợi Keycloak (Quá trình này có thể mất 30-60 giây)..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' sso-keycloak 2>/dev/null)" = "healthy" ]; do
    echo -n "."
    sleep 2
done
echo " Keycloak đã sẵn sàng!"

# Chờ phpLDAPadmin
echo -n "Đang đợi phpLDAPadmin..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' sso-ldap-admin 2>/dev/null)" = "healthy" ]; do
    echo -n "."
    sleep 2
done
echo " phpLDAPadmin đã sẵn sàng!"

# 6. Tự động cấu hình Keycloak Clients cho Moodle & GitLab
chmod +x ./update-keycloak-clients.sh
./update-keycloak-clients.sh

echo "========================================================================="
echo "🎉 QUÁ TRÌNH KHỞI TẠO SSO STACK HOÀN TẤT THÀNH CÔNG!"
echo "-------------------------------------------------------------------------"
echo "OpenLDAP Server:         ldap://localhost:${LDAP_HOST_PORT:-1389}"
echo "Keycloak Web Console:    http://localhost:${KEYCLOAK_HOST_PORT:-18090} (Realm: school)"
echo "Tài khoản Keycloak:      ${KEYCLOAK_ADMIN} / ${KEYCLOAK_ADMIN_PASSWORD}"
echo "-------------------------------------------------------------------------"
echo "phpLDAPadmin Web:        http://localhost:${LDAP_ADMIN_HOST_PORT:-18091}"
echo "Tài khoản phpLDAPadmin:  cn=admin,${LDAP_BASE_DN} / ${LDAP_ADMIN_PASSWORD}"
echo "========================================================================="
