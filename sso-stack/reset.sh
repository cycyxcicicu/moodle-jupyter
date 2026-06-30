#!/usr/bin/env bash
# =========================================================================
# Script Reset/Dọn dẹp môi trường SSO Stack
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

# 1. Nạp môi trường từ tệp .env
if [ -f .env ]; then
    set -a
    source <(sed 's/\r$//' .env)
    set +a
else
    echo "Không tìm thấy tệp .env. Không có gì để reset."
    exit 0
fi

CONFIRM_ALL="false"
WITH_DB="false"

# Phân tích tham số đầu vào
for arg in "$@"; do
    if [ "$arg" = "--with-db" ]; then
        WITH_DB="true"
    elif [ "$arg" = "-f" ]; then
        CONFIRM_ALL="true"
    fi
done

# 2. Xác nhận từ người dùng
if [ "${CONFIRM_ALL}" != "true" ]; then
    echo "========================================================================="
    echo "CẢNH BÁO: Hành động này sẽ XÓA TOÀN BỘ container và volume của SSO stack."
    if [ "${WITH_DB}" = "true" ]; then
        echo "CẢNH BÁO THÊM: Sẽ XÓA CƠ SỞ DỮ LIỆU '${KEYCLOAK_DB_NAME}' và tài khoản '${KEYCLOAK_DB_USER}' trên PostgreSQL dùng chung!"
    fi
    echo "Hành động này KHÔNG THỂ HOÀN TÁC!"
    echo "========================================================================="
    read -rp "Bạn có chắc chắn muốn tiếp tục reset? (y/N): " choice
    case "$choice" in 
      y|Y ) echo "Đang tiến hành reset...";;
      * ) echo "Đã hủy reset."; exit 0;;
    esac
fi

echo "Đang dừng các dịch vụ SSO và xóa container/volume..."
docker compose down --volumes --remove-orphans

# 3. Xử lý xóa database nếu được yêu cầu
if [ "${WITH_DB}" = "true" ]; then
    INFRA_DATA_DIR=""
    if [ -d "../infra-data" ]; then
        INFRA_DATA_DIR="../infra-data"
    elif [ -d "../../infra-data" ]; then
        INFRA_DATA_DIR="../../infra-data"
    fi

    if docker ps --format '{{.Names}}' | grep -q '^infra-postgres$'; then
        INFRA_POSTGRES_USER="postgres"
        INFRA_POSTGRES_DB="postgres"
        if [ -n "${INFRA_DATA_DIR}" ] && [ -f "${INFRA_DATA_DIR}/.env" ]; then
            # Đọc cấu hình PostgreSQL root từ infra-data/.env
            INFRA_POSTGRES_USER=$(grep -E '^POSTGRES_USER=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
            INFRA_POSTGRES_DB=$(grep -E '^POSTGRES_DB=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
        fi
        INFRA_POSTGRES_USER=${INFRA_POSTGRES_USER:-postgres}
        INFRA_POSTGRES_DB=${INFRA_POSTGRES_DB:-postgres}

        # Xác nhận thêm một lần nữa cho việc xóa database nếu không có cờ -f
        if [ "${CONFIRM_ALL}" != "true" ]; then
            read -rp "XÁC NHẬN: Bạn thực sự muốn xóa DB '${KEYCLOAK_DB_NAME}' và User '${KEYCLOAK_DB_USER}'? (y/N): " db_choice
            case "$db_choice" in 
              y|Y ) echo "Đang thực hiện xóa database...";;
              * ) echo "Đã bỏ qua bước xóa database."; WITH_DB="false";;
            esac
        fi

        if [ "${WITH_DB}" = "true" ]; then
            echo "Đang xóa database ${KEYCLOAK_DB_NAME} và role ${KEYCLOAK_DB_USER} trên infra-postgres..."
            docker exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" <<SQL
DROP DATABASE IF EXISTS "${KEYCLOAK_DB_NAME}";
DROP ROLE IF EXISTS "${KEYCLOAK_DB_USER}";
SQL
            echo "Đã dọn dẹp xong database của Keycloak."
        fi
    else
        echo "⚠️ Cảnh báo: Container 'infra-postgres' không chạy. Không thể xóa database."
    fi
fi

echo "========================================================================="
echo "Reset hoàn tất! Môi trường SSO đã được dọn sạch."
echo "Bạn có thể chạy ./init.sh để thiết lập lại."
echo "========================================================================="
