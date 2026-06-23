# GitLab Repository Provisioning Tool (Phase 2A)

Tool này hỗ trợ tự động hóa việc tạo repository private cho từng sinh viên, gán quyền và sao chép bài mẫu (assignment-template) sang repository riêng của sinh viên.

---

## 1. Yêu Cầu Hệ Thống (Chạy Local)

Để chạy được script này trên máy cá nhân hoặc máy chủ quản trị:
- **Python 3.8+** và **pip**
- **Git CLI** (được thêm vào biến môi trường PATH để script gọi lệnh thông qua command line)

---

## 2. Hướng Dẫn Cài Đặt (Chạy Local)

### Bước 1: Di chuyển tới thư mục tool
```bash
cd gitlab-jupyter-platform/jupyter/tools/repo-provisioning/
```

### Bước 2: Cài đặt thư viện phụ thuộc (Dependencies)
Tạo môi trường ảo và cài đặt thư viện cần thiết:
```bash
python3 -m venv .venv
# Trên Linux/macOS:
source .venv/bin/activate
# Trên Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### Bước 3: Tạo file cấu hình môi trường `.env`
Sao chép file `.env.example` thành `.env`:
```bash
cp .env.example .env
```
Mở `.env` và thiết lập các giá trị:
- `GITLAB_URL`: URL truy cập instance GitLab (mặc định: `http://gitlab.local:8929`).
- `GITLAB_ADMIN_TOKEN`: Personal Access Token của admin hoặc DevOps admin (xem hướng dẫn tạo ở mục 3).
- `DEFAULT_BRANCH`: Tên branch mặc định (mặc định: `main`).

> [!CAUTION]
> **Bảo mật:** Tuyệt đối không commit file `.env` lên git. File này đã được đưa vào `.gitignore`.

---

## 3. Cách Tạo Personal Access Token trên GitLab

Để script có thể gọi API GitLab tạo dự án và gán quyền sinh viên:

1. Đăng nhập vào GitLab với tài khoản Admin (`root`).
2. Nhấp vào **Avatar** ở góc trên bên phải → chọn **Preferences**.
3. Chọn **Access Tokens** ở menu bên trái.
4. Nhấp vào **Add new token**:
   - **Token name**: `repo-provisioning-token`
   - **Expiration date**: Chọn ngày hết hạn phù hợp (hoặc bỏ trống).
   - **Scopes**: Tích chọn duy nhất ô **`api`**.
5. Nhấp **Create personal access token**.
6. Sao chép chuỗi token hiển thị (token này chỉ xuất hiện duy nhất 1 lần) và lưu lại để dùng cho cấu hình.

---

## 4. Cấu Hình Tự Động Hóa Qua GitLab CI/CD Pipeline

Để thực thi việc cấp phát bài tự động thông qua giao diện Web của GitLab bằng GitLab CI/CD, làm theo các bước sau:

### Bước 1: Cấu hình CI/CD Variables
Đưa Token bảo mật vào cấu hình dự án `lab-provisioning` thay vì lưu trong code:
1. Truy cập dự án `lab-provisioning` trên GitLab.
2. Chọn **Settings → CI/CD** ở menu bên trái.
3. Tìm đến phần **Variables** và nhấn **Expand**.
4. Bấm **Add variable** để thêm 2 biến sau:
   - Biến 1: 
     - **Key**: `GITLAB_URL`
     - **Value**: `http://gitlab-ce:8929` (đường dẫn mạng Docker nội bộ của GitLab).
   - Biến 2:
     - **Key**: `GITLAB_ADMIN_TOKEN`
     - **Value**: *[Dán mã Personal Access Token đã tạo ở mục 3 vào đây]*
     - **Tùy chọn**: Tích chọn **Mask variable** để ẩn token khỏi log của pipeline.

### Bước 2: Tạo và khởi chạy Container GitLab Runner
Chạy lệnh sau trên terminal WSL để tạo một Runner chuyên dụng nằm chung mạng nội bộ với GitLab:
```bash
docker run -d --name gitlab-runner --restart always \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --network gitlab-local-net \
  gitlab/gitlab-runner:latest
```

### Bước 3: Đăng ký Runner với hệ thống GitLab
1. Truy cập trang quản trị GitLab Admin Area để lấy Token đăng ký:
   👉 **`http://gitlab.local:8929/admin/runners`**
2. Nhấp vào **New instance runner** (hoặc copy mã token đăng ký hiện có ở góc trên bên phải).
3. Chạy lệnh sau trên terminal WSL (thay thế `<REGISTRATION_TOKEN>` bằng token vừa copy):
   ```bash
   docker exec -it gitlab-runner gitlab-runner register \
     --url "http://gitlab-ce:8929/" \
     --registration-token "<REGISTRATION_TOKEN>" \
     --executor "docker" \
     --docker-image "python:3.11-slim" \
     --docker-volumes "/var/run/docker.sock:/var/run/docker.sock" \
     --non-interactive
   ```

### Bước 4: Cho phép Runner nhận các Job không gắn thẻ (Untagged Jobs)
Vì Job chạy script trong `.gitlab-ci.yml` mặc định không khai báo tag, bạn cần cấu hình cho phép Runner nhận các job này:
1. Truy cập: `http://gitlab.local:8929/admin/runners`
2. Nhấp vào nút **Edit (hình bút chì)** bên cạnh Runner vừa đăng ký.
3. Tích chọn vào ô **`Run untagged jobs`**.
4. Nhấn **Save changes**.

### Bước 5: Cấu hình Mạng & DNS nội bộ cho Runner
Để container của Runner có thể clone code từ mạng nội bộ `gitlab-local-net`, ta cần sửa file cấu hình `config.toml` của Runner:
1. Copy file cấu hình ra ngoài máy host để sửa:
   ```bash
   sudo docker cp gitlab-runner:/etc/gitlab-runner/config.toml ./config.toml
   sudo chown $(whoami):$(whoami) ./config.toml
   ```
2. Mở file `config.toml` và bổ sung 2 cấu hình sau:
   - Thêm `clone_url = "http://gitlab-ce:8929/"` vào dưới `[[runners]]`.
   - Thêm `network_mode = "gitlab-local-net"` vào dưới `[runners.docker]`.
3. Copy đè ngược lại vào container và khởi động lại:
   ```bash
   sudo docker cp ./config.toml gitlab-runner:/etc/gitlab-runner/config.toml
   rm ./config.toml
   sudo docker restart gitlab-runner
   ```

---

## 5. Chuẩn Bị Dữ Liệu Trước Khi Chạy

1. **Tạo target group/subgroup** trên GitLab để chứa bài làm học viên:
   - Ví dụ: `lab-khoa-cntt-2026/lab01-python-basic`.
2. **Tạo đề bài mẫu (Template Project)**:
   - Tạo dự án `assignment-template` nằm dưới subgroup trên (đường dẫn: `lab-khoa-cntt-2026/lab01-python-basic/assignment-template`).
   - Push đề bài mẫu hoặc các file Jupyter Notebook lên dự án này.
3. **Chuẩn bị danh sách CSV sinh viên**:
   - Chỉnh sửa file `configs/lab01-example/students.example.csv`:
     ```csv
     username,repo_name,maintainers
     student01,lab01-python-basic-student01,teacher01;root
     student02,lab01-python-basic-student02,teacher01;root
     huynhduc23,lab01-python-basic-huynhduc23,teacher01;root
     ```

---

## 6. Thực Thi Hướng Dẫn Chạy Pipeline

### 1. Chạy thử mô phỏng (Dry-run)
Đảm bảo file `.gitlab-ci.yml` có flag `--dry-run` ở dòng cuối phần script:
```yaml
  script:
    - |
      python create_lab_repos.py \
        --csv "$CSV_PATH" \
        --target-group-path "$TARGET_GROUP_PATH" \
        --template-project-path "$TEMPLATE_PROJECT_PATH" \
        --dry-run
```
Push thay đổi lên, truy cập:
👉 **`http://gitlab.local:8929/root/lab-provisioning/-/pipelines`**
Chạy pipeline mới nhất bằng cách bấm **Play** ở Job `provision_lab`. Kiểm tra xem báo cáo tổng kết cuối log có hiển thị lỗi nào không.

### 2. Chạy thật (Tạo dữ liệu thực tế)
Mở file `.gitlab-ci.yml`, xóa bỏ flag `--dry-run` ở cuối script và push lên git:
```bash
git add .gitlab-ci.yml
git commit -m "remove dry-run for actual run"
git push
```
Vào lại trang Pipelines trên GitLab và bấm **Play** ở Job `provision_lab` của Pipeline mới nhất để hệ thống chính thức tự động hóa tạo Repo, phân quyền và copy đề bài mẫu.

---

## 7. Kế Hoạch Phase 2B (Auto Clone Trên JupyterHub)

Khi triển khai Phase 2B (Tự động clone bài làm vào Workspace của user khi JupyterHub container khởi động):
1. **Chuẩn bị singleuser image:** Tích hợp sẵn gói `git` và extension `jupyterlab-git` vào Dockerfile của user notebook (`singleuser/Dockerfile`).
2. **Cấu hình LifeCycle Hook trong JupyterHub:**
   - Sử dụng tùy chọn `c.Spawner.post_start_hook` hoặc viết script khởi động `/usr/local/bin/before-notebook.d/` bên trong container của user.
   - Script khởi động sẽ đọc biến môi trường `JUPYTERHUB_USER` (tương ứng với username GitLab của học viên).
   - Kiểm tra nếu thư mục bài tập của học viên chưa tồn tại trong workspace (`/home/jovyan/workspace`), thực hiện lệnh clone tự động thông qua giao thức HTTPS đã chèn Access Token ngắn hạn hoặc SSH key:
     `git clone http://oauth2:<token>@gitlab.local:8929/lab-khoa-cntt-2026/lab01-python-basic/lab01-python-basic-<username>.git`
