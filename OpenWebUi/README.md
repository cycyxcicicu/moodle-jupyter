# Hướng dẫn Vận hành Open WebUI (AI Platform nội bộ)

Thư mục này chứa cấu hình Docker Compose để triển khai **Open WebUI** kết nối trực tiếp với cơ sở dữ liệu **PostgreSQL dùng chung (`infra-postgres`)** thuộc stack `infra-data` giúp tối ưu bộ nhớ RAM máy host.

---

## 1. Khởi chạy Dịch vụ

### Bước 1: Cấu hình khóa OpenAI API
1. Di chuyển vào thư mục `OpenWebUi`:
   ```bash
   cd OpenWebUi
   ```
2. Cập nhật khóa OpenAI API thật của bạn vào tệp `.env` tại dòng:
   ```env
   OPENAI_API_KEY=sk-proj-your-real-key-here
   ```

### Bước 2: Cấp quyền và chạy Script khởi tạo tự động
Chạy script `init.sh` để hệ thống tự động kiểm tra, tạo Database, tạo User và phân quyền trên Postgres dùng chung, sau đó tự khởi động Open WebUI:
```bash
chmod +x init.sh
./init.sh
```

Hệ thống sẽ mở cổng **`3000`** của máy Host để truy cập giao diện Web.

---

## 2. Tạo Tài khoản Quản trị viên (Admin) đầu tiên

1. Mở trình duyệt và truy cập: `http://localhost:3000` (hoặc tên miền của bạn).
2. Mặc dù tính năng đăng ký tự do đã bị tắt (`ENABLE_SIGNUP=False`), Open WebUI có cơ chế bảo mật thông minh: **Tài khoản đăng ký đầu tiên trên hệ thống sẽ tự động được cấp quyền Quản trị viên cao nhất (Admin)**.
3. Bạn hãy click vào **Sign Up** và tạo tài khoản Admin cho mình. Sau tài khoản đầu tiên này, nút đăng ký tự do sẽ bị khóa hoàn toàn.

---

## 3. Quản lý Người dùng & Xem lịch sử Chat (Dành cho Admin)

### Tạo tài khoản cho nhân viên:
1. Đăng nhập bằng tài khoản Admin.
2. Click vào ảnh đại diện ở góc dưới bên trái -> Chọn **Admin Panel**.
3. Vào tab **Users** -> Bấm **Add User** hoặc **Create Account** để nhập Email và mật khẩu cấp tài khoản thủ công cho nhân viên.

### Xem lịch sử Chat của hệ thống:
1. Trong **Admin Panel**, bạn chuyển qua tab **Dashboard** hoặc **Chats**.
2. Admin có thể giám sát tất cả lịch sử hội thoại của các thành viên trong nhóm để đảm bảo tuân thủ bảo mật thông tin nội bộ.

---

## 4. Tích hợp vào IDE (VS Code & JetBrains qua Continue.dev)

Mỗi nhân viên sau khi nhận tài khoản có thể tự tạo API Key cá nhân để kết nối trực tiếp từ môi trường lập trình (IDE) mà không cần nhập key OpenAI.

### Bước 1: Nhân viên tự sinh API Key
1. Đăng nhập vào Open WebUI.
2. Click vào ảnh đại diện ở góc trái -> Chọn **Settings** -> Chọn **API Keys**.
3. Bấm **Generate New Key** và sao chép mã khóa (dạng `tpts-xxxxxxxx...`).

### Bước 2: Cấu hình trên VS Code / JetBrains (Continue.dev)
Mở file cấu hình `config.json` của extension **Continue** (thường nằm ở `~/.continue/config.json`) và thiết lập như sau:

```json
{
  "models": [
    {
      "title": "Open WebUI GPT-4o",
      "provider": "openai",
      "model": "gpt-4o",
      "apiBase": "http://localhost:3000/api",
      "apiKey": "tpts-your-open-webui-api-key-here"
    }
  ]
}
```
*(Nếu bạn chạy trên máy chủ khác hoặc qua domain thực tế, hãy đổi `http://localhost:3000/api` thành `https://your-domain.com/api`)*.

Đường dẫn gọi chat completions tương thích OpenAI lúc này sẽ là:
`http://localhost:3000/api/chat/completions` (được tự động phân giải bởi Continue.dev thông qua trường `apiBase`).
