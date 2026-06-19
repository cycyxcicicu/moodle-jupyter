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

# 3. Đảm bảo file cấu hình LTI tồn tại trước khi build
./scripts/ensure-generated-env.sh

# 4. Build các container
echo "Đang build các container..."
docker compose --profile jupyterhub-spawnable build jupyter-singleuser jupyter-assignment-service jupyterhub moodle postgres

# 5. Khởi động Postgres và Moodle trước (chưa khởi động jupyterhub)
echo "Đang khởi động PostgreSQL và Moodle..."
docker compose up -d postgres moodle

# 6. Đợi Postgres sẵn sàng & tạo xong 2 database
./scripts/wait-postgres.sh

# 7. Đợi Moodle hoàn tất cài đặt cơ sở dữ liệu
./scripts/wait-moodle.sh

# 8. Cấu hình LTI và sinh ra file generated/lti.env
if ! ./scripts/configure-lti.sh; then
    echo "Lỗi: Không thể cấu hình LTI và sinh file generated/lti.env."
    exit 1
fi

# 9. Khởi động và bắt buộc tạo lại container jupyterhub cùng jupyter-assignment-service để nạp env mới cập nhật
echo "Đang khởi động và tạo lại JupyterHub & Assignment Service..."
docker compose up -d --force-recreate jupyterhub jupyter-assignment-service

# 9.5. Khởi tạo cấu trúc thư mục và dữ liệu mẫu cho nbgrader
echo "Đang khởi tạo cấu trúc thư mục và dữ liệu mẫu cho nbgrader..."
docker run --rm -u root \
  -v "${PROJECT_NAME:-moodle-jupyter-platform}_nbgrader-courses:/srv/nbgrader/courses" \
  -v "${PROJECT_NAME:-moodle-jupyter-platform}_nbgrader-templates:/srv/nbgrader/templates" \
  -v "${PROJECT_NAME:-moodle-jupyter-platform}_nbgrader-exchange:/srv/nbgrader/exchange" \
  -v "$PROJECT_ROOT/scripts:/tmp/scripts" \
  moodle-jupyter-singleuser:latest \
  bash -c "
    mkdir -p /srv/nbgrader/templates/moodle_teacher_demo/python_basic/lab01_function && \
    python3 /tmp/scripts/create_sample_assignment.py /srv/nbgrader/templates/moodle_teacher_demo/python_basic/lab01_function/lab01_function.ipynb && \
    python3 /usr/local/bin/create_nbgrader_course.py --nbgrader-course-id moodle_course_demo && \
    cp -r /srv/nbgrader/templates/moodle_teacher_demo/python_basic/lab01_function /srv/nbgrader/courses/moodle_course_demo/source/ && \
    mkdir -p /srv/nbgrader/exchange && \
    chmod 777 /srv/nbgrader/exchange && \
    JOVYAN_UID=\$(id -u jovyan) && \
    JOVYAN_GID=\$(id -g jovyan) && \
    chown -R \$JOVYAN_UID:\$JOVYAN_GID /srv/nbgrader
  "

# 10. Chạy doctor chẩn đoán sức khỏe hệ thống
echo "Đang chạy chẩn đoán sức khỏe..."
./scripts/doctor.sh

echo ""
echo "=== THIẾT LẬP HOÀN TẤT ==="
echo "Moodle Web:             http://localhost:${MOODLE_HOST_PORT:-18080}"
echo "JupyterHub Web:         http://localhost:${JUPYTERHUB_HOST_PORT:-18000}"
echo "PostgreSQL Host:        localhost:${POSTGRES_HOST_PORT:-15432}"
echo "Tài khoản Admin Moodle: ${MOODLE_ADMIN_USER:-admin} / ${MOODLE_ADMIN_PASSWORD:-admin123}"
echo "========================================================="
