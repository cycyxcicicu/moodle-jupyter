#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

read -r -p "CẢNH BÁO: Hành động này sẽ xóa sạch các container, volume, dữ liệu tệp tin upload/runtime/cache của Moodle (data/moodledata) và file LTI cấu hình tự sinh. Nhập YES để tiếp tục: " confirm

# Hỗ trợ cả trường hợp người dùng bật Unikey/Telex gõ nhầm YES thành ÝES, ýes, ýe...
case "$confirm" in
    YES*|yes*)
        # Tiếp tục thực hiện reset
        ;;
    *)
        echo "Đã hủy bỏ lệnh reset."
        exit 0
        ;;
esac

# Load các biến môi trường để lấy PROJECT_NAME
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi
PROJECT_NAME=${PROJECT_NAME:-moodle-jupyter-platform}

echo "=== Đang dừng các container ==="
docker compose down

echo "=== Đang xóa các volume dữ liệu mặc định ==="
docker volume rm -f "${PROJECT_NAME}_postgres_data" "${PROJECT_NAME}_jupyter_users" "${PROJECT_NAME}_nbgrader-exchange" || true

# Hỏi xác nhận xóa volume chứa đề bài học (nbgrader-courses)
read -r -p "Bạn có muốn xóa sạch volume dữ liệu môn học nbgrader-courses (chứa đề bài gốc và điểm)? Nhập YES để xóa: " confirm_courses
case "$confirm_courses" in
    YES*|yes*)
        echo "Đang xóa volume dữ liệu môn học nbgrader-courses..."
        docker volume rm -f "${PROJECT_NAME}_nbgrader-courses" || true
        ;;
    *)
        echo "Đã giữ lại volume dữ liệu môn học nbgrader-courses."
        ;;
esac

# Hỏi xác nhận xóa volume chứa template đề bài học (nbgrader-templates)
read -r -p "Bạn có muốn xóa sạch volume dữ liệu template đề bài nbgrader-templates? Nhập YES để xóa: " confirm_templates
case "$confirm_templates" in
    YES*|yes*)
        echo "Đang xóa volume dữ liệu template đề bài nbgrader-templates..."
        docker volume rm -f "${PROJECT_NAME}_nbgrader-templates" || true
        ;;
    *)
        echo "Đã giữ lại volume dữ liệu template đề bài nbgrader-templates."
        ;;
esac

# Dọn dẹp các container single-user của học viên do DockerSpawner tạo ra
singleuser_containers=$(docker ps -a --filter name="^jupyter-" -q || true)
if [ -n "$singleuser_containers" ]; then
    echo "Đang dừng và xóa các container single-user của học viên..."
    docker rm -f $singleuser_containers || true
fi

# Dọn dẹp các volume single-user của học viên do DockerSpawner tạo ra
singleuser_volumes=$(docker volume ls -q --filter name="^jupyterhub-user-" || true)
if [ -n "$singleuser_volumes" ]; then
    echo "Đang xóa các volume lưu trữ của học viên..."
    docker volume rm -f $singleuser_volumes || true
fi

echo "Đang dọn dẹp thư mục dữ liệu moodledata trên máy host..."
if [ -d "data/moodledata" ]; then
    find data/moodledata -mindepth 1 ! -name '.gitkeep' -depth -delete 2>/dev/null || \
    sudo find data/moodledata -mindepth 1 ! -name '.gitkeep' -depth -delete
fi

echo "Đang xóa các thư mục cache của Python..."
rm -rf jupyterhub/__pycache__

echo "Đang dọn dẹp các tệp cấu hình LTI tự sinh..."
rm -f generated/lti.env

echo "Quá trình reset hệ thống đã hoàn tất thành công!"
