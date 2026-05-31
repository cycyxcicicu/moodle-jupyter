#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

read -r -p "CẢNH BÁO: Hành động này sẽ xóa sạch các container, volume, dữ liệu tệp tin upload/runtime/cache của Moodle (data/moodledata) và file LTI cấu hình tự sinh. Nhập YES để tiếp tục: " confirm

# Hỗ trợ cả trường hợp người dùng bật Unikey/Telex gõ nhầm YES thành ÝES, ýes, ýe...
case "$confirm" in
    YES*|yes*|ÝES*|ýes*|Ýe*|ýe*)
        # Tiếp tục thực hiện reset
        ;;
    *)
        echo "Đã hủy bỏ lệnh reset."
        exit 0
        ;;
esac

echo "=== Đang dừng các container và xóa các volume dữ liệu ==="
docker compose down -v

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
