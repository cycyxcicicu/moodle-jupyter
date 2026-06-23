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
MOODLE_DB_NAME=${MOODLE_DB_NAME:-moodle}
MOODLE_DB_USER=${MOODLE_DB_USER:-moodle_user}
MOODLE_DB_PASSWORD=${MOODLE_DB_PASSWORD:-moodle_password}
JUPYTERHUB_DB_NAME=${JUPYTERHUB_DB_NAME:-jupyterhub}
JUPYTERHUB_DB_USER=${JUPYTERHUB_DB_USER:-jupyterhub_user}
JUPYTERHUB_DB_PASSWORD=${JUPYTERHUB_DB_PASSWORD:-moodle_password} # Mật khẩu của jupyterhub user

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

echo "PostgreSQL (infra-postgres) socket đã mở. Đang kiểm tra và tự động khởi tạo database nếu cần..."

# Tạo role và DB Moodle nếu chưa có
docker exec -i infra-postgres psql -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_DB" -c "
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$MOODLE_DB_USER') THEN
    CREATE ROLE \"$MOODLE_DB_USER\" LOGIN PASSWORD '$MOODLE_DB_PASSWORD';
  ELSE
    ALTER ROLE \"$MOODLE_DB_USER\" WITH LOGIN PASSWORD '$MOODLE_DB_PASSWORD';
  END IF;
END
\$\$;" >/dev/null 2>&1 || true

docker exec -i infra-postgres psql -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_DB" -c "
SELECT 'CREATE DATABASE \"$MOODLE_DB_NAME\" OWNER \"$MOODLE_DB_USER\"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$MOODLE_DB_NAME')\gexec" >/dev/null 2>&1 || true

# Tạo role và DB JupyterHub nếu chưa có
docker exec -i infra-postgres psql -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_DB" -c "
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$JUPYTERHUB_DB_USER') THEN
    CREATE ROLE \"$JUPYTERHUB_DB_USER\" LOGIN PASSWORD '$JUPYTERHUB_DB_PASSWORD';
  ELSE
    ALTER ROLE \"$JUPYTERHUB_DB_USER\" WITH LOGIN PASSWORD '$JUPYTERHUB_DB_PASSWORD';
  END IF;
END
\$\$;" >/dev/null 2>&1 || true

docker exec -i infra-postgres psql -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_DB" -c "
SELECT 'CREATE DATABASE \"$JUPYTERHUB_DB_NAME\" OWNER \"$JUPYTERHUB_DB_USER\"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$JUPYTERHUB_DB_NAME')\gexec" >/dev/null 2>&1 || true

echo "PostgreSQL đã sẵn sàng. Các database '$MOODLE_DB_NAME' và '$JUPYTERHUB_DB_NAME' đã được tạo lập thành công."
