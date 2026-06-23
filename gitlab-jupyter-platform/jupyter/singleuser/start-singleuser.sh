#!/bin/bash
set -e

# Chạy script tự động clone bài lab của sinh viên
if [ -f /usr/local/bin/gitlab-auto-clone.py ]; then
    python3 /usr/local/bin/gitlab-auto-clone.py || echo "[Auto-Clone] Cảnh báo: Có lỗi xảy ra trong tiến trình clone nhưng vẫn tiếp tục mở JupyterLab."
fi

# Chuyển quyền thực thi sang jupyterhub-singleuser với các tham số đi kèm
exec jupyterhub-singleuser "$@"
