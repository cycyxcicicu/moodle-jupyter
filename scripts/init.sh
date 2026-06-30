#!/bin/bash
set -euo pipefail

# Di chuyển về thư mục gốc của dự án
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Tự động cấp quyền thực thi cho toàn bộ các script trong thư mục scripts
chmod +x "$SCRIPT_DIR"/*.sh 2>/dev/null || true

echo "========================================================="
echo "  Bắt đầu cài đặt hệ thống Moodle + JupyterHub LTI 1.3"
echo "========================================================="

# 1. Kiểm tra Docker & Compose
./scripts/check-docker.sh

# 2. Tạo file .env từ .env.example nếu chưa có
if [[ ! -f .env && -f .env.example ]]; then
    cp .env.example .env
    echo "Đã tạo file .env từ .env.example."
fi

# Load các biến môi trường
set -a
. ./.env
set +a

# 2.5. Kiểm tra/Tạo mạng Docker 'infra-data-net' nếu chưa có
echo "=== Kiểm tra Docker Network ==="
if ! docker network inspect infra-data-net >/dev/null 2>&1; then
    echo "⚠️ Mạng 'infra-data-net' chưa tồn tại. Tiến hành tạo mới..."
    docker network create infra-data-net
    echo "Đã tạo mạng 'infra-data-net'."
else
    echo "Mạng 'infra-data-net' đã tồn tại."
fi

# 3. Đảm bảo file cấu hình LTI tồn tại trước khi build
./scripts/ensure-generated-env.sh

# 3.5. Kiểm tra và khởi động database dùng chung (infra-postgres)
echo "=== Kiểm tra PostgreSQL dùng chung (infra-postgres) ==="
if ! docker ps --filter name=infra-postgres --filter status=running -q | grep -q . ; then
    echo "⚠️ Phát hiện container 'infra-postgres' chưa khởi chạy."
    if [ -d "infra-data" ]; then
        echo "Đang tự động khởi chạy stack infra-data..."
        docker compose -f infra-data/docker-compose.yml up -d postgres
    else
        echo "❌ Lỗi: Không tìm thấy thư mục infra-data để khởi động database."
        exit 1
    fi
fi

# 3.6. Đợi Postgres sẵn sàng & Khởi tạo database và phân quyền cho các service
./scripts/wait-postgres.sh
./scripts/ensure-assignment-db.sh

# 4. Build các container
echo "Đang build các container..."
docker compose --profile jupyterhub-spawnable build jupyter-singleuser jupyter-assignment-service jupyterhub moodle

# 5. Khởi động Moodle trước (chưa khởi động jupyterhub)
echo "Đang khởi động Moodle..."
docker compose up -d moodle

# 7. Đợi Moodle hoàn tất cài đặt cơ sở dữ liệu
./scripts/wait-moodle.sh

# 8. Cấu hình LTI và sinh ra file generated/lti.env
if ! ./scripts/configure-lti.sh; then
    echo "Lỗi: Không thể cấu hình LTI và sinh file generated/lti.env."
    exit 1
fi

# 8.5. Cấu hình Keycloak OAuth2 SSO cho Moodle
if ! ./scripts/configure-oauth2.sh; then
    echo "Lỗi: Không thể cấu hình Keycloak OAuth2 cho Moodle."
    exit 1
fi

# 9. Khởi động và bắt buộc tạo lại container jupyterhub cùng jupyter-assignment-service để nạp env mới cập nhật
echo "Đang khởi động và tạo lại JupyterHub & Assignment Service..."
docker compose up -d --force-recreate jupyterhub jupyter-assignment-service moodle-cron

# 9.5. Khởi tạo cấu trúc thư mục và dữ liệu mẫu cho nbgrader từ DATA_ROOT
echo "Đang khởi tạo cấu trúc thư mục và dữ liệu mẫu cho nbgrader..."
mkdir -p "${DATA_ROOT}/nbgrader/courses" "${DATA_ROOT}/nbgrader/templates" "${DATA_ROOT}/nbgrader/exchange"
docker run --rm -u root \
  -v "${DATA_ROOT}/nbgrader/courses:/srv/nbgrader/courses" \
  -v "${DATA_ROOT}/nbgrader/templates:/srv/nbgrader/templates" \
  -v "${DATA_ROOT}/nbgrader/exchange:/srv/nbgrader/exchange" \
  -v "$PROJECT_ROOT/scripts:/tmp/scripts" \
  moodle-jupyter-singleuser:latest \
  bash -c "
    mkdir -p /srv/nbgrader/templates/moodle_teacher_demo/python_basic/lab01_function && \
    python3 /tmp/scripts/create_sample_assignment.py /srv/nbgrader/templates/moodle_teacher_demo/python_basic/lab01_function/lab01_function.ipynb && \
    python3 /usr/local/bin/create_nbgrader_course.py --nbgrader-course-id course_demo && \
    cp -r /srv/nbgrader/templates/moodle_teacher_demo/python_basic/lab01_function /srv/nbgrader/courses/course_demo/source/ && \
    mkdir -p /srv/nbgrader/exchange && \
    chmod -R 777 /srv/nbgrader && \
    JOVYAN_UID=\$(id -u jovyan) && \
    JOVYAN_GID=\$(id -g jovyan) && \
    chown -R \$JOVYAN_UID:\$JOVYAN_GID /srv/nbgrader || true
  "

# 10. Chạy doctor chẩn đoán sức khỏe hệ thống
echo "Đang chạy chẩn đoán sức khỏe..."
./scripts/doctor.sh

echo ""
echo "=== THIẾT LẬP HOÀN TẤT ==="
echo "Moodle Web:             http://moodle.school.local:${MOODLE_HOST_PORT:-18080}"
echo "JupyterHub Web:         http://jupyterhub.school.local:${JUPYTERHUB_HOST_PORT:-18000}"
echo "PostgreSQL Host:        localhost:${POSTGRES_HOST_PORT:-15432}"
echo "Tài khoản Admin Moodle: ${MOODLE_ADMIN_USER:-admin} / ${MOODLE_ADMIN_PASSWORD:-admin123}"
echo ""
echo "LƯU Ý: Vui lòng đảm bảo đã thêm các dòng cấu hình sau vào file 'hosts' của máy host"
echo "(Windows: C:\\Windows\\System32\\drivers\\etc\\hosts hoặc WSL/Linux: /etc/hosts):"
echo "127.0.0.1 moodle.school.local"
echo "127.0.0.1 jupyterhub.school.local"
echo "127.0.0.1 keycloak.school.local"
echo "127.0.0.1 gitlab.school.local"
echo "127.0.0.1 openwebui.school.local"
echo "========================================================="
