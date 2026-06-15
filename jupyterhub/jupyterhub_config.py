import os
import re
from jupyterhub.handlers.base import BaseHandler

# Monkeypatch để đặt Hub cookie path là "/" thay vì "/hub/"
# Điều này giúp trình duyệt gửi cookie đăng nhập (jupyterhub-hub-login)
# sang cho các dịch vụ phụ trợ như /services/assignment-service/.
original_set_user_cookie = BaseHandler._set_user_cookie
original_clear_login_cookie = BaseHandler.clear_login_cookie

def patched_set_user_cookie(self, user, server):
    if server.cookie_name == self.hub.cookie_name:
        self.log.debug("PATCHED Setting cookie for %s: %s with path '/'", user.name, server.cookie_name)
        self._set_cookie(
            server.cookie_name, user.cookie_id, encrypted=True, path="/"
        )
    else:
        original_set_user_cookie(self, user, server)

def patched_clear_login_cookie(self, name=None):
    original_clear_login_cookie(self, name)
    self.clear_cookie(self.hub.cookie_name, path="/")

BaseHandler._set_user_cookie = patched_set_user_cookie
BaseHandler.clear_login_cookie = patched_clear_login_cookie


def env(name, default):
    return os.getenv(name, default)


# =====================================================================
# Cấu hình URLs & Ports mặc định
# =====================================================================
MOODLE_WWWROOT = env("MOODLE_WWWROOT", "http://localhost:18080").rstrip("/")
JUPYTERHUB_PORT = int(env("JUPYTERHUB_PORT", "8000"))
JUPYTERHUB_ADMIN_USER = env("JUPYTERHUB_ADMIN_USER", "admin")

c = get_config()  # noqa: F821

# =====================================================================
# Cấu hình Cơ sở dữ liệu (PostgreSQL)
# =====================================================================
# Kết nối JupyterHub tới database PostgreSQL của riêng nó
c.JupyterHub.db_url = env(
    "JUPYTERHUB_DB_URL",
    "postgresql://jupyterhub_user:jupyterhub_password@postgres:5432/jupyterhub"
)

c.JupyterHub.bind_url = f"http://0.0.0.0:{JUPYTERHUB_PORT}"

# =====================================================================
# Cấu hình DockerSpawner
# =====================================================================
from dockerspawner import DockerSpawner

c.JupyterHub.spawner_class = DockerSpawner

# Tên single-user image tự build
c.DockerSpawner.image = "moodle-jupyter-singleuser:latest"

# Mạng Docker cố định cho Hub và các single-user container
c.DockerSpawner.network_name = "moodle-jupyter-net"
c.DockerSpawner.use_internal_ip = True

# Cấu hình IP và Port Hub lắng nghe để container con gọi ngược lại API
c.JupyterHub.hub_ip = "0.0.0.0"
c.JupyterHub.hub_port = 8081

# Cấu hình IP kết nối ngược (phải trùng khớp với service name của Hub trong docker-compose.yml)
c.JupyterHub.hub_connect_ip = "jupyterhub"

# Tên của container chạy notebook của học viên
c.DockerSpawner.name_template = "jupyter-{username}"

# Thư mục làm việc mặc định trong container con
c.DockerSpawner.notebook_dir = "/home/jovyan/work"

# Map volume riêng cho từng user và volume exchange dùng chung cho mọi user
PROJECT_NAME = env("PROJECT_NAME", "moodle-jupyter-platform")
exchange_vol = f"{PROJECT_NAME}_nbgrader-exchange"
courses_vol = f"{PROJECT_NAME}_nbgrader-courses"
templates_vol = f"{PROJECT_NAME}_nbgrader-templates"

c.DockerSpawner.volumes = {
    "jupyterhub-user-{username}": "/home/jovyan/work"
}

def is_teacher_user(username: str) -> bool:
    normalized = username.lower()
    return (
        "admin" in normalized
        or "teacher" in normalized
        or normalized.startswith("moodle_teacher_")
    )


# Hook tiền khởi động để mount động volume khóa học chỉ cho Giáo viên/Admin ở chế độ Read-Only
def pre_spawn_hook(spawner):
    username = spawner.user.name
    is_teacher = spawner.user.admin or is_teacher_user(username)
    
    volumes = {
        f"jupyterhub-user-{username}": "/home/jovyan/work"
    }
    
    if is_teacher:
        volumes[courses_vol] = {"bind": "/srv/nbgrader/courses", "mode": "ro"}
        volumes[templates_vol] = "/srv/nbgrader/templates"
        
    spawner.volumes = volumes


c.Spawner.pre_spawn_hook = pre_spawn_hook

# Giữ DockerSpawner.remove = False trong giai đoạn test để dễ debug container user.
c.DockerSpawner.remove = False

# Chuyển hướng mặc định khi đăng nhập thành công vào Jupyter Assignment Service GUI
c.Spawner.default_url = "../../services/assignment-service/gui"

# Khai báo jupyter-assignment-service vào JupyterHub với quyền admin để xác thực cookie & quản lý container
c.JupyterHub.services = [
    {
        'name': 'assignment-service',
        'url': 'http://jupyter-assignment-service:8001',
        'api_token': 'super-secret-token',
        'admin': True,
    }
]

# =====================================================================
# Cấu hình Xác thực LTI 1.3
# =====================================================================
from ltiauthenticator.lti13.auth import LTI13Authenticator
import jwt


class CustomLTIAuthenticator(LTI13Authenticator):
    def normalize_username(self, username):
        # Loại bỏ các ký tự đặc biệt không được Linux hỗ trợ trong tên người dùng
        safe_username = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(username))
        full_username = f"moodle_{safe_username}"
        # Giới hạn độ dài tên user Linux tối đa là 32 ký tự (giới hạn của lệnh useradd).
        # Nếu vượt quá, cắt ngắn và băm (hash) để đảm bảo tính duy nhất và độ dài hợp lệ.
        if len(full_username) > 31:
            import hashlib
            h = hashlib.sha256(full_username.encode('utf-8')).hexdigest()[:8]
            full_username = f"m_{safe_username[:16]}_{h}"
        return full_username

    async def authenticate(self, handler, data=None):
        user_dict = await super().authenticate(handler, data)
        if user_dict:
            try:
                # Trích xuất LTI 1.3 claims từ id_token JWT (đã được class cha xác thực thành công)
                id_token_jwt = handler.get_argument('id_token')
                decoded = jwt.decode(id_token_jwt, options={"verify_signature": False})
                
                context_claim = decoded.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
                course_id = context_claim.get("id", "demo")
                
                resource_link_claim = decoded.get("https://purl.imsglobal.org/spec/lti/claim/resource_link", {})
                resource_link_id = resource_link_claim.get("id", "lab01_function")
                
                username = user_dict["name"]
                
                roles = decoded.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])
                role_str = ",".join(roles) if isinstance(roles, list) else str(roles)
                
                # Gửi thông tin LTI launch sang jupyter-assignment-service
                import httpx
                async with httpx.AsyncClient() as client:
                    await client.post(
                        "http://jupyter-assignment-service:8001/services/assignment-service/api/internal/lti-launch",
                        json={
                            "username": self.normalize_username(username),
                            "moodle_course_id": course_id,
                            "moodle_resource_link_id": resource_link_id,
                            "role": role_str
                        },
                        headers={"Authorization": "Bearer super-secret-token"},
                        timeout=5.0
                    )
            except Exception as e:
                self.log.error(f"Error intercepting LTI launch: {e}")
                
        return user_dict


c.JupyterHub.authenticator_class = CustomLTIAuthenticator

# Đọc và verify đầy đủ 7 biến môi trường LTI 1.3 (do Moodle sinh ra trong lti.env)
LTI13_CLIENT_ID = env("LTI13_CLIENT_ID", "")
LTI13_ISSUER = env("LTI13_ISSUER", "")
LTI13_AUTHORIZE_URL = env("LTI13_AUTHORIZE_URL", "")
LTI13_TOKEN_URL = env("LTI13_TOKEN_URL", "")
LTI13_JWKS_URL = env("LTI13_JWKS_URL", "")
LTI13_REDIRECT_URI = env("LTI13_REDIRECT_URI", "")
LTI13_LAUNCH_URL = env("LTI13_LAUNCH_URL", "")
LTI_USERNAME_KEY = env("LTI_USERNAME_KEY", "email")

# Ưu tiên lấy MOODLE_JWKS_URL từ môi trường cho việc fetch server-side JWKS nội bộ
jwks_url = env("MOODLE_JWKS_URL", "") or LTI13_JWKS_URL

c.LTI13Authenticator.issuer = LTI13_ISSUER
c.LTI13Authenticator.client_id = [LTI13_CLIENT_ID] if LTI13_CLIENT_ID else []
c.LTI13Authenticator.authorize_url = LTI13_AUTHORIZE_URL
c.LTI13Authenticator.jwks_endpoint = jwks_url
c.LTI13Authenticator.username_key = LTI_USERNAME_KEY

c.Authenticator.allow_all = True
c.Authenticator.auto_login = True
c.Authenticator.admin_users = {JUPYTERHUB_ADMIN_USER} if JUPYTERHUB_ADMIN_USER else set()

# Đọc cấu hình cookie linh hoạt
JUPYTERHUB_COOKIE_SECURE = env("JUPYTERHUB_COOKIE_SECURE", "false").lower() in ("true", "1", "yes")
JUPYTERHUB_COOKIE_SAMESITE = env("JUPYTERHUB_COOKIE_SAMESITE", "Lax")

# =====================================================================
# Cấu hình Cookie / CORS / Nhúng IFrame (Embed/IFrame)
# =====================================================================
# Truyền cấu hình sang cho container single-user qua biến môi trường
c.DockerSpawner.environment = {
    "MOODLE_WWWROOT": MOODLE_WWWROOT,
    "JUPYTERHUB_COOKIE_SAMESITE": JUPYTERHUB_COOKIE_SAMESITE,
    "JUPYTERHUB_COOKIE_SECURE": "True" if JUPYTERHUB_COOKIE_SECURE else "False"
}


# Cấu hình cookie của JupyterHub thông qua tornado_settings
c.JupyterHub.tornado_settings = {
    "headers": {
        "Content-Security-Policy": f"frame-ancestors 'self' {MOODLE_WWWROOT}",
        "Access-Control-Allow-Origin": MOODLE_WWWROOT,
        "X-Frame-Options": "",
    },
    "cookie_options": {
        "samesite": JUPYTERHUB_COOKIE_SAMESITE,
        "secure": JUPYTERHUB_COOKIE_SECURE,
        "path": "/",
    }
}
