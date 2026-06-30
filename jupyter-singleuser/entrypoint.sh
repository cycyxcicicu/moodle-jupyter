#!/bin/bash
set -e

# 1. Tạo thư mục work nếu chưa có
mkdir -p /home/jovyan/work

# 2. Tạo symlink tới courses và templates cho Giáo viên nếu có mount volume tương ứng
if [ -d /srv/nbgrader/courses ] && [ ! -e /home/jovyan/work/courses ]; then
    ln -s /srv/nbgrader/courses /home/jovyan/work/courses
fi
if [ -d /srv/nbgrader/templates ] && [ ! -e /home/jovyan/work/templates ]; then
    ln -s /srv/nbgrader/templates /home/jovyan/work/templates
fi

# 3. Cấu hình JupyterLab Autosave mỗi 10 giây
mkdir -p /home/jovyan/.jupyter/lab/user-settings/@jupyterlab/docmanager-extension
cat <<EOF > /home/jovyan/.jupyter/lab/user-settings/@jupyterlab/docmanager-extension/tracker.jupyterlab-settings
{
    "autosaveInterval": 10
}
EOF

# 4. Tự động clone/fetch bài làm của sinh viên từ GitLab
if [ -f /usr/local/bin/gitlab-auto-clone.py ]; then
    python3 /usr/local/bin/gitlab-auto-clone.py || echo "[Auto-Clone] Cảnh báo: Có lỗi xảy ra trong tiến trình clone nhưng vẫn tiếp tục mở JupyterLab."
fi

# 5. Chạy lệnh tiếp theo được truyền từ DockerSpawner (ví dụ: jupyterhub-singleuser)
exec "$@"

