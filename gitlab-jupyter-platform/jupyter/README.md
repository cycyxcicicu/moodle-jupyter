# Hướng dẫn Vận hành Phase 2B: JupyterHub GitLab Auto-Clone

Tài liệu này hướng dẫn quản trị viên triển khai, cấu hình và kiểm thử tính năng **Tự động clone/push bài làm cho sinh viên** trên JupyterHub sử dụng chính tài khoản GitLab của sinh viên.

---

## 1. Cấu hình Scopes trên GitLab (Bắt buộc)

Do tool cần quyền clone và push bài làm của chính sinh viên, ứng dụng OAuth JupyterHub trên GitLab phải được cấp quyền truy cập kho lưu trữ:

1. Đăng nhập vào GitLab bằng tài khoản quản trị viên (`root`).
2. Đi tới **Admin Area** -> **Applications** -> Chọn **JupyterHub GitLab OAuth** (hoặc tên ứng dụng OAuth bạn đã tạo).
3. Tại phần **Scopes**, tích chọn thêm các quyền sau (lưu ý bổ sung đầy đủ):
   * `read_user`
   * `openid`
   * `profile`
   * `email`
   * `read_api` (Mới - Rất quan trọng để gọi API quét dự án)
   * `read_repository` (Mới - Để clone bài lab)
   * `write_repository` (Mới - Để sinh viên push bài làm)
4. Nhấn **Save changes** để lưu lại.
5. *Lưu ý:* Sau bước này, sinh viên cần **Logout** (Đăng xuất) khỏi JupyterHub và đăng nhập lại để hệ thống nhận diện Token mới có đủ quyền hạn.

---

## 2. Cấu hình File Môi trường `.env` của Jupyter

Mở file `gitlab-jupyter-platform/jupyter/.env` và thêm/cập nhật các dòng cấu hình sau:

### Bước 2.1: Tạo Khóa mã hóa bảo mật `JUPYTERHUB_CRYPT_KEY`
Chạy lệnh sau trên terminal WSL để sinh mã Hex ngẫu nhiên dài 32-byte:
```bash
openssl rand -hex 32
```
Sao chép mã kết quả đầu ra (64 ký tự Hex) và điền vào biến `JUPYTERHUB_CRYPT_KEY`.

### Bước 2.2: Thêm các cấu hình Auto-Clone
```env
# Kích hoạt tính năng tự động clone bài lab
GITLAB_AUTOCLONE_ENABLED=true

# Không tự động pull/fetch đối với các repo đã tồn tại (tránh xung đột mất code của học sinh)
GITLAB_AUTOCLONE_FETCH_EXISTING=false

# Dán mã Hex tạo được từ lệnh openssl ở trên vào đây
JUPYTERHUB_CRYPT_KEY=your_generated_hex_key_here
```

---

## 3. Cơ chế Hoạt động Tự động (Không cần file CSV)

Hệ thống đã được thiết kế lại để **tự động hóa 100%** và không cần duy trì bất kỳ file cấu hình CSV nào:

1. **Quét dự án tự động**: Khi sinh viên đăng nhập vào JupyterHub, script khởi chạy của container sẽ sử dụng chính OAuth Token của sinh viên đó để truy vấn danh sách tất cả các dự án (projects) trên GitLab mà họ là thành viên (quyền Developer trở lên).
2. **Lọc bỏ kho cá nhân**: Hệ thống tự động bỏ qua các kho lưu trữ cá nhân nằm trong Namespace riêng của sinh viên.
3. **Tái tạo cấu trúc thư mục phân cấp (Tránh trùng lặp)**: Để giải quyết triệt để trường hợp trùng tên dự án giữa các bài Lab khác nhau (ví dụ: cùng là dự án tên `lab01-python-basic-student01` nhưng nằm ở hai Subgroup khác nhau là `lab01-python-basic` và `lab02-database`), hệ thống sẽ tự động tái tạo lại các thư mục con tương tự trên GitLab vào thư mục `work/` (ví dụ: `work/lab01-python-basic/lab01-python-basic/` và `work/lab02-database/lab01-python-basic/`), đồng thời loại bỏ phần hậu tố tên người dùng (`-student01`) ở tên thư mục cuối cùng để đảm bảo sự ngăn nắp và tương thích hoàn toàn khi push/pull.
4. **Hỗ trợ đa bài học/đa Group**: Sinh viên tham gia bao nhiêu bài học, thuộc nhiều Group khác nhau trên GitLab, hệ thống đều tự động nhận diện và clone về đầy đủ các thư mục tương ứng khi container khởi động.

---

## 4. Build lại và Khởi động lại dịch vụ

Thực hiện các lệnh sau tại thư mục `gitlab-jupyter-platform/jupyter/` để áp dụng cấu hình:

### Bước 4.1: Build lại Single-User Image (Chứa git-extension và script auto-clone)
```bash
docker compose --profile jupyterhub-spawnable build singleuser
```

### Bước 4.2: Build và Khởi động lại dịch vụ JupyterHub
```bash
docker compose build jupyterhub
docker compose up -d jupyterhub
```

---

## 5. Nút Đồng bộ bài tập nhanh trên màn hình Launcher

Hệ thống đã tích hợp thêm tiện ích **`jupyter-app-launcher`** để hiển thị một nút bấm trực quan trên màn hình chính **Launcher** của JupyterLab giúp sinh viên đồng bộ bài nhanh.

### Cách sử dụng:
1. Khi sinh viên đang ở JupyterLab, mở màn hình **Launcher** (bằng cách bấm dấu **`+`** ở góc trên cùng bên trái).
2. Cuộn xuống phần **Khác (Other)**, sinh viên sẽ nhìn thấy một nút bấm màu xám có icon 🔄 tên là **`Đồng bộ bài tập`**.
3. Bấm vào nút này, một cửa sổ terminal nhỏ sẽ hiện lên chạy script quét và clone bài tập mới dưới nền.
4. Khi chạy xong, màn hình terminal sẽ hiển thị: `Đồng bộ hoàn tất! Nhấn Enter để đóng...`. Sinh viên chỉ cần nhấn phím bất kỳ để đóng cửa sổ terminal. Bài tập mới sẽ xuất hiện ở cây thư mục bên trái ngay lập tức!

---

## 6. Quy trình xử lý lỗi Hết hạn Token (Dành cho Sinh viên/Giảng viên)

Vì lý do bảo mật, OAuth Token của người dùng chỉ có giá trị trong một khoảng thời gian nhất định. 
Nếu sinh viên đang làm bài trong JupyterLab mà thực hiện **Push** code lên GitLab gặp lỗi xác thực (Authentication/Permission denied):

Sinh viên cần bình tĩnh thực hiện theo các bước sau để cập nhật lại token:
1. Nhấn nút **Save** (Lưu) trên JupyterLab để lưu lại toàn bộ file bài làm.
2. Vào **File** -> **Hub Control Panel** -> Chọn **Stop My Server** (Dừng container hiện tại).
3. Nhấp nút **Logout** ở góc trên cùng bên phải để đăng xuất khỏi JupyterHub.
4. Nhấn **Login** đăng nhập lại JupyterHub thông qua tài khoản GitLab OAuth của mình (Quá trình này sẽ sinh ra OAuth token mới có hạn dùng mới).
5. Nhấn **Start My Server** để khởi tạo lại container làm việc.
6. Mở Git Extension trong JupyterLab và bấm **Push** lại bài làm. Toàn bộ code đã làm sẽ được đẩy lên GitLab thành công mà không sợ bị mất dữ liệu!

> [!WARNING]
> **Cảnh báo Bảo mật**: Token được sử dụng để clone/push bên trong container của sinh viên chỉ là OAuth Token giới hạn của riêng sinh viên đó (chỉ truy cập được vào repo của chính sinh viên). Token quản trị (`GITLAB_ADMIN_TOKEN`) tuyệt đối không được truyền vào container của người dùng để tránh nguy cơ rò rỉ quyền quản trị cao nhất.

---

## 7. Xử lý lỗi Protected Branch (Không cho sinh viên push lên main/master)

Mặc định, GitLab sẽ cấu hình các nhánh chính như `main` hoặc `master` là **Protected Branch** (Nhánh bảo vệ), chỉ cho phép quyền **Maintainer** đẩy code trực tiếp lên, trong khi sinh viên chỉ được phân quyền **Developer**. Khi sinh viên bấm **Push** trên JupyterLab sẽ gặp lỗi:
`remote: GitLab: You are not allowed to push code to protected branches on this project.`

### Các hướng khắc phục:
* **Tự động cấu hình qua Script**: Bản cập nhật mới của script khởi tạo dự án `create_lab_repos.py` đã tự động giải phóng nhánh default khỏi cấu hình bảo vệ mặc định và cấp quyền push cho nhóm **Developer** ngay khi tạo dự án hoặc gán quyền thành viên.
* **Cấu hình thủ công trên GitLab (cho các repo cũ)**: 
  1. Truy cập vào dự án trên GitLab bằng tài khoản Admin/Giảng viên.
  2. Vào **Settings** -> **Repository** -> Expand mục **Protected branches**.
  3. Tại nhánh mặc định (`main` hoặc `master`), tại cột **Allowed to push and merge** (hoặc **Allowed to push**), chọn đổi từ *Maintainers* thành **Developers + Maintainers** (Hoặc bấm thẳng nút **Unprotect** màu đỏ bên phải để hủy chế độ bảo vệ nhánh).
* **Cấu hình mặc định cho toàn Server GitLab**: Để tất cả các repo tạo mới trong tương lai tự động cho phép sinh viên push mà không cần can thiệp:
  1. Vào **Admin Area** -> **Settings** -> **Repository**.
  2. Chọn phần **Default branch** bấm **Expand**.
  3. Thiết lập **Initial default branch protection** thành **Partially protected** (Cho phép Developer push). Bấm **Save changes**.
