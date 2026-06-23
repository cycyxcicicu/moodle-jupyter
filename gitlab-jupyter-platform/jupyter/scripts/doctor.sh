#!/usr/bin/env bash
# =========================================================================
# Script Kiểm tra & Xác minh Hệ thống (Doctor Check) cho JupyterHub mới
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}/.."

echo "========================================================================="
echo "Đang chạy JupyterHub Local Doctor (Kiểm tra hệ thống)"
echo "========================================================================="

# Định dạng output màu sắc
success() { echo -e "\e[32m[PASS]\e[0m $1"; }
warning() { echo -e "\e[33m[WARN]\e[0m $1"; }
error() { echo -e "\e[31m[FAIL]\e[0m $1"; }

# 1. Load các biến môi trường
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
    success "Đã nạp tệp .env thành công."
else
    error "Không tìm thấy tệp .env. Vui lòng chạy ./scripts/init.sh trước."
    exit 1
fi

# 2. Kiểm tra Docker
if ! docker info >/dev/null 2>&1; then
    error "Docker daemon chưa được chạy. Vui lòng mở Docker Desktop hoặc khởi động docker daemon."
    exit 1
else
    success "Docker daemon đang chạy bình thường."
fi

if ! docker compose version >/dev/null 2>&1; then
    error "Docker Compose V2 chưa được cài đặt."
    exit 1
else
    success "Docker Compose đã sẵn sàng."
fi

# 3. Kiểm tra kết nối GitLab
echo "Kiểm tra kết nối tới GitLab (${GITLAB_URL})..."
GL_HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "${GITLAB_URL}/help" || echo "000")
if [ "${GL_HTTP_STATUS}" = "200" ] || [ "${GL_HTTP_STATUS}" = "302" ]; then
    success "Kết nối tới GitLab thành công (HTTP ${GL_HTTP_STATUS})."
else
    warning "Không thể kết nối tốt tới GitLab (${GITLAB_URL}/help trả về HTTP ${GL_HTTP_STATUS})."
    echo "Hãy chắc chắn GitLab container đang chạy."
fi

# 4. Kiểm tra OAuth Client ID & Secret
if [ -z "${GITLAB_CLIENT_ID:-}" ] || [ -z "${GITLAB_CLIENT_SECRET:-}" ]; then
    error "Chưa điền 'GITLAB_CLIENT_ID' hoặc 'GITLAB_CLIENT_SECRET' trong tệp .env!"
else
    success "Đã cấu hình OAuth client credentials."
fi

# 5. Kiểm tra Docker Network
if docker network inspect "${DOCKER_NETWORK_NAME}" >/dev/null 2>&1; then
    success "Mạng Docker '${DOCKER_NETWORK_NAME}' tồn tại."
else
    error "Mạng Docker '${DOCKER_NETWORK_NAME}' chưa được tạo. Chạy ./scripts/init.sh để tạo."
fi

# 6. Kiểm tra các container trong stack JupyterHub
echo "Kiểm tra trạng thái container..."
if docker ps --format '{{.Names}}' | grep -q '^gitlab-jupyterhub$'; then
    success "Container 'gitlab-jupyterhub' đang chạy."
    
    # Kiểm tra cổng public của hub có phản hồi không
    HUB_HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "http://localhost:${JUPYTERHUB_HOST_PORT}/hub/health" || echo "000")
    if [ "${HUB_HTTP_STATUS}" = "200" ]; then
        success "JupyterHub API phản hồi tốt trên cổng ${JUPYTERHUB_HOST_PORT} (HTTP 200)."
    else
        warning "JupyterHub trên cổng ${JUPYTERHUB_HOST_PORT} trả về HTTP ${HUB_HTTP_STATUS} (Mong đợi 200)."
    fi
else
    warning "Container 'gitlab-jupyterhub' KHÔNG chạy."
fi

# 7. Quét các container của singleuser đang chạy
SINGLEUSER_COUNT=$(docker ps --filter "name=gitlab-jupyter-" --format '{{.Names}}' | grep -v "gitlab-jupyterhub" | wc -l || echo "0")
if [ "${SINGLEUSER_COUNT}" -gt 0 ]; then
    success "Phát hiện có ${SINGLEUSER_COUNT} container singleuser đang chạy:"
    docker ps --filter "name=gitlab-jupyter-" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -v "gitlab-jupyterhub"
else
    echo "Không có container singleuser của học viên nào đang chạy (đây là trạng thái bình thường khi chưa có ai đăng nhập)."
fi

echo "========================================================================="
echo "Kiểm tra doctor hoàn tất."
echo "========================================================================="
