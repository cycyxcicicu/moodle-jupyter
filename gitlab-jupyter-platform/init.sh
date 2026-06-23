#!/usr/bin/env bash
# =========================================================================
# Script Khởi tạo GitLab Local (WSL2 / Linux)
# =========================================================================

set -euo pipefail

# Xác định thư mục chứa script này
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

echo "========================================================================="
echo "Bắt đầu khởi tạo môi trường GitLab CE & MinIO Local"
echo "========================================================================="

# 1. Tải các biến môi trường
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "Đang tạo file .env từ .env.example..."
        cp .env.example .env
    else
        echo "❌ Lỗi: Không tìm thấy tệp .env hoặc .env.example!"
        exit 1
    fi
fi

# Nạp các biến môi trường từ .env
export $(grep -v '^#' .env | xargs)

# 2. Kiểm tra và tạo cấu trúc thư mục mount dữ liệu
echo "Đang tạo các thư mục mount dữ liệu..."
mkdir -p "${GITLAB_CONFIG_DIR}"
mkdir -p "${GITLAB_LOGS_DIR}"
mkdir -p "${GITLAB_DATA_DIR}"
mkdir -p "${MINIO_DATA_DIR}"

# 3. Tạo mạng Docker dùng chung nếu chưa tồn tại
echo "Đang kiểm tra mạng dùng chung infra-data-net..."
docker network inspect infra-data-net >/dev/null 2>&1 || {
    echo "Đang tạo mạng dùng chung infra-data-net..."
    docker network create infra-data-net
}

# 4. Xử lý cơ sở dữ liệu và Redis ngoài nếu được bật
if [ "${USE_EXTERNAL_DB_REDIS}" = "true" ]; then
    echo "Đang sử dụng PostgreSQL & Redis ngoài."

    # Xác định đường dẫn tương đối tới thư mục infra-data
    INFRA_DATA_DIR=""
    if [ -d "../../infra-data" ]; then
        INFRA_DATA_DIR="../../infra-data"
    elif [ -d "../infra-data" ]; then
        INFRA_DATA_DIR="../infra-data"
    fi

    # Đợi container infra-postgres sẵn sàng
    echo "=== Kiểm tra PostgreSQL dùng chung (infra-postgres) ==="
    if ! docker ps --filter name=infra-postgres --filter status=running -q | grep -q . ; then
        echo "⚠️ Phát hiện container 'infra-postgres' chưa khởi chạy."
        if [ -n "${INFRA_DATA_DIR}" ]; then
            echo "Đang tự động khởi chạy stack infra-data..."
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

    # Đọc cấu hình superuser và db mặc định từ infra-data/.env nếu có
    INFRA_POSTGRES_USER="postgres"
    INFRA_POSTGRES_DB="postgres"
    if [ -f "${INFRA_DATA_DIR}/.env" ]; then
        INFRA_POSTGRES_USER=$(grep -E '^POSTGRES_USER=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
        INFRA_POSTGRES_DB=$(grep -E '^POSTGRES_DB=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
    fi
    INFRA_POSTGRES_USER=${INFRA_POSTGRES_USER:-postgres}
    INFRA_POSTGRES_DB=${INFRA_POSTGRES_DB:-postgres}

    # Kiểm tra và khởi tạo database ngoài
    echo "Đang khởi tạo database và tài khoản người dùng ngoài (Sử dụng user: ${INFRA_POSTGRES_USER}, db: ${INFRA_POSTGRES_DB})..."
    docker exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" <<SQL
-- Tạo role/user nếu chưa có
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${EXTERNAL_DB_USER}') THEN
    CREATE ROLE "${EXTERNAL_DB_USER}" LOGIN PASSWORD '${EXTERNAL_DB_PASSWORD}';
  ELSE
    ALTER ROLE "${EXTERNAL_DB_USER}" WITH LOGIN PASSWORD '${EXTERNAL_DB_PASSWORD}';
  END IF;
END
\$\$;

-- Tạo database nếu chưa có
SELECT 'CREATE DATABASE "${EXTERNAL_DB_NAME}" OWNER "${EXTERNAL_DB_USER}"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${EXTERNAL_DB_NAME}')\gexec

-- Cấp quyền hạn truy cập database
ALTER DATABASE "${EXTERNAL_DB_NAME}" OWNER TO "${EXTERNAL_DB_USER}";
GRANT ALL PRIVILEGES ON DATABASE "${EXTERNAL_DB_NAME}" TO "${EXTERNAL_DB_USER}";
SQL

    # Kích hoạt các extension cần thiết trong database gitlab
    echo "Đang bật các extension pg_trgm, btree_gist..."
    docker exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${EXTERNAL_DB_NAME}" <<SQL
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS plpgsql;
SQL

    # Đợi container infra-redis sẵn sàng
    echo "=== Kiểm tra Redis dùng chung (infra-redis) ==="
    if ! docker ps --filter name=infra-redis --filter status=running -q | grep -q . ; then
        echo "⚠️ Phát hiện container 'infra-redis' chưa khởi chạy."
        if [ -n "${INFRA_DATA_DIR}" ]; then
            echo "Đang tự động khởi chạy stack infra-data..."
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

# 5. Sinh tệp cấu hình gitlab.rb
GITLAB_RB_PATH="${GITLAB_CONFIG_DIR}/gitlab.rb"
echo "Đang sinh tệp cấu hình GitLab: ${GITLAB_RB_PATH}..."

cat <<EOF > "${GITLAB_RB_PATH}"
# =========================================================================
# GitLab Configuration File (Auto-generated by init.sh)
# =========================================================================

# GitLab URL & Port
external_url "http://${GITLAB_HOST}:${GITLAB_HTTP_PORT}"
nginx['listen_port'] = 8929
nginx['listen_https'] = false

# GitLab Shell SSH port
gitlab_rails['gitlab_shell_ssh_port'] = ${GITLAB_SSH_PORT}

# Initial Root Password (for first installation)
gitlab_rails['initial_root_password'] = '${GITLAB_ROOT_PASSWORD}'

# =========================================================================
# Resource Optimization (Tailored for low RAM)
# =========================================================================
# Puma Web Server (Single mode)
puma['worker_processes'] = 0
puma['min_threads'] = 2
puma['max_threads'] = 4

# Sidekiq Background Jobs
sidekiq['concurrency'] = 5

# Monitoring - Disable all prometheus/grafana tools to save RAM (~1GB)
prometheus_monitoring['enable'] = false
prometheus['enable'] = false
alertmanager['enable'] = false
node_exporter['enable'] = false
redis_exporter['enable'] = false
postgres_exporter['enable'] = false
pgbouncer_exporter['enable'] = false
gitlab_exporter['enable'] = false
grafana['enable'] = false

# =========================================================================
# Object Storage (MinIO Integration)
# =========================================================================
gitlab_rails['object_store']['enabled'] = true
gitlab_rails['object_store']['connection'] = {
  'provider' => 'AWS',
  'region' => 'us-east-1',
  'aws_access_key_id' => '${MINIO_ROOT_USER}',
  'aws_secret_access_key' => '${MINIO_ROOT_PASSWORD}',
  'endpoint' => 'http://gitlab-minio:9000',
  'path_style' => true
}
gitlab_rails['object_store']['objects']['artifacts']['bucket'] = 'gitlab-artifacts'
gitlab_rails['object_store']['objects']['external_diffs']['bucket'] = 'gitlab-external-diffs'
gitlab_rails['object_store']['objects']['lfs']['bucket'] = 'gitlab-lfs'
gitlab_rails['object_store']['objects']['uploads']['bucket'] = 'gitlab-uploads'
gitlab_rails['object_store']['objects']['packages']['bucket'] = 'gitlab-packages'
gitlab_rails['object_store']['objects']['dependency_proxy']['bucket'] = 'gitlab-dependency-proxy'
gitlab_rails['object_store']['objects']['terraform_state']['bucket'] = 'gitlab-terraform-state'
gitlab_rails['object_store']['objects']['pages']['bucket'] = 'gitlab-pages'

# Backups to MinIO
gitlab_rails['backup_upload_connection'] = {
  'provider' => 'AWS',
  'region' => 'us-east-1',
  'aws_access_key_id' => '${MINIO_ROOT_USER}',
  'aws_secret_access_key' => '${MINIO_ROOT_PASSWORD}',
  'endpoint' => 'http://gitlab-minio:9000',
  'path_style' => true
}
gitlab_rails['backup_upload_remote_directory'] = 'gitlab-backups'

# =========================================================================
# External Database & Redis Configuration
# =========================================================================
EOF

if [ "${USE_EXTERNAL_DB_REDIS}" = "true" ]; then
    cat <<EOF >> "${GITLAB_RB_PATH}"
# Disabled bundled services
postgresql['enable'] = false
redis['enable'] = false

# External PostgreSQL
gitlab_rails['db_adapter'] = 'postgresql'
gitlab_rails['db_encoding'] = 'utf8'
gitlab_rails['db_host'] = '${EXTERNAL_DB_HOST}'
gitlab_rails['db_port'] = ${EXTERNAL_DB_PORT}
gitlab_rails['db_username'] = '${EXTERNAL_DB_USER}'
gitlab_rails['db_password'] = '${EXTERNAL_DB_PASSWORD}'
gitlab_rails['db_database'] = '${EXTERNAL_DB_NAME}'

# External Redis
gitlab_rails['redis_host'] = '${EXTERNAL_REDIS_HOST}'
gitlab_rails['redis_port'] = ${EXTERNAL_REDIS_PORT}
gitlab_rails['redis_password'] = '${EXTERNAL_REDIS_PASSWORD}'
EOF
else
    cat <<EOF >> "${GITLAB_RB_PATH}"
# Using Bundled (Internal) PostgreSQL and Redis
postgresql['enable'] = true
redis['enable'] = true
EOF
fi

echo "Đã tạo thành công tệp cấu hình gitlab.rb."

# 6. Xây dựng và khởi chạy các container
echo "Đang khởi động các dịch vụ bằng Docker Compose..."
docker compose up -d

# 7. Đợi GitLab sẵn sàng và khởi tạo xong database
echo "========================================================================="
echo "Đang chờ máy chủ GitLab phản hồi trên cổng ${GITLAB_HTTP_PORT}..."
echo "Lưu ý: Quá trình khởi tạo lần đầu tiên có thể mất từ 3 - 5 phút để chạy database migrations."
echo "========================================================================="

# Vòng lặp kiểm tra cho đến khi trang web của GitLab phản hồi HTTP 200 hoặc 302
until curl -s -o /dev/null -w "%{http_code}" "http://localhost:${GITLAB_HTTP_PORT}/help" | grep -qE "200|302" >/dev/null 2>&1; do
    echo -n "."
    sleep 5
done

echo ""
echo "🎉 GitLab Web Server đã phản hồi!"

# 8. Tự động kiểm tra và khởi tạo tài khoản quản trị root nếu chưa có
echo "Đang kiểm tra và khởi tạo tài khoản quản trị root trong cơ sở dữ liệu..."
docker exec -i gitlab-ce gitlab-rails runner "
u = User.find_by_username('root')
if u.nil?
  puts 'User root chua ton tai. Dang khoi tao...'
  u = User.new(
    name: 'Administrator',
    username: 'root',
    email: 'admin@example.com',
    password: '${GITLAB_ROOT_PASSWORD}',
    password_confirmation: '${GITLAB_ROOT_PASSWORD}',
    admin: true
  )
  if u.respond_to?(:assign_personal_namespace)
    org = defined?(Organizations::Organization) ? Organizations::Organization.default_organization : nil
    u.assign_personal_namespace(org) if org
  end
  u.skip_confirmation!
  if u.save
    puts 'Da tao thanh cong user root!'
  else
    puts 'Loi tao user root: ' + u.errors.full_messages.join(', ')
  end
else
  puts 'User root da ton tai. Dam bao mat khau dung voi .env...'
  u.password = '${GITLAB_ROOT_PASSWORD}'
  u.password_confirmation = '${GITLAB_ROOT_PASSWORD}'
  if u.save
    puts 'Da cap nhat mat khau cho user root!'
  else
    puts 'Khong the cap nhat mat khau cho user root: ' + u.errors.full_messages.join(', ')
  end
end
" || echo "⚠️ Cảnh báo: Không thể kết nối tới gitlab-rails để cấu hình user root tự động. Hãy tự kiểm tra hoặc đăng nhập lại."

echo "========================================================================="
echo "🎉 QUÁ TRÌNH KHỞI TẠO HOÀN TẤT THÀNH CÔNG!"
echo "-------------------------------------------------------------------------"
echo "Trang quản trị GitLab:   http://localhost:${GITLAB_HTTP_PORT} (hoặc http://${GITLAB_HOST}:${GITLAB_HTTP_PORT})"
echo "Tài khoản mặc định:      root / ${GITLAB_ROOT_PASSWORD}"
echo "-------------------------------------------------------------------------"
echo "Trang quản lý MinIO:     http://localhost:${MINIO_CONSOLE_PORT}"
echo "-------------------------------------------------------------------------"
echo "Lưu ý cấu hình tên miền ảo trên Windows:"
echo "Nếu bạn muốn dùng tên miền http://${GITLAB_HOST}:${GITLAB_HTTP_PORT},"
echo "hãy thêm dòng sau vào file 'hosts' của Windows (C:\Windows\System32\drivers\etc\hosts):"
echo "127.0.0.1 ${GITLAB_HOST}"
echo "========================================================================="
