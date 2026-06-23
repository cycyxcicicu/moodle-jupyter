#!/usr/bin/env bash
# =========================================================================
# Script Khởi động JupyterHub Cô lập
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}/.."

# 1. Load các biến môi trường từ .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "❌ Lỗi: Không tìm thấy tệp .env. Vui lòng chạy ./scripts/init.sh trước."
    exit 1
fi

echo "========================================================================="
echo "Bắt đầu khởi động nhanh JupyterHub Stack mới"
echo "========================================================================="

# 2. Đảm bảo mạng docker dùng chung tồn tại
if ! docker network inspect "${DOCKER_NETWORK_NAME}" >/dev/null 2>&1; then
    echo "Đang tạo mạng dùng chung '${DOCKER_NETWORK_NAME}'..."
    docker network create "${DOCKER_NETWORK_NAME}"
fi

# 3. Kiểm tra xem GitLab có phản hồi không
echo "Kiểm tra kết nối tới GitLab tại ${GITLAB_URL}..."
if ! curl -s --connect-timeout 3 -o /dev/null -w "%{http_code}" "${GITLAB_URL}" | grep -qE "200|302|401|403|404" >/dev/null 2>&1; then
    echo "⚠️ Cảnh báo: Không thể kết nối trực tiếp đến GitLab (${GITLAB_URL})."
    echo "Hãy chắc chắn GitLab đang chạy để việc đăng nhập bằng OAuth diễn ra suôn sẻ."
else
    echo "Kết nối tới GitLab thành công."
fi

# 4. Khởi động các dịch vụ bằng Docker Compose
echo "Đang khởi động JupyterHub bằng Docker Compose..."
docker compose up -d

echo "========================================================================="
echo "🎉 HỆ THỐNG JUPYTERHUB MỚI ĐÃ KHỞI CHẠY!"
echo "-------------------------------------------------------------------------"
echo "Địa chỉ truy cập:   ${JUPYTERHUB_PUBLIC_URL}"
echo "Xem logs chạy:      ./scripts/logs.sh"
echo "========================================================================="
