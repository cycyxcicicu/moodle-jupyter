# GitLab CE & MinIO Local Environment (WSL2) - GitLab Jupyter Platform

Tài liệu này hướng dẫn cách cài đặt, vận hành và quản lý hệ thống GitLab CE và MinIO tích hợp làm Object Storage giả lập S3 trên môi trường WSL2 Ubuntu. Hệ thống này được đặt trong thư mục `gitlab-jupyter-platform` để làm tiền đề tích hợp JupyterHub OAuth sau này.

---

## Kiến trúc Hệ thống

Hệ thống bao gồm các dịch vụ:
1. **GitLab CE** (Port HTTP: `8929`, SSH: `2424`): Hệ thống quản lý mã nguồn.
2. **MinIO** (Port API: `9000`, Console: `9001`): Đóng vai trò Object Storage S3-compatible để lưu trữ:
   * Artifacts của GitLab CI/CD.
   * LFS (Large File Storage).
   * User uploads (avatar, file đính kèm issue/merge request).
   * Bản backup của GitLab.
3. **MinIO Creator**: Tự động khởi tạo các bucket cần thiết trên MinIO khi hệ thống khởi động.

---

## Cấu trúc Thư mục

```text
gitlab-jupyter-platform/
├── .env.example        # File cấu hình mẫu
├── .env                # File cấu hình thực tế (được bỏ qua bởi git)
├── .gitignore          # Cấu hình bỏ qua các file sinh tự động & nhạy cảm
├── docker-compose.yml  # Định nghĩa các dịch vụ Docker (sau này thêm JupyterHub vào đây)
├── init.sh             # Script khởi tạo tự động (Tạo folder, cấu hình DB ngoài, setup admin)
├── start.sh            # Script khởi động nhanh hàng ngày (Kiểm tra DB/Redis ngoài và start stack)
├── stop.sh             # Script dừng hệ thống
├── logs.sh             # Script xem log của hệ thống
├── reset.sh            # Script xóa sạch dữ liệu để cấu hình lại từ đầu
├── doctor.sh           # Script kiểm tra sức khỏe và chẩn đoán lỗi hệ thống
├── backup.sh           # Script sao lưu dữ liệu toàn diện (GitLab + MinIO + Config)
└── README.md           # Hướng dẫn sử dụng này
```

---

## Hướng dẫn Vận hành nhanh (Quick Start)

### Bước 1: Cấu hình tên miền ảo (Hosts)

Thêm bản ghi sau vào file `hosts` của máy tính để truy cập thông qua tên miền `gitlab.local`:

*   **Trên Windows** (`C:\Windows\System32\drivers\etc\hosts` chạy quyền Administrator):
    ```text
    127.0.0.1 gitlab.local
    ```
*   **Trên WSL Ubuntu** (`/etc/hosts`):
    ```text
    127.0.0.1 gitlab.local
    ```

### Bước 2: Cấu hình Môi trường (`.env`)

Sao chép file `.env.example` thành `.env` (nếu chưa chạy `init.sh`):
```bash
cp .env.example .env
```
Mặc định hệ thống lưu trữ dữ liệu tại phân vùng đĩa D (`/mnt/d/NCKH/e-learning/gitlab-jupyter-platform/...`). Bạn có thể chỉnh sửa các biến mount path trong `.env` để phù hợp với máy mình.

### Bước 3: Khởi tạo hệ thống (`init.sh`) hoặc Khởi động nhanh (`start.sh`)

* **Khởi tạo lần đầu tiên**:
  Chạy script khởi tạo để thiết lập thư mục lưu trữ, cấu hình database và khởi tạo tài khoản quản trị:
  ```bash
  chmod +x *.sh
  ./init.sh
  ```
  *Script sẽ tự động tạo thư mục, khởi tạo database/user trong `infra-postgres`, sinh file cấu hình `gitlab.rb`, chờ dịch vụ sẵn sàng và tạo tài khoản `root`.*

* **Khởi động lại các lần tiếp theo (Khởi động nhanh)**:
  Khi hệ thống đã được khởi tạo trước đó, hãy sử dụng script khởi động nhanh để tránh ghi đè cấu hình và database:
  ```bash
  ./start.sh
  ```
  *Script này sẽ kiểm tra xem PostgreSQL và Redis ngoài đã chạy chưa (nếu chưa sẽ tự khởi chạy), sau đó khởi động nhanh GitLab/MinIO và chờ dịch vụ phản hồi.*

### Bước 4: Kiểm tra trạng thái và Đăng nhập

Kiểm tra quá trình khởi chạy:
```bash
./doctor.sh
# Hoặc xem log thời gian thực:
./logs.sh gitlab
```
*Lưu ý: GitLab CE mất khoảng 2-5 phút để hoàn tất khởi động lần đầu tiên.*

Khi trạng thái `./doctor.sh` báo `healthy`, truy cập:
*   **GitLab Web UI**: [http://gitlab.local:8929](http://gitlab.local:8929)
    *   **Username**: `root`
    *   **Password**: Lấy giá trị cấu hình của `GITLAB_ROOT_PASSWORD` trong file `.env` (mặc định là `gitlab_root_password`).
*   **MinIO Console**: [http://localhost:9001](http://localhost:9001)
    *   **Username**: `minioadmin`
    *   **Password**: `minioadmin`

---

## Các tính năng tối ưu cốt lõi

### 1. Tùy chọn Database & Redis (`USE_EXTERNAL_DB_REDIS`)
Trong file `.env`:
*   `USE_EXTERNAL_DB_REDIS=false` (Mặc định): Chạy độc lập hoàn toàn, GitLab tự quản lý PostgreSQL & Redis đi kèm bên trong container.
*   `USE_EXTERNAL_DB_REDIS=true`: Tắt database & Redis bên trong GitLab để tiết kiệm **2-3GB RAM**. Hệ thống sẽ tự động tạo database `gitlabhq_production` và kết nối tới container `infra-postgres` và `infra-redis` dùng chung của dự án qua mạng `infra-data-net`.

### 2. Tối ưu hóa RAM cho máy cá nhân
Các cấu hình sau đã được thiết lập mặc định trong file `gitlab.rb` sinh ra từ `init.sh` để giảm mức tiêu thụ RAM xuống dưới 2.5GB (chế độ external):
*   Tắt toàn bộ hệ thống Prometheus & Grafana exporter (`prometheus_monitoring['enable'] = false`).
*   Chạy Puma ở chế độ Single mode (`puma['worker_processes'] = 0`).
*   Giới hạn Sidekiq concurrency ở mức thấp (`sidekiq['max_concurrency'] = 5`).

### 3. Xử lý lỗi Permission trên WSL2 (NTFS mount)
Nếu phân vùng đĩa D (định dạng NTFS) của bạn gặp lỗi phân quyền khi chạy GitLab (thường gặp lỗi Permission Denied ở tiến trình `gitaly` hoặc `postgres`), hãy thực hiện:
1. Sửa biến `GITLAB_DATA_DIR` trong `.env` trỏ về phân vùng ext4 của WSL2:
   ```text
   GITLAB_DATA_DIR=/home/huynhduc/gitlab-jupyter-platform/gitlab/data
   ```
2. Chạy lại `./init.sh`.
*Các thư mục cấu hình `GITLAB_CONFIG_DIR`, logs `GITLAB_LOGS_DIR` và dữ liệu MinIO `MINIO_DATA_DIR` vẫn có thể giữ nguyên ở đĩa D.*

---

## Hướng dẫn Sao lưu và Phục hồi (Backup & Restore)

### Sao lưu dữ liệu (`backup.sh`)
Chạy script sao lưu:
```bash
./backup.sh
```
Script sẽ tự động:
1. Kích hoạt lệnh backup của GitLab (sao lưu các git repositories).
2. Sao lưu file cấu hình `gitlab.rb` và mã hóa bí mật `gitlab-secrets.json` (bắt buộc phải có để restore).
3. Nén toàn bộ dữ liệu MinIO Object Storage (`minio_data.tar.gz`).
4. Thu thập toàn bộ các file trên vào thư mục dạng: `backup/backup_YYYYMMDD_HHMMSS/`.

---

## Các Lệnh Quản lý Tiện ích

*   **Khởi tạo lần đầu**: `./init.sh`
*   **Khởi động nhanh**: `./start.sh`
*   **Dừng hệ thống**: `./stop.sh`
*   **Xem logs**: `./logs.sh` (hoặc `./logs.sh gitlab` để xem riêng log GitLab)
*   **Kiểm tra hệ thống**: `./doctor.sh`
*   **Xóa sạch dữ liệu**: `./reset.sh` (Cảnh báo: Lệnh này xóa toàn bộ dữ liệu hiện có để khởi tạo lại)

---

## Bước Tiếp Theo: Tích Hợp JupyterHub

Sau khi đã thiết lập và chạy thành công GitLab cùng MinIO ở môi trường này, bạn hãy chuyển sang thư mục con `jupyter` để cài đặt và tích hợp JupyterHub OAuth cùng cơ chế tự động đồng bộ bài làm cho sinh viên.

👉 Xem hướng dẫn chi tiết tại: [jupyter/README.md](file:///wsl.localhost/Ubuntu/home/huynhduc/e-learning-huce/moodle-jupyter-platform/gitlab-jupyter-platform/jupyter/README.md)
