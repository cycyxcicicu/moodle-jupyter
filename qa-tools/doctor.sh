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

echo "========================================================="
echo "  Bắt đầu kiểm tra chẩn đoán hệ thống QA Tools..."
echo "========================================================="

echo "✅ Docker daemon đang hoạt động."

# Nạp file .env nếu có
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
else
    echo "⚠️ Cảnh báo: File .env không tồn tại. Sử dụng giá trị mặc định."
    QA_DB_USER=admin
    POSTGRES_DB=qa_default_db
    POSTGRES_PORT=15434
    MANTIS_PORT=18081
    TESTLINK_PORT=18082
fi

# 2. Kiểm tra trạng thái các container
containers=("infra-postgres" "mantis-app" "testlink-app")
all_running=true

for container in "${containers[@]}"; do
    status=$($DOCKER_CMD inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo "not_found")
    if [ "$status" = "running" ]; then
        # Check health if container has healthcheck configured
        health=$($DOCKER_CMD inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}healthy{{end}}' "$container" 2>/dev/null || echo "healthy")
        if [ "$health" = "healthy" ]; then
            echo "✅ Container '$container' đang chạy (healthy)."
        elif [ "$health" = "starting" ]; then
            echo "⚠️ Container '$container' đang chạy và đang khởi động (starting)."
        else
            echo "❌ Container '$container' đang chạy nhưng không lành mạnh (Trạng thái: $health)."
            all_running=false
        fi
    elif [ "$status" = "not_found" ]; then
        echo "❌ Container '$container' chưa được khởi tạo."
        all_running=false
    else
        echo "❌ Container '$container' đang dừng (Trạng thái: $status)."
        all_running=false
    fi
done

# 3. Kiểm tra cổng kết nối trên Host
check_port() {
    local port=$1
    local name=$2
    if timeout 1 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/$port" 2>/dev/null; then
        echo "✅ Cổng $port ($name) đang lắng nghe kết nối."
    else
        echo "❌ Cổng $port ($name) KHÔNG phản hồi."
        all_running=false
    fi
}

echo "---------------------------------------------------------"
echo "Kiểm tra cổng kết nối trên localhost:"
check_port "${POSTGRES_PORT:-15434}" "PostgreSQL"
check_port "${MANTIS_PORT:-18081}" "MantisBT"
check_port "${TESTLINK_PORT:-18082}" "TestLink"

# 4. Kiểm tra kết nối cơ sở dữ liệu
echo "---------------------------------------------------------"
echo "Kiểm tra kết nối CSDL trong container infra-postgres:"
if $DOCKER_CMD exec -i infra-postgres pg_isready -U "${QA_DB_USER:-admin}" -d "${POSTGRES_DB:-qa_default_db}" >/dev/null 2>&1; then
    echo "✅ Kết nối PostgreSQL thành công (sẵn sàng kết nối)."
else
    echo "❌ Lỗi: PostgreSQL chưa sẵn sàng hoặc thông tin kết nối sai."
    all_running=false
fi

# Kết luận
echo "========================================================="
if [ "$all_running" = true ]; then
    echo "🎉 Mọi dịch vụ QA Tools hoạt động bình thường!"
    echo "Mantis Bug Tracker: http://localhost:${MANTIS_PORT:-18081}"
    echo "TestLink:           http://localhost:${TESTLINK_PORT:-18082}"
else
    echo "❌ Có sự cố xảy ra trong hệ thống QA Tools."
    echo "Hướng dẫn sửa lỗi:"
    echo "  1. Kiểm tra log chi tiết: `./logs.sh`"
    echo "  2. Restart lại hệ thống: `./stop.sh && ./init.sh`"
fi
echo "========================================================="
