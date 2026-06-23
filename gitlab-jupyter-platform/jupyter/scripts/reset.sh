#!/usr/bin/env bash
# =========================================================================
# Script Khôi phục/Reset Môi trường JupyterHub Cô lập
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}/.."

# 1. Load các biến môi trường
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "Không tìm thấy tệp .env. Không có gì để reset."
    exit 0
fi

# 2. Xác nhận trước khi xóa dữ liệu người dùng
CONFIRM=${1:-""}
if [ "${CONFIRM}" != "-f" ]; then
    echo "========================================================================="
    echo "CẢNH BÁO: Hành động này sẽ DỪNG JupyterHub và XÓA TOÀN BỘ container"
    echo "         và volume lưu trữ dữ liệu của học viên (gitlab-jupyter-user-*)."
    echo "         Hành động này KHÔNG THỂ đảo ngược!"
    echo "========================================================================="
    read -rp "Bạn có chắc chắn muốn reset toàn bộ stack JupyterHub mới không? (y/N): " choice
    case "$choice" in 
      y|Y ) echo "Đang tiến hành reset...";;
      * ) echo "Đã hủy thao tác reset."; exit 0;;
    esac
fi

# 3. Dừng các dịch vụ chính của JupyterHub
echo "Đang dừng các dịch vụ chính và xóa container, volume của JupyterHub..."
docker compose down --volumes --remove-orphans

# 4. Tìm và xóa các container singleuser của học viên
echo "Đang tìm và dọn dẹp các container singleuser của học viên (dạng gitlab-jupyter-*)..."
STUDENT_CONTAINERS=$(docker ps -a --filter "name=gitlab-jupyter-" --format '{{.Names}}' | grep -v "gitlab-jupyterhub" || true)
if [ -n "${STUDENT_CONTAINERS}" ]; then
    echo "Đang xóa các container: ${STUDENT_CONTAINERS}"
    echo "${STUDENT_CONTAINERS}" | xargs -r docker rm -f
else
    echo "Không phát hiện container singleuser nào."
fi

# 5. Tìm và xóa các volume dữ liệu học viên
echo "Đang tìm và dọn dẹp các volume dữ liệu học viên (dạng gitlab-jupyter-user-*)..."
STUDENT_VOLUMES=$(docker volume ls --filter "name=gitlab-jupyter-user-" --format '{{.Name}}' || true)
if [ -n "${STUDENT_VOLUMES}" ]; then
    echo "Đang xóa các volume: ${STUDENT_VOLUMES}"
    echo "${STUDENT_VOLUMES}" | xargs -r docker volume rm
else
    echo "Không phát hiện volume học viên nào."
fi

echo "========================================================================="
echo "Quá trình reset JupyterHub hoàn tất! Hệ thống đã hoàn toàn sạch."
echo "Bạn có thể chạy lại ./scripts/init.sh để cấu hình lại."
echo "========================================================================="
