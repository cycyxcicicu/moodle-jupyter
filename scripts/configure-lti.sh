#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Load environment variables
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

echo "=== Đang chạy thiết lập cấu hình LTI Tool trong Moodle ==="

# Chạy PHP CLI script để cài đặt/cập nhật cấu hình trong Moodle
if ! output=$(docker compose exec -T moodle php -f /usr/local/share/moodle/setup_jupyter_lti.php 2>&1); then
    echo "Lỗi: Khởi chạy setup_jupyter_lti.php thất bại."
    echo "$output"
    exit 1
fi

# Sửa quyền ngay sau khi purge cache từ PHP CLI (vì php chạy bằng root có thể tạo lại thư mục cache sở hữu bởi root)
docker compose exec -T moodle bash -c "mkdir -p /var/moodledata/cache/cachestore_file/default_application && chown -R www-data:www-data /var/moodledata && chmod -R 775 /var/moodledata"

echo "=== Truy vấn trực tiếp PostgreSQL để cấu hình LTI ==="
DB_USER=${POSTGRES_ADMIN_USER:-postgres}
DB_PASS=${POSTGRES_ADMIN_PASSWORD:-postgres}
DB_NAME=${MOODLE_DB_NAME:-moodle}

# 1. Truy vấn lấy typeid và clientid từ mdl_lti_types
db_info=$(docker compose exec -T postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT id, clientid FROM mdl_lti_types WHERE name = 'JupyterHub' ORDER BY id LIMIT 1;" 2>/dev/null || echo "")

if [ -z "$db_info" ]; then
    echo "Lỗi: Không thể kết nối cơ sở dữ liệu hoặc không tìm thấy LTI Tool 'JupyterHub' trong bảng mdl_lti_types."
    exit 1
fi

typeid=$(echo "$db_info" | cut -d'|' -f1)
clientid=$(echo "$db_info" | cut -d'|' -f2)

if [ -z "$clientid" ]; then
    echo "Lỗi: Không thể sinh lti.env vì thiếu clientid trong Moodle."
    exit 1
fi

# 2. Truy vấn các cấu hình bắt buộc trong mdl_lti_types_config
toolurl=$(docker compose exec -T postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT value FROM mdl_lti_types_config WHERE typeid = '$typeid' AND name = 'toolurl';" 2>/dev/null || echo "")
initiatelogin=$(docker compose exec -T postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT value FROM mdl_lti_types_config WHERE typeid = '$typeid' AND name = 'initiatelogin';" 2>/dev/null || echo "")
publickeyset=$(docker compose exec -T postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT value FROM mdl_lti_types_config WHERE typeid = '$typeid' AND name = 'publickeyset';" 2>/dev/null || echo "")
redirectionuris=$(docker compose exec -T postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT value FROM mdl_lti_types_config WHERE typeid = '$typeid' AND name = 'redirectionuris';" 2>/dev/null || echo "")
keytype=$(docker compose exec -T postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT value FROM mdl_lti_types_config WHERE typeid = '$typeid' AND name = 'keytype';" 2>/dev/null || echo "")

if [ -z "$toolurl" ]; then
    echo "Lỗi: Không thể sinh lti.env vì thiếu cấu hình LTI trong Moodle (thiếu toolurl)."
    exit 1
fi
if [ -z "$initiatelogin" ]; then
    echo "Lỗi: Không thể sinh lti.env vì thiếu cấu hình LTI trong Moodle (thiếu initiatelogin)."
    exit 1
fi
if [ -z "$publickeyset" ]; then
    echo "Lỗi: Không thể sinh lti.env vì thiếu cấu hình LTI trong Moodle (thiếu publickeyset)."
    exit 1
fi
if [ -z "$redirectionuris" ]; then
    echo "Lỗi: Không thể sinh lti.env vì thiếu cấu hình LTI trong Moodle (thiếu redirectionuris)."
    exit 1
fi
if [ -z "$keytype" ]; then
    echo "Lỗi: Không thể sinh lti.env vì thiếu cấu hình LTI trong Moodle (thiếu keytype)."
    exit 1
fi

# 3. Chuẩn bị ghi tệp cấu hình tạm
MOODLE_WWWROOT=${MOODLE_WWWROOT:-http://localhost:18080}
JUPYTERHUB_URL=${JUPYTERHUB_URL:-http://localhost:18000}

mkdir -p generated
cat << EOF > generated/lti.env.tmp
LTI13_CLIENT_ID=$clientid
LTI13_ISSUER=$MOODLE_WWWROOT
LTI13_AUTHORIZE_URL=$MOODLE_WWWROOT/mod/lti/auth.php
LTI13_TOKEN_URL=$MOODLE_WWWROOT/mod/lti/token.php
LTI13_JWKS_URL=$MOODLE_WWWROOT/mod/lti/certs.php
LTI13_REDIRECT_URI=$JUPYTERHUB_URL/hub/lti13/oauth_callback
LTI13_LAUNCH_URL=$JUPYTERHUB_URL/hub/lti13/oauth_login

# Các biến tương thích cũ
MOODLE_ISSUER=$MOODLE_WWWROOT
MOODLE_CLIENT_ID=$clientid
MOODLE_AUTHORIZE_URL=$MOODLE_WWWROOT/mod/lti/auth.php
MOODLE_JWKS_URL=http://moodle/mod/lti/certs.php
EOF

# 4. Kiểm tra file tạm
if [ ! -f generated/lti.env.tmp ]; then
    echo "Lỗi: Không thể sinh lti.env vì file tạm không được tạo."
    exit 1
fi

if [ ! -s generated/lti.env.tmp ]; then
    echo "Lỗi: Không thể sinh lti.env vì file tạm rỗng."
    rm -f generated/lti.env.tmp
    exit 1
fi

required_vars=("LTI13_CLIENT_ID" "LTI13_ISSUER" "LTI13_AUTHORIZE_URL" "LTI13_TOKEN_URL" "LTI13_JWKS_URL" "LTI13_REDIRECT_URI" "LTI13_LAUNCH_URL")
for var in "${required_vars[@]}"; do
    val=$(grep "^${var}=" generated/lti.env.tmp | cut -d= -f2-)
    if [ -z "$val" ]; then
        echo "Lỗi: Không thể sinh lti.env vì thiếu hoặc rỗng biến $var."
        rm -f generated/lti.env.tmp
        exit 1
    fi
done

# 5. Ghi đè file chính thức bằng cách đổi tên file tạm
mv generated/lti.env.tmp generated/lti.env
echo "Đã sinh generated/lti.env thành công"
echo "========================================================="
