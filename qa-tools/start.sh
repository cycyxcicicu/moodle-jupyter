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
echo "Đang khởi chạy các container QA Tools..."
$DOCKER_CMD compose up -d

echo "========================================================="
echo "   Hệ thống QA Tools đang được khởi chạy!"
echo "---------------------------------------------------------"
echo " Mantis Bug Tracker: http://localhost:${MANTIS_PORT:-18081}"
echo " TestLink:           http://localhost:${TESTLINK_PORT:-18082}"
echo " PostgreSQL Host:    localhost:${POSTGRES_PORT:-15433}"
echo "---------------------------------------------------------"
echo " Chạy chẩn đoán hệ thống bằng doctor.sh..."
./doctor.sh
