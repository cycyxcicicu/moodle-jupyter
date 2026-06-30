#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Load environment variables
if [ -f .env ]; then
    set -a
    source <(sed 's/\r$//' .env)
    set +a
fi

echo "=== Đang chạy cấu hình Keycloak OAuth2 trong Moodle ==="

# 1. Đảm bảo moodle container đang chạy
if ! docker compose ps -q moodle > /dev/null 2>&1; then
    echo "Lỗi: Container moodle chưa khởi chạy hoặc chưa sẵn sàng."
    exit 1
fi

# 2. Sao chép file setup_keycloak_oauth2.php vào container
echo "Đang sao chép setup_keycloak_oauth2.php vào container moodle..."
docker compose cp moodle/config/setup_keycloak_oauth2.php moodle:/usr/local/share/moodle/setup_keycloak_oauth2.php

# 3. Chạy PHP CLI script
if ! output=$(docker compose exec -T moodle php -f /usr/local/share/moodle/setup_keycloak_oauth2.php 2>&1); then
    echo "Lỗi: Khởi chạy setup_keycloak_oauth2.php thất bại."
    echo "$output"
    exit 1
fi

echo "$output"

# 4. Sửa quyền ngay sau khi purge cache từ PHP CLI (vì php chạy bằng root có thể tạo lại thư mục cache sở hữu bởi root)
docker compose exec -T moodle bash -c "mkdir -p /var/moodledata/cache/cachestore_file/default_application && chown -R www-data:www-data /var/moodledata && chmod -R 775 /var/moodledata"

echo "=== Cấu hình Keycloak OAuth2 thành công! ==="
