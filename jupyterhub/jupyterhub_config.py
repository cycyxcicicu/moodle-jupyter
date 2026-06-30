import os
import re
import jwt
import httpx
import time
import urllib.parse
from datetime import datetime, timedelta
from jupyterhub.handlers.base import BaseHandler
from dockerspawner import DockerSpawner
from ltiauthenticator.lti13.auth import LTI13Authenticator
from oauthenticator.generic import GenericOAuthenticator
from oauthenticator.oauth2 import OAuthLogoutHandler as _OAuthLogoutHandler
from traitlets import Unicode, List

class KeycloakSSOLogoutHandler(_OAuthLogoutHandler):
    """Logout handler gửi id_token_hint sang Keycloak end-session để xóa SSO session."""

    async def handle_logout(self):
        # Lấy id_token trước khi session bị xóa
        user = self.current_user
        self._keycloak_id_token = None
        self._is_keycloak_user = False
        if user:
            try:
                auth_state = await user.get_auth_state()
                if auth_state:
                    self._keycloak_id_token = auth_state.get('id_token')
                    # Nếu có tokens đặc trưng của Keycloak OIDC trong auth_state
                    if auth_state.get('access_token') or auth_state.get('refresh_token') or self._keycloak_id_token:
                        self._is_keycloak_user = True
            except Exception as e:
                self.log.warning(f"Không lấy được id_token từ auth_state: {e}")
        await super().handle_logout()

    async def render_logout_page(self):
        id_token = getattr(self, '_keycloak_id_token', None)
        is_keycloak = getattr(self, '_is_keycloak_user', False)
        if is_keycloak or id_token:
            # Nếu đăng nhập trực tiếp qua Keycloak: logout khỏi Keycloak và quay về trang chủ JupyterHub
            params = {
                'client_id': 'jupyterhub-client',
                'post_logout_redirect_uri': JUPYTERHUB_URL,
            }
            if id_token:
                params['id_token_hint'] = id_token
            self.redirect(
                f"{_keycloak_issuer}/protocol/openid-connect/logout"
                f"?{urllib.parse.urlencode(params)}"
            )
        else:
            # Nếu đăng nhập từ Moodle LTI (không có Keycloak id_token), quay thẳng về trang chủ Moodle
            self.redirect(MOODLE_WWWROOT)


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


# Đọc cấu hình từ Env
def env(name, default=""):
    return os.getenv(name, default)


c = get_config()  # noqa: F821

# =====================================================================
# Cấu hình URLs & Ports mặc định
# =====================================================================
MOODLE_WWWROOT = env("MOODLE_WWWROOT", "http://localhost:18080").rstrip("/")
JUPYTERHUB_URL = env("JUPYTERHUB_URL", "http://localhost:18000").rstrip("/")
JUPYTERHUB_PORT = int(env("JUPYTERHUB_PORT", "8000"))
JUPYTERHUB_API_TOKEN = env("JUPYTERHUB_API_TOKEN", "")

if not JUPYTERHUB_API_TOKEN:
    raise ValueError("Lỗi: Thiếu biến JUPYTERHUB_API_TOKEN trong môi trường!")

# =====================================================================
# Cấu hình Cơ sở dữ liệu (PostgreSQL)
# =====================================================================
c.JupyterHub.db_url = env(
    "JUPYTERHUB_DB_URL",
    "postgresql://jupyterhub_user:jupyterhub_password@postgres:5432/jupyterhub"
)
c.JupyterHub.bind_url = f"http://0.0.0.0:{JUPYTERHUB_PORT}"

# =====================================================================
# Cấu hình DockerSpawner
# =====================================================================
c.JupyterHub.spawner_class = DockerSpawner
c.DockerSpawner.image = env("JUPYTERHUB_SINGLEUSER_IMAGE", "moodle-jupyter-singleuser:latest")
c.DockerSpawner.network_name = "infra-data-net"
c.DockerSpawner.use_internal_ip = True

# Cấu hình IP và Port Hub lắng nghe để container con gọi ngược lại API
c.JupyterHub.hub_ip = "0.0.0.0"
c.JupyterHub.hub_port = 8081
c.JupyterHub.hub_connect_ip = "jupyterhub"

# Quy tắc đặt tên container theo định danh thống nhất (LDAP username)
c.DockerSpawner.name_template = "jupyter-{username}"
c.DockerSpawner.notebook_dir = "/home/jovyan/work"

# Cấu hình đường dẫn lưu trữ host (DATA_ROOT)
DATA_ROOT = env("DATA_ROOT", "")
if not DATA_ROOT:
    raise ValueError("Lỗi: Thiếu biến DATA_ROOT trong môi trường!")

# Cấu hình giới hạn tài nguyên container
mem_limit = env("JUPYTERHUB_USER_MEM_LIMIT", None)
if mem_limit:
    c.DockerSpawner.mem_limit = mem_limit

cpu_limit_val = env("JUPYTERHUB_USER_CPU_LIMIT", None)
if cpu_limit_val:
    c.DockerSpawner.cpu_limit = float(cpu_limit_val)

c.DockerSpawner.volumes = {
    f"{DATA_ROOT}/jupyter/users/{{username}}": "/home/jovyan/work"
}


def is_teacher_user(username: str) -> bool:
    normalized = username.lower()
    return (
        "admin" in normalized
        or "teacher" in normalized
        or normalized.startswith("moodle_teacher_")
    )


# Hàm tự sinh GitLab Impersonation Token: Revoke toàn bộ token cũ để tránh tràn hạn ngạch 100 tokens
async def fetch_gitlab_token(username: str):
    gitlab_url = env("GITLAB_URL_INTERNAL", "http://gitlab-ce:8929").rstrip('/')
    admin_token = env("GITLAB_ADMIN_TOKEN")
    if not admin_token:
        raise ValueError("GITLAB_ADMIN_TOKEN chưa được cấu hình!")

    headers = {"Private-Token": admin_token}
    async with httpx.AsyncClient(verify=False) as client:
        # Step 1: Lấy User ID của học viên dựa vào LDAP username
        r = await client.get(f"{gitlab_url}/api/v4/users?username={username}", headers=headers)
        r.raise_for_status()
        users = r.json()
        if not users:
            raise ValueError(f"Không tìm thấy tài khoản {username} trên GitLab")
        user_id = users[0]["id"]
        user_email = users[0].get("email", "")

        # Step 2: Liệt kê các impersonation token đang active của user này
        token_prefix = f"jupyter-{username}"
        try:
            r_tokens = await client.get(
                f"{gitlab_url}/api/v4/users/{user_id}/impersonation_tokens?state=active",
                headers=headers
            )
            r_tokens.raise_for_status()
            active_tokens = r_tokens.json()

            # Step 3: Thu hồi (Revoke) tất cả token cũ có tên khớp với prefix
            for token_obj in active_tokens:
                if token_obj.get("name", "").startswith(token_prefix):
                    token_id = token_obj["id"]
                    try:
                        r_revoke = await client.delete(
                            f"{gitlab_url}/api/v4/users/{user_id}/impersonation_tokens/{token_id}",
                            headers=headers
                        )
                        r_revoke.raise_for_status()
                    except Exception as e:
                        # Log lỗi thu hồi token nhưng không dừng tiến trình tạo token mới
                        print(f"[Warning] Thất bại khi thu hồi token {token_id} của user {username}: {e}", flush=True)
        except Exception as e:
            print(f"[Warning] Lỗi khi truy xuất danh sách token của user {username}: {e}", flush=True)

        # Step 4: Tạo mới một Impersonation Token ngắn hạn (hiệu lực 1 ngày) để bảo mật
        token_data = {
            "name": f"{token_prefix}-{int(datetime.now().timestamp())}",
            "scopes": ["api", "read_repository", "write_repository"],
            "expires_at": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        }
        r = await client.post(
            f"{gitlab_url}/api/v4/users/{user_id}/impersonation_tokens",
            json=token_data,
            headers=headers
        )
        r.raise_for_status()
        return r.json()["token"], user_email


# Pre-spawn Hook: Mount volume và tiêm token GitLab
async def pre_spawn_hook(spawner):
    if spawner.environment is None:
        spawner.environment = {}
    username = spawner.user.name
    is_teacher = spawner.user.admin or is_teacher_user(username)
    
    volumes = {
        f"{DATA_ROOT}/jupyter/users/{username}": "/home/jovyan/work"
    }
    
    if is_teacher:
        volumes[f"{DATA_ROOT}/nbgrader/courses"] = {"bind": "/srv/nbgrader/courses", "mode": "rw"}
        volumes[f"{DATA_ROOT}/nbgrader/templates"] = {"bind": "/srv/nbgrader/templates", "mode": "rw"}
        volumes[f"{DATA_ROOT}/nbgrader/exchange"] = {"bind": "/srv/nbgrader/exchange", "mode": "rw"}
        
    spawner.volumes = volumes

    # Tự động lấy token GitLab cho sinh viên/giáo viên thông qua GitLab API
    try:
        token, email = await fetch_gitlab_token(username)
        spawner.environment['GITLAB_USER_TOKEN'] = token
        spawner.environment['GITLAB_USER_EMAIL'] = email
        spawner.log.info(f"Đã cấp phát thành công token GitLab cho user {username}")
    except Exception as e:
        spawner.log.error(f"Thất bại khi lấy token GitLab cho user {username}: {e}")

    # Xác định nguồn đăng nhập (LTI từ Moodle hay OIDC trực tiếp)
    is_lti = False
    if spawner.handler:
        handler_name = spawner.handler.__class__.__name__
        spawner.log.info(f"Spawn triggered by handler: {handler_name}")
        if "LTI13" in handler_name:
            is_lti = True

    # Truyền các biến môi trường cấu hình git
    spawner.environment['GITLAB_URL'] = env("GITLAB_URL_INTERNAL", "http://gitlab-ce:8929")
    spawner.environment['GITLAB_USERNAME'] = username
    spawner.environment['GITLAB_AUTOCLONE_FETCH_EXISTING'] = env("GITLAB_AUTOCLONE_FETCH_EXISTING", "false")

    # Thiết lập cookie bảo mật cho IFrame
    spawner.environment['MOODLE_WWWROOT'] = MOODLE_WWWROOT
    spawner.environment['JUPYTERHUB_COOKIE_SAMESITE'] = env("JUPYTERHUB_COOKIE_SAMESITE", "None")
    spawner.environment['JUPYTERHUB_COOKIE_SECURE'] = env("JUPYTERHUB_COOKIE_SECURE", "true")

    # Phân hướng động và bật/tắt Auto-Clone
    if is_lti:
        # Nếu từ Moodle: chuyển hướng tới trang bài tập Moodle, TẮT tự động clone GitLab khi khởi động
        spawner.default_url = "../../services/assignment-service/gui"
        spawner.environment['GITLAB_AUTOCLONE_ENABLED'] = "false"
        spawner.log.info(f"User {username} logged in via Moodle LTI. Disabling auto-clone on startup.")
    else:
        # Nếu đăng nhập trực tiếp: chuyển hướng thẳng vào JupyterLab, BẬT tự động clone GitLab
        spawner.default_url = "/lab"
        spawner.environment['GITLAB_AUTOCLONE_ENABLED'] = env("GITLAB_AUTOCLONE_ENABLED", "true")
        spawner.log.info(f"User {username} logged in directly. Enabling auto-clone on startup.")


c.Spawner.pre_spawn_hook = pre_spawn_hook
c.DockerSpawner.remove = False
c.Spawner.default_url = "/lab"


# Đăng ký Assignment Service
c.JupyterHub.services = [
    {
        'name': 'assignment-service',
        'url': 'http://jupyter-assignment-service:8001',
        'api_token': JUPYTERHUB_API_TOKEN,
        'admin': True,
    }
]

# Custom Handlers để tránh xung đột Traitlet với GenericOAuthenticator/Authenticator.
# Ghi đè thuộc tính authenticator để hướng các handler này đọc cấu hình từ lti_auth helper.
from ltiauthenticator.lti13.handlers import LTI13LoginInitHandler, LTI13CallbackHandler, LTI13ConfigHandler

# Store LTI state server-side — fallback khi cookie Secure=true bị block trên HTTP cross-site POST.
# Key = state value (sent to Moodle), Value = {state, nonce_state, ts}
_lti_state_store: dict = {}

def _lti_state_cleanup():
    cutoff = time.time() - 300  # xóa state cũ hơn 5 phút
    for k in [k for k, v in _lti_state_store.items() if v.get('ts', 0) < cutoff]:
        del _lti_state_store[k]


class CustomLTI13LoginInitHandler(LTI13LoginInitHandler):
    @property
    def authenticator(self):
        return self.settings['authenticator'].lti_auth_configured

    def set_nonce_state_cookie(self, nonce_state):
        # Lưu tạm nonce_state vào instance để liên kết với state bên dưới
        self._pending_nonce_state = nonce_state
        super().set_nonce_state_cookie(nonce_state)

    def set_state_cookie(self, state):
        _lti_state_cleanup()
        _lti_state_store[state] = {
            'state': state,
            'nonce_state': getattr(self, '_pending_nonce_state', None),
            'ts': time.time(),
        }
        super().set_state_cookie(state)


class CustomLTI13CallbackHandler(LTI13CallbackHandler):
    @property
    def authenticator(self):
        return self.settings['authenticator'].lti_auth_configured

    def _get_state_cookie(self):
        result = super()._get_state_cookie()
        if not result:
            state_param = self.get_argument('state', None)
            if state_param and state_param in _lti_state_store:
                self._state_cookie = _lti_state_store[state_param]['state']
                result = self._state_cookie
        return result

    def _get_nonce_state_cookie(self):
        result = super()._get_nonce_state_cookie()
        if not result:
            state_param = self.get_argument('state', None)
            if state_param and state_param in _lti_state_store:
                nonce_state = _lti_state_store[state_param].get('nonce_state')
                if nonce_state:
                    self._nonce_state_cookie = nonce_state
                    result = self._nonce_state_cookie
        return result

    def get_next_url(self, user=None):
        return '/services/assignment-service/gui'

    async def login_user(self, user=None):
        user = await super().login_user(user)
        if user:
            try:
                id_token_jwt = self.get_argument('id_token')
                decoded = jwt.decode(id_token_jwt, options={"verify_signature": False})
                context_claim = decoded.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
                course_id = context_claim.get("id", "demo")
                course_title = context_claim.get("title") or ""
                resource_link_claim = decoded.get("https://purl.imsglobal.org/spec/lti/claim/resource_link", {})
                resource_link_id = resource_link_claim.get("id")
                resource_link_title = resource_link_claim.get("title") or ""
                roles = decoded.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])
                role_str = ",".join(roles) if isinstance(roles, list) else str(roles)

                parent_auth = self.settings['authenticator']
                normalized_name = parent_auth.normalize_username(user.name)

                self.log.info(f"Bắt đầu đồng bộ LTI launch data cho user {normalized_name}")
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "http://jupyter-assignment-service:8001/services/assignment-service/api/internal/lti-launch",
                        json={
                            "username": normalized_name,
                            "moodle_course_id": course_id,
                            "moodle_course_title": course_title,
                            "moodle_resource_link_id": resource_link_id,
                            "moodle_resource_link_title": resource_link_title,
                            "role": role_str
                        },
                        headers={"Authorization": f"Bearer {JUPYTERHUB_API_TOKEN}"},
                        timeout=5.0
                    )
                    response.raise_for_status()
                    self.log.info(f"Đồng bộ LTI launch data thành công: status={response.status_code}")
            except Exception as e:
                self.log.error(f"Lỗi gửi LTI launch data: {e}")
        return user

class CustomLTI13ConfigHandler(LTI13ConfigHandler):
    @property
    def authenticator(self):
        return self.settings['authenticator'].lti_auth_configured


class UnifiedAuthenticator(GenericOAuthenticator):
    # Khai báo rõ ràng các Traitlets phục vụ cho LTI 1.3
    lti_issuer = Unicode(config=True)
    lti_client_ids = List(config=True)
    lti_authorize_url = Unicode(config=True)
    lti_jwks_endpoint = Unicode(config=True)
    lti_username_key = Unicode(config=True, default_value="email")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Khởi tạo LTI13Authenticator làm helper nội bộ
        self.lti_auth = LTI13Authenticator(parent=self.parent)
        # Ghi đè hàm normalize_username của helper để đồng nhất định dạng username
        self.lti_auth.normalize_username = self.normalize_username

    @property
    def lti_auth_configured(self):
        # Đồng bộ động cấu hình từ UnifiedAuthenticator sang LTI13Authenticator helper
        self.lti_auth.issuer = self.lti_issuer
        self.lti_auth.client_id = set(self.lti_client_ids)
        self.lti_auth.authorize_url = self.lti_authorize_url
        self.lti_auth.jwks_endpoint = self.lti_jwks_endpoint
        self.lti_auth.username_key = self.lti_username_key
        return self.lti_auth

    def get_handlers(self, app):
        # Lấy danh sách các route mặc định từ GenericOAuthenticator (cho Keycloak OIDC)
        handlers = super().get_handlers(app)

        # Override logout handler để gửi id_token_hint sang Keycloak end-session
        handlers = [(p, h) for p, h in handlers if p != r'/logout']
        handlers.append((r'/logout', KeycloakSSOLogoutHandler))

        # Các handler LTI 1.3 (login từ Moodle)
        handlers.extend([
            (r'/lti13/oauth_login', CustomLTI13LoginInitHandler),
            (r'/lti13/oauth_callback', CustomLTI13CallbackHandler),
            (r'/lti13/config', CustomLTI13ConfigHandler),
        ])
        return handlers


    def normalize_username(self, username):
        if "@" in username:
            username = username.split("@")[0]
        safe_username = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(username))
        return safe_username.lower()

    async def authenticate(self, handler, data=None):
        if 'lti13' in handler.request.uri:
            lti_helper = self.lti_auth_configured
            
            user_dict = await lti_helper.authenticate(handler, data)
            if user_dict:
                user_dict["name"] = self.normalize_username(user_dict["name"])
                # Xử lý đồng bộ dữ liệu phiên LTI sang Assignment Service
                try:
                    id_token_jwt = handler.get_argument('id_token')
                    decoded = jwt.decode(id_token_jwt, options={"verify_signature": False})
                    context_claim = decoded.get("https://purl.imsglobal.org/spec/lti/claim/context", {})
                    course_id = context_claim.get("id", "demo")
                    course_title = context_claim.get("title") or ""
                    resource_link_claim = decoded.get("https://purl.imsglobal.org/spec/lti/claim/resource_link", {})
                    resource_link_id = resource_link_claim.get("id")
                    resource_link_title = resource_link_claim.get("title") or ""
                    roles = decoded.get("https://purl.imsglobal.org/spec/lti/claim/roles", [])
                    role_str = ",".join(roles) if isinstance(roles, list) else str(roles)

                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            "http://jupyter-assignment-service:8001/services/assignment-service/api/internal/lti-launch",
                            json={
                                "username": user_dict["name"],
                                "moodle_course_id": course_id,
                                "moodle_course_title": course_title,
                                "moodle_resource_link_id": resource_link_id,
                                "moodle_resource_link_title": resource_link_title,
                                "role": role_str
                            },
                            headers={"Authorization": f"Bearer {JUPYTERHUB_API_TOKEN}"},
                            timeout=5.0
                        )
                        response.raise_for_status()
                        self.log.info(f"Đồng bộ LTI launch data thành công: status={response.status_code}")
                except Exception as e:
                    self.log.error(f"Lỗi gửi LTI launch data: {e}")
            return user_dict
        else:
            user_dict = await super().authenticate(handler, data)
            if user_dict:
                user_dict["name"] = self.normalize_username(user_dict["name"])
            return user_dict

    async def refresh_user(self, user, handler=None):
        auth_state = await user.get_auth_state()
        if not auth_state:
            return True

        # Nếu là user đăng nhập qua LTI (không có refresh_token trong auth_state), không cần refresh
        if 'refresh_token' not in auth_state or not auth_state['refresh_token']:
            return True

        # Nếu là user Keycloak OIDC, gọi GenericOAuthenticator để refresh token
        try:
            return await super().refresh_user(user, handler)
        except Exception as e:
            self.log.error(f"Lỗi khi refresh token Keycloak cho user {user.name}: {e}")
            return False


c.JupyterHub.authenticator_class = UnifiedAuthenticator
c.JupyterHub.logout_handler = KeycloakSSOLogoutHandler

# Cấu hình OIDC Keycloak
c.GenericOAuthenticator.authorize_url = f"{env('KEYCLOAK_ISSUER')}/protocol/openid-connect/auth"
c.GenericOAuthenticator.token_url = "http://sso-keycloak:8080/realms/school/protocol/openid-connect/token"
c.GenericOAuthenticator.userdata_url = "http://sso-keycloak:8080/realms/school/protocol/openid-connect/userinfo"
c.GenericOAuthenticator.client_id = "jupyterhub-client"
c.GenericOAuthenticator.client_secret = env("JUPYTERHUB_OIDC_CLIENT_SECRET")
c.GenericOAuthenticator.username_claim = "preferred_username"
c.GenericOAuthenticator.scope = ['openid', 'profile', 'email']


# Cấu hình LTI 1.3
c.UnifiedAuthenticator.lti_issuer = env("LTI13_ISSUER")
c.UnifiedAuthenticator.lti_client_ids = [env("LTI13_CLIENT_ID")] if env("LTI13_CLIENT_ID") else []
c.UnifiedAuthenticator.lti_authorize_url = env("LTI13_AUTHORIZE_URL")
c.UnifiedAuthenticator.lti_jwks_endpoint = env("MOODLE_JWKS_URL") or env("LTI13_JWKS_URL")
c.UnifiedAuthenticator.lti_username_key = env("LTI_USERNAME_KEY", "email")

c.Authenticator.allow_all = True
# Phải set trên OAuthenticator (không phải Authenticator gốc) vì oauthenticator override default = True
c.OAuthenticator.auto_login = False
c.GenericOAuthenticator.login_service = "Keycloak"
c.Authenticator.admin_users = set(env("JUPYTERHUB_ADMIN_USERS", "admin,root").split(","))

# Lưu id_token vào auth_state để dùng cho id_token_hint khi logout.
# Yêu cầu JUPYTERHUB_CRYPT_KEY được set trong .env để mã hóa auth_state.
_keycloak_issuer = env("KEYCLOAK_ISSUER").rstrip("/")
_jupyterhub_url = JUPYTERHUB_URL
c.Authenticator.enable_auth_state = True
crypt_key = env("JUPYTERHUB_CRYPT_KEY")
if crypt_key:
    c.CryptKeeper.keys = [crypt_key]

# Cấu hình Cookie / CORS / Frame IFrame cho Moodle
c.JupyterHub.tornado_settings = {
    "headers": {
        "Content-Security-Policy": f"frame-ancestors 'self' {MOODLE_WWWROOT}",
        "Access-Control-Allow-Origin": MOODLE_WWWROOT,
        "X-Frame-Options": "",
    },
    "cookie_options": {
        "samesite": env("JUPYTERHUB_COOKIE_SAMESITE", "None"),
        "secure": env("JUPYTERHUB_COOKIE_SECURE", "true").lower() in ("true", "1"),
        "path": "/",
    }
}

c.JupyterHub.log_level = 'DEBUG'
