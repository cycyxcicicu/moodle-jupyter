#!/usr/bin/env bash
# =========================================================================
# GitLab & MinIO Local Start Script
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

# 1. Load các biến môi trường từ .env
if [ -f .env ]; then
    # Load env variables excluding comments
    export $(grep -v '^#' .env | xargs)
else
    echo "❌ Lỗi: Không tìm thấy tệp .env. Vui lòng chạy ./init.sh trước để khởi tạo môi trường."
    exit 1
fi

echo "========================================================================="
echo "Bắt đầu khởi động nhanh GitLab CE & MinIO Local"
echo "========================================================================="

# 2. Đảm bảo mạng docker dùng chung tồn tại
if ! docker network inspect infra-data-net >/dev/null 2>&1; then
    echo "Đang tạo mạng dùng chung 'infra-data-net'..."
    docker network create infra-data-net
fi

# 3. Kiểm tra các dịch vụ phụ thuộc nếu sử dụng Database/Redis ngoài
if [ "${USE_EXTERNAL_DB_REDIS}" = "true" ]; then
    # Tìm thư mục infra-data để tự khởi chạy khi cần
    INFRA_DATA_DIR=""
    if [ -d "../infra-data" ]; then
        INFRA_DATA_DIR="../infra-data"
    elif [ -d "../../infra-data" ]; then
        INFRA_DATA_DIR="../../infra-data"
    fi

    # === Kiểm tra PostgreSQL dùng chung ===
    if ! docker ps --filter name=infra-postgres --filter status=running -q | grep -q . ; then
        echo "⚠️ Phát hiện container 'infra-postgres' chưa khởi chạy."
        if [ -n "${INFRA_DATA_DIR}" ]; then
            echo "Đang tự động khởi chạy stack infra-data (PostgreSQL)..."
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

    # === Kiểm tra Redis dùng chung ===
    if ! docker ps --filter name=infra-redis --filter status=running -q | grep -q . ; then
        echo "⚠️ Phát hiện container 'infra-redis' chưa khởi chạy."
        if [ -n "${INFRA_DATA_DIR}" ]; then
            echo "Đang tự động khởi chạy stack infra-data (Redis)..."
            docker compose -f "${INFRA_DATA_DIR}/docker-compose.yml" up -d redis
        else
            echo "❌ Lỗi: Không tìm thấy thư mục infra-data để khởi động Redis."
            exit 1
        fi
    fi

    echo "Đang chờ infra-redis sẵn sàng kết nối (healthy)..."
    until [ "$(docker inspect -f '{{.State.Health.Status}}' infra-redis 2>/dev/null)" = "healthy" ]; do
        echo -n "."
        sleep 2
    done
    echo " infra-redis đã sẵn sàng!"
fi

# 4. Khởi động các dịch vụ của GitLab & MinIO
echo "Đang khởi động các dịch vụ GitLab & MinIO bằng Docker Compose..."
docker compose up -d

# 5. Chờ GitLab phản hồi HTTP 200/302
echo "========================================================================="
echo "Đang chờ máy chủ GitLab phản hồi trên cổng ${GITLAB_HTTP_PORT}..."
echo "========================================================================="

until curl -s -o /dev/null -w "%{http_code}" "http://localhost:${GITLAB_HTTP_PORT}/help" | grep -qE "200|302" >/dev/null 2>&1; do
    echo -n "."
    sleep 5
done

echo ""
echo "🎉 GitLab Web Server đã sẵn sàng!"

echo "========================================================================="
echo "🎉 KHỞI ĐỘNG HỆ THỐNG THÀNH CÔNG!"
echo "-------------------------------------------------------------------------"
echo "Trang quản trị GitLab:   http://localhost:${GITLAB_HTTP_PORT} (hoặc http://${GITLAB_HOST}:${GITLAB_HTTP_PORT})"
echo "Tài khoản mặc định:      root / ${GITLAB_ROOT_PASSWORD}"
echo "-------------------------------------------------------------------------"
echo "Trang quản lý MinIO:     http://localhost:${MINIO_CONSOLE_PORT}"
echo "========================================================================="
