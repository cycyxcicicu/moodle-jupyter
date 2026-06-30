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

echo "⚠️ CẢNH BÁO: Hành động này sẽ xóa toàn bộ các container, network và DỮ LIỆU DATABASE của các công cụ QA (MantisBT & TestLink)."
read -p "Bạn có chắc chắn muốn thực hiện reset không? (y/N): " confirm

if [[ "$confirm" =~ ^[yY]$ ]]; then
    echo "Đang dừng các container QA Tools..."
    $DOCKER_CMD compose down -v --remove-orphans

    # Hỏi xác nhận reset cơ sở dữ liệu trên Postgres dùng chung (infra-postgres)
    read -p "Bạn có muốn làm sạch cơ sở dữ liệu (qa_default_db, mantis_db, testlink_db) trên infra-postgres (Hard Reset)? (y/N): " confirm_db
    if [[ "$confirm_db" =~ ^[yY]$ ]]; then
        if $DOCKER_CMD ps --filter name=infra-postgres --filter status=running -q | grep -q . ; then
            echo "=== Đang xóa sạch cơ sở dữ liệu QA Tools trên infra-postgres ==="
            if [ -f .env ]; then
                set -a
                . ./.env
                set +a
            fi
            
            # Đọc cấu hình superuser và db mặc định từ infra-data/.env
            INFRA_POSTGRES_USER="postgres"
            INFRA_POSTGRES_DB="postgres"
            if [ -f "../infra-data/.env" ]; then
                INFRA_POSTGRES_USER=$(grep -E '^POSTGRES_USER=' "../infra-data/.env" | cut -d= -f2 | xargs)
                INFRA_POSTGRES_DB=$(grep -E '^POSTGRES_DB=' "../infra-data/.env" | cut -d= -f2 | xargs)
            fi
            INFRA_POSTGRES_USER=${INFRA_POSTGRES_USER:-postgres}
            INFRA_POSTGRES_DB=${INFRA_POSTGRES_DB:-postgres}
            
            # Đóng các kết nối đang mở trước khi drop DB
            $DOCKER_CMD exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" -c "SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname IN ('${MANTIS_DB_NAME:-mantis_db}', '${TESTLINK_DB_NAME:-testlink_db}', '${POSTGRES_DB:-qa_default_db}') AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
            
            # Xóa các database
            $DOCKER_CMD exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" -c "DROP DATABASE IF EXISTS \"${MANTIS_DB_NAME:-mantis_db}\";" || true
            $DOCKER_CMD exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" -c "DROP DATABASE IF EXISTS \"${TESTLINK_DB_NAME:-testlink_db}\";" || true
            $DOCKER_CMD exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB:-qa_default_db}\";" || true
            
            # Xóa role/user của QA Tools
            $DOCKER_CMD exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" -c "DROP ROLE IF EXISTS \"${QA_DB_USER:-admin}\";" || true
        else
            echo "⚠️ infra-postgres không chạy, không thể reset cơ sở dữ liệu."
        fi
    else
        echo "Đã giữ lại dữ liệu trong Database (Soft Reset)."
    fi
    echo "Đã reset hoàn tất. Bạn có thể khởi chạy lại bằng lệnh: ./init.sh"
else
    echo "Đã hủy bỏ hành động reset."
fi
