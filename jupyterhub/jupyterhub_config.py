import os
import re


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
c.JupyterHub.spawner_class = "jupyterhub.spawner.LocalProcessSpawner"


# =====================================================================
# Hook tiền khởi động để tự động tạo và phân quyền Linux User
# =====================================================================
def create_dir_hook(spawner):
    import pwd

    raw_username = spawner.user.name
    # Chuẩn hóa tên user để làm tên thư mục và linux username
    username = re.sub(r"[^a-zA-Z0-9_.-]", "_", raw_username)
    homedir = f"/home/{username}"

    try:
        pwd.getpwnam(username)
    except KeyError:
        os.system(f"useradd -d {homedir} -s /bin/bash {username}")

    if not os.path.exists(homedir):
        os.makedirs(homedir, exist_ok=True)

    os.system(f"chown -R {username}:{username} {homedir}")


c.Spawner.pre_spawn_hook = create_dir_hook
c.Spawner.default_url = "/lab"


# =====================================================================
# Cấu hình Xác thực LTI 1.3
# =====================================================================
from ltiauthenticator.lti13.auth import LTI13Authenticator


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
# Cấu hình file /etc/jupyter/jupyter_server_config.py cho JupyterLab (single-user server)
os.makedirs("/etc/jupyter", exist_ok=True)
with open("/etc/jupyter/jupyter_server_config.py", "w", encoding="utf-8") as f:
    f.write(
        f"""c = get_config()
c.ServerApp.allow_origin = '{MOODLE_WWWROOT}'
c.ServerApp.tornado_settings = {{
    'headers': {{
        'Content-Security-Policy': "frame-ancestors 'self' {MOODLE_WWWROOT}",
        'Access-Control-Allow-Origin': '{MOODLE_WWWROOT}',
        'X-Frame-Options': ''
    }},
    'cookie_options': {{
        'samesite': '{JUPYTERHUB_COOKIE_SAMESITE}',
        'secure': {str(JUPYTERHUB_COOKIE_SECURE)}
    }}
}}
"""
    )

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
    }
}
