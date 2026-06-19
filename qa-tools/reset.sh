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
    echo "Đang xóa sạch tài nguyên và volume của QA Tools..."
    $DOCKER_CMD compose down -v --remove-orphans
    echo "Đã reset hoàn tất. Bạn có thể khởi chạy lại bằng lệnh: ./init.sh"
else
    echo "Đã hủy bỏ hành động reset."
fi
