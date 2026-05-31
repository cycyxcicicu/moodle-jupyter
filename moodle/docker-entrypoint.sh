#!/bin/bash
set -euo pipefail

echo "=== Bắt đầu thiết lập Moodle Entrypoint ==="

# 1. Đẩy các biến môi trường Docker vào file envvars của Apache để PHP getenv() có thể đọc được
echo "Đang truyền các biến môi trường Docker sang Apache..."
for var in $(env | cut -f1 -d=); do
    if [[ "$var" =~ ^(POSTGRES_|MOODLE_|TZ|LTI_) ]]; then
        echo "export $var=\"${!var}\"" >> /etc/apache2/envvars
    fi
done

# 2. Định nghĩa hàm sửa quyền thư mục Moodle data
fix_moodledata_permissions() {
    echo "Đang sửa quyền thư mục Moodle data..."
    mkdir -p /var/moodledata/cache/cachestore_file/default_application
    mkdir -p /var/moodledata/localcache /var/moodledata/temp
    chown -R www-data:www-data /var/moodledata
    chmod -R 775 /var/moodledata
}

# Khởi tạo quyền thư mục lần đầu
fix_moodledata_permissions

run_as_www_data() {
    runuser -u www-data -- "$@"
}

# 3. Đợi Database PostgreSQL sẵn sàng kết nối
echo "Đang chờ cơ sở dữ liệu PostgreSQL sẵn sàng..."
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
    echo "Đang đợi CSDL khởi động..."
    sleep 2
done

# 4. Kiểm tra xem CSDL Moodle đã được cài đặt chưa, nếu chưa thì tự động cài đặt qua CLI
echo "Đang kiểm tra trạng thái CSDL Moodle..."
if ! PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1 FROM mdl_config LIMIT 1;" >/dev/null 2>&1; then
    echo "CSDL Moodle chưa được cài đặt. Tiến hành cài đặt tự động qua CLI..."
    
    # Sửa quyền trước khi chạy Moodle install CLI
    fix_moodledata_permissions
    
    run_as_www_data php -d max_input_vars=5000 /var/www/html/admin/cli/install_database.php \
        --agree-license \
        --adminuser="${MOODLE_ADMIN_USER:-admin}" \
        --adminpass="${MOODLE_ADMIN_PASSWORD:-admin123}" \
        --adminemail="${MOODLE_ADMIN_EMAIL:-admin@example.com}" \
        --fullname="Moodle Jupyter Platform" \
        --shortname="MJP" \
        --lang="${MOODLE_LANG:-en}"
    
    # Sửa quyền ngay sau khi chạy Moodle install CLI
    fix_moodledata_permissions
    echo "Cài đặt CSDL Moodle hoàn tất."
else
    echo "CSDL Moodle đã được cài đặt trước đó."
fi

# Đảm bảo quyền được sửa lần cuối trước khi start Apache
fix_moodledata_permissions

echo "Đang khởi động máy chủ web Apache..."
exec apachectl -D FOREGROUND
