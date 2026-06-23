#!/usr/bin/env bash
# =========================================================================
# Script Khởi tạo JupyterHub Cô lập với GitLab OAuth
# =========================================================================

set -euo pipefail

# Xác định thư mục chứa script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}/.."

echo "========================================================================="
echo "Bắt đầu khởi tạo môi trường JupyterHub Cô lập"
echo "========================================================================="

# 1. Tạo file .env từ .env.example
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "Đang tạo file .env từ .env.example..."
        cp .env.example .env
    else
        echo "❌ Lỗi: Không tìm thấy tệp .env hoặc .env.example!"
        exit 1
    fi
fi

# 2. Sinh JUPYTERHUB_COOKIE_SECRET nếu chưa có
COOKIE_SECRET=$(grep -E "^JUPYTERHUB_COOKIE_SECRET=" .env | cut -d= -f2- | xargs || true)
if [ -z "${COOKIE_SECRET}" ]; then
    echo "Đang tạo JUPYTERHUB_COOKIE_SECRET ngẫu nhiên..."
    NEW_SECRET=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "fallbackcookie_secret_long_and_secure_value_1234")
    # Thay thế dòng JUPYTERHUB_COOKIE_SECRET= bằng giá trị mới trong .env
    sed -i.bak "s|^JUPYTERHUB_COOKIE_SECRET=.*|JUPYTERHUB_COOKIE_SECRET=${NEW_SECRET}|" .env && rm -f .env.bak
fi

# Load env variables
export $(grep -v '^#' .env | xargs)

# 3. Tạo mạng Docker dùng chung nếu chưa tồn tại
echo "Đang kiểm tra mạng Docker: ${DOCKER_NETWORK_NAME}..."
docker network inspect "${DOCKER_NETWORK_NAME}" >/dev/null 2>&1 || {
    echo "Đang tạo mạng Docker: ${DOCKER_NETWORK_NAME}..."
    docker network create "${DOCKER_NETWORK_NAME}"
}

# 4. Kiểm tra OAuth client credentials
if [ -z "${GITLAB_CLIENT_ID:-}" ] || [ -z "${GITLAB_CLIENT_SECRET:-}" ]; then
    echo "⚠️  CẢNH BÁO: 'GITLAB_CLIENT_ID' hoặc 'GITLAB_CLIENT_SECRET' hiện đang trống."
    echo "Vui lòng truy cập trang quản trị GitLab (http://gitlab.local:8929) để tạo Application,"
    echo "sau đó điền thông tin Client ID và Client Secret vào tệp: $(pwd)/.env"
fi

# 5. Build docker images
echo "Đang tiến hành build các Docker image cho JupyterHub và SingleUser..."
docker compose build jupyterhub singleuser

echo "========================================================================="
echo "🎉 KHỞI TẠO HOÀN TẤT THÀNH CÔNG!"
echo "Cách bắt đầu:"
echo "1. Đảm bảo bạn đã cấu hình Client ID và Secret trong .env"
echo "2. Chạy: ./scripts/start.sh"
echo "========================================================================="
