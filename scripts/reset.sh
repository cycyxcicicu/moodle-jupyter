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

echo "=== Đang xóa các thư mục lưu trữ mặc định ==="
if [ -n "${DATA_ROOT:-}" ] && [ -d "${DATA_ROOT}" ]; then
    if [ -d "${DATA_ROOT}/jupyter_users" ]; then
        echo "   - Đang dọn dẹp thư mục jupyter_users..."
        rm -rf "${DATA_ROOT}/jupyter_users"/* 2>/dev/null || sudo rm -rf "${DATA_ROOT}/jupyter_users"/* || true
    fi
    if [ -d "${DATA_ROOT}/nbgrader/exchange" ]; then
        echo "   - Đang dọn dẹp thư mục nbgrader/exchange..."
        rm -rf "${DATA_ROOT}/nbgrader/exchange"/* 2>/dev/null || sudo rm -rf "${DATA_ROOT}/nbgrader/exchange"/* || true
    fi
else
    docker volume rm -f "${PROJECT_NAME}_jupyter_users" "${PROJECT_NAME}_nbgrader-exchange" || true
fi

# Hỏi xác nhận reset các cơ sở dữ liệu của dự án trên Postgres dùng chung (infra-postgres)
read -r -p "Bạn có muốn xóa sạch các cơ sở dữ liệu của dự án này (moodle, jupyterhub, assignment_service) trên infra-postgres? Nhập YES để xóa: " confirm_db
case "$confirm_db" in
    YES*|yes*)
        if docker ps --filter name=infra-postgres --filter status=running -q | grep -q . ; then
            echo "=== Đang xóa sạch cơ sở dữ liệu moodle, jupyterhub và assignment_service trên infra-postgres ==="
            DB_USER=${POSTGRES_ADMIN_USER:-postgres_user}
            DB_PASS=${POSTGRES_ADMIN_PASSWORD:-postgres_password}
            DB_NAME=${POSTGRES_DB:-postgres_app_db}
            MOODLE_DB_NAME=${MOODLE_DB_NAME:-moodle}
            JUPYTERHUB_DB_NAME=${JUPYTERHUB_DB_NAME:-jupyterhub}
            
            # Đóng các kết nối đang mở trước khi drop DB
            docker exec -i infra-postgres psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname IN ('$MOODLE_DB_NAME', '$JUPYTERHUB_DB_NAME', '${ASSIGNMENT_DB_NAME:-assignment_service}') AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
            
            docker exec -i infra-postgres psql -U "$DB_USER" -d "$DB_NAME" -c "DROP DATABASE IF EXISTS \"$MOODLE_DB_NAME\";" || true
            docker exec -i infra-postgres psql -U "$DB_USER" -d "$DB_NAME" -c "DROP DATABASE IF EXISTS \"$JUPYTERHUB_DB_NAME\";" || true
            docker exec -i infra-postgres psql -U "$DB_USER" -d "$DB_NAME" -c "DROP DATABASE IF EXISTS \"${ASSIGNMENT_DB_NAME:-assignment_service}\";" || true
            
            echo "Đang khởi tạo lại database moodle và jupyterhub..."
            docker exec -i infra-postgres bash /docker-entrypoint-initdb.d/01-create-app-databases.sh || true
            
            echo "Đang khởi tạo lại database assignment_service..."
            ./scripts/ensure-assignment-db.sh || true
        else
            echo "⚠️ infra-postgres không chạy, không thể reset cơ sở dữ liệu."
        fi
        ;;
    *)
        echo "Đã giữ lại dữ liệu trong Database (Soft Reset)."
        ;;
esac

# Hỏi xác nhận xóa thư mục chứa đề bài học (nbgrader-courses)
read -r -p "Bạn có muốn xóa sạch dữ liệu môn học nbgrader-courses (chứa đề bài gốc và điểm)? Nhập YES để xóa: " confirm_courses
case "$confirm_courses" in
    YES*|yes*)
        echo "Đang dọn dẹp thư mục nbgrader-courses..."
        if [ -n "${DATA_ROOT:-}" ] && [ -d "${DATA_ROOT}/nbgrader/courses" ]; then
            rm -rf "${DATA_ROOT}/nbgrader/courses"/* 2>/dev/null || sudo rm -rf "${DATA_ROOT}/nbgrader/courses"/* || true
        else
            docker volume rm -f "${PROJECT_NAME}_nbgrader-courses" || true
        fi
        ;;
    *)
        echo "Đã giữ lại dữ liệu môn học nbgrader-courses."
        ;;
esac

# Hỏi xác nhận xóa thư mục chứa template đề bài học (nbgrader-templates)
read -r -p "Bạn có muốn xóa sạch dữ liệu template đề bài nbgrader-templates? Nhập YES để xóa: " confirm_templates
case "$confirm_templates" in
    YES*|yes*)
        echo "Đang dọn dẹp thư mục nbgrader-templates..."
        if [ -n "${DATA_ROOT:-}" ] && [ -d "${DATA_ROOT}/nbgrader/templates" ]; then
            rm -rf "${DATA_ROOT}/nbgrader/templates"/* 2>/dev/null || sudo rm -rf "${DATA_ROOT}/nbgrader/templates"/* || true
        else
            docker volume rm -f "${PROJECT_NAME}_nbgrader-templates" || true
        fi
        ;;
    *)
        echo "Đã giữ lại dữ liệu template đề bài nbgrader-templates."
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
