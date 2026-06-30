# SSO Stack - Phân hệ Single Sign-On cho Hệ thống E-Learning

Phân hệ Single Sign-On (SSO) này cung cấp dịch vụ xác thực tập trung và quản lý tài khoản người dùng cho toàn bộ hệ thống (Moodle, GitLab, JupyterHub). Stack được cấu hình tự động, build trực tiếp từ nền Ubuntu và tích hợp với PostgreSQL dùng chung.

## 1. Thành phần hệ thống

* **OpenLDAP (sso-openldap)**: Dịch vụ lưu trữ danh bạ người dùng tập trung (Directory Service).
* **Keycloak (sso-keycloak)**: Dịch vụ quản lý định danh và xác thực (Identity & Access Management) hỗ trợ giao thức OIDC/SAML.
* **phpLDAPadmin (sso-ldap-admin)**: Công cụ quản trị giao diện web cho OpenLDAP.
* **Cơ sở dữ liệu**: Sử dụng PostgreSQL dùng chung từ `infra-data` (`infra-postgres`).

## 2. Thông số cổng mặc định

* **OpenLDAP**: Cổng `1389` (Host) -> `389` (Container)
* **Keycloak**: Cổng `18090` (Host) -> `8080` (Container)
* **phpLDAPadmin**: Cổng `18091` (Host) -> `80` (Container)

---

## 3. Hướng dẫn sử dụng nhanh (Quick Start)

### Bước 1: Chuẩn bị môi trường
Đảm bảo bạn đang ở thư mục `sso-stack/` và Docker daemon đang chạy.

### Bước 2: Khởi tạo và Chạy hệ thống
Chạy script `init.sh` để bắt đầu cài đặt:
```bash
./init.sh
```
*Script sẽ tự động sinh tệp `.env` từ `.env.example`, kiểm tra mạng `infra-data-net`, tự động cấu hình cơ sở dữ liệu `keycloak` trên container `infra-postgres` dùng chung, thực hiện build các Docker image từ Ubuntu và khởi chạy stack.*

### Bước 3: Kiểm tra sức khỏe hệ thống
Sau khi chạy xong, bạn có thể chạy chẩn đoán lỗi bằng `doctor.sh`:
```bash
./doctor.sh
```

---

## 4. Các script quản trị tiện ích

Mỗi script đều tuân thủ quy ước chung của dự án:
* **`./init.sh`**: Khởi tạo cấu hình, build và chạy stack SSO.
* **`./doctor.sh`**: Kiểm tra kết nối DB, kiểm tra `ldapsearch` và trạng thái các container.
* **`./logs.sh`**: Xem log các container (Ví dụ xem log Keycloak: `./logs.sh keycloak`).
* **`./stop.sh`**: Dừng toàn bộ các container SSO đang chạy.
* **`./reset.sh`**: Dọn dẹp container và volume cục bộ của SSO.
  * Mặc định **không xóa database** trên `infra-postgres`.
  * Để xóa hoàn toàn database và user Keycloak, hãy truyền thêm cờ: `./reset.sh --with-db` (yêu cầu nhập xác nhận `y/N`).

---

## 5. Hướng dẫn cấu hình User Federation (LDAP) trên Keycloak

Sau khi hệ thống khởi chạy thành công:

1. Truy cập trang Keycloak Admin Console tại: `http://keycloak.school.local:18090`
2. Đăng nhập với tài khoản admin mặc định: `admin` / `adminpwd123` (từ `.env`).
3. Chọn Realm **school** từ menu góc trên bên trái.
4. Chọn **User Federation** từ menu bên trái -> Bấm **Add provider** -> Chọn **ldap**.
5. Cấu hình các tham số kết nối như sau:
   * **Console Display Name**: `School LDAP`
   * **Edit Mode**: `READ_ONLY`
   * **Vendor**: `Other`
   * **Connection URL**: `ldap://openldap:389` (sử dụng tên service trong Docker network)
   * **Users DN**: `ou=users,dc=school,dc=local`
   * **Bind DN**: `cn=admin,dc=school,dc=local`
   * **Bind Credential**: `<Mật khẩu LDAP_ADMIN_PASSWORD trong .env>` (mặc định: `adminpwd123`)
6. Bấm **Test connection** và **Test authentication** để kiểm tra tính chính xác.
7. Cấu hình phần **LDAP Searching and Syncing**:
   * **Username LDAP Attribute**: `uid`
   * **RDN LDAP Attribute**: `uid`
   * **UUID LDAP Attribute**: `entryUUID`
   * **User Object Classes**: `inetOrgPerson, posixAccount`
8. Bấm **Save** để lưu lại.
9. Bấm tiếp **Action** ở góc trên bên phải -> Chọn **Sync all users** để đồng bộ người dùng từ OpenLDAP sang Keycloak.
10. Kiểm tra tab **Users** trong realm `school` -> Bấm **Search all users** -> Bạn sẽ thấy xuất hiện tài khoản `sv001` và `teacher01` từ LDAP.

### Đăng nhập thử nghiệm:
* Bạn có thể truy cập `http://keycloak.school.local:18090/realms/school/account/` để đăng nhập thử bằng các tài khoản LDAP:
  * Sinh viên: `sv001` / `student123`
  * Giáo viên: `teacher01` / `teacher123`

---

## 6. Danh sách các Client OIDC đã được cấu hình mẫu
Các client sau đây đã có sẵn cấu hình placeholder trong realm `school`:
* **`moodle-client`**: Trỏ về `http://moodle.school.local:18080/*` (Mật khẩu bí mật: `moodle-secret-123`)
* **`gitlab-client`**: Trỏ về `http://gitlab.school.local:18088/*` (Mật khẩu bí mật: `gitlab-secret-123`)
* **`jupyterhub-client`**: Trỏ về `http://jupyterhub.school.local:18000/*` (Mật khẩu bí mật: `jupyterhub-secret-123`)
