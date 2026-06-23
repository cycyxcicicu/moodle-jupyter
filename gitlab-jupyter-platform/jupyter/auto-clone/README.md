# Cấu hình Tự động Clone Bài Lab (Auto-Clone Assignments Mapping)

Thư mục này chứa danh sách ánh xạ các sinh viên với các bài Lab và kho lưu trữ (repository) riêng tư tương ứng trên GitLab.

## Tệp `assignments.csv`

Tệp cấu hình chính là `assignments.csv` (được mount vào container JupyterHub tại `/srv/jupyterhub/auto-clone/assignments.csv`). 

Hãy tạo tệp này dựa trên mẫu có sẵn `assignments.example.csv`:

```bash
cp assignments.example.csv assignments.csv
```

### Cấu trúc các cột dữ liệu (Schema):

*   **`username`**: Tên đăng nhập (username) của sinh viên trên GitLab (trùng với username trên JupyterHub).
*   **`project_code`**: Mã định danh của bài Lab (ví dụ: `lab01-python-basic`).
*   **`repo_http_url`**: URL HTTP truy cập repository riêng tư của sinh viên trên GitLab (ví dụ: `http://gitlab.local:8929/lab-khoa-cntt-2026/lab01-python-basic/lab01-python-basic-student01.git`).
*   **`local_dir`**: Tên thư mục sẽ được tự động clone vào trong thư mục làm việc của sinh viên trên JupyterLab (`/home/jovyan/work/<local_dir>`).
*   **`enabled`**: Trạng thái kích hoạt. Nhập `true` để tự động clone bài này, hoặc `false` để tạm thời tắt tính năng clone bài này cho sinh viên đó.

### Lưu ý quan trọng:

1.  Một sinh viên có thể có nhiều dòng tương ứng với nhiều bài Lab khác nhau. Khi container khởi động, hệ thống sẽ tự động clone tất cả các bài Lab được kích hoạt (`enabled = true`).
2.  Sau khi sửa đổi tệp `assignments.csv`, sinh viên chỉ cần khởi động lại Server cá nhân của mình (**Control Panel -> Stop My Server -> Start My Server**) để áp dụng việc clone các bài mới. Không cần phải khởi động lại toàn bộ hệ thống JupyterHub.
