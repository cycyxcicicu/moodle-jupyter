import os
import re
import json
import csv

c = get_config()  # noqa: F821

def env(name, default=""):
    return os.getenv(name, default)

# =====================================================================
# Cấu hình Web Server & Database nội bộ
# =====================================================================
c.JupyterHub.bind_url = "http://0.0.0.0:8000"
c.JupyterHub.db_url = "sqlite:///jupyterhub.sqlite"  # SQLite nội bộ lưu trong Volume để persistent

# =====================================================================
# Cấu hình GitLab OAuth Authenticator
# =====================================================================
from oauthenticator.gitlab import GitLabOAuthenticator

c.JupyterHub.authenticator_class = GitLabOAuthenticator
c.GitLabOAuthenticator.gitlab_url = env("GITLAB_URL", "http://gitlab.local:8929")
c.GitLabOAuthenticator.client_id = env("GITLAB_CLIENT_ID")
c.GitLabOAuthenticator.client_secret = env("GITLAB_CLIENT_SECRET")
c.GitLabOAuthenticator.oauth_callback_url = env("GITLAB_OAUTH_CALLBACK_URL")
# Bổ sung scopes read_api, read_repository và write_repository cho Phase 2B
c.GitLabOAuthenticator.scope = ['read_user', 'openid', 'profile', 'email', 'read_api', 'read_repository', 'write_repository']
c.GitLabOAuthenticator.username_claim = "username"

# Cấu hình auth_state để lưu trữ OAuth Access Token của người dùng
c.Authenticator.enable_auth_state = True
c.Authenticator.auth_refresh_age = 300
c.Authenticator.refresh_pre_spawn = True

# Khóa mã hóa bảo mật cho auth_state
crypt_key = env("JUPYTERHUB_CRYPT_KEY")
if crypt_key:
    try:
        key_bytes = bytes.fromhex(crypt_key.strip())
    except ValueError:
        key_bytes = crypt_key.encode()
    c.CryptKeeper.keys = [key_bytes]

# Tự động tạo tài khoản khi đăng nhập thành công
c.Authenticator.allow_all = True
c.Authenticator.auto_login = False

# Cấu hình Admin & Allowed Users
admin_users = env("JUPYTERHUB_ADMIN_USERS", "root,admin")
c.Authenticator.admin_users = set(admin_users.split(",")) if admin_users else set()

allowed_users = env("JUPYTERHUB_ALLOWED_USERS", "")
if allowed_users:
    c.Authenticator.allowed_users = set(allowed_users.split(","))

# =====================================================================
# Cấu hình DockerSpawner
# =====================================================================
from dockerspawner import DockerSpawner
c.JupyterHub.spawner_class = DockerSpawner

# Cấu hình kết nối container
c.DockerSpawner.image = env("SINGLEUSER_IMAGE", "gitlab-jupyter-singleuser:latest")
c.DockerSpawner.pull_policy = "ifnotpresent"
c.DockerSpawner.network_name = env("DOCKER_NETWORK_NAME", "gitlab-jupyter-net")
c.DockerSpawner.use_internal_ip = True

# Phân giải gitlab.local bên trong singleuser container qua host-gateway
c.DockerSpawner.extra_host_config = {
    "extra_hosts": {
        "gitlab.local": "host-gateway"
    }
}

# IP & Port kết nối ngược về Hub
c.JupyterHub.hub_ip = "0.0.0.0"
c.JupyterHub.hub_port = 8081
c.JupyterHub.hub_connect_url = "http://gitlab-jupyterhub:8081"

# Đặt tên container và volume theo username
c.DockerSpawner.name_template = "gitlab-jupyter-{username}"
c.DockerSpawner.notebook_dir = "/home/jovyan/work"
c.DockerSpawner.volumes = {
    "gitlab-jupyter-user-{username}": "/home/jovyan/work"
}

# Tự động xóa container khi stop để giải phóng tài nguyên (nhưng volume vẫn giữ nguyên)
c.DockerSpawner.remove = True
c.Spawner.default_url = "/lab"

# =====================================================================
# Pre-spawn Hook để chuẩn bị môi trường và truyền token OAuth
# =====================================================================
async def pre_spawn_hook(spawner):
    # 1. Trích xuất GitLab OAuth Token từ auth_state
    auth_state = await spawner.user.get_auth_state()
    if not auth_state or 'access_token' not in auth_state:
        spawner.log.error(f"LỖI: Không tìm thấy access_token trong auth_state của user {spawner.user.name}")
        from tornado.web import HTTPError
        raise HTTPError(401, "OAuth token expired, please logout and login again.")

    gitlab_user_token = auth_state['access_token']
    spawner.log.info(f"Đã trích xuất OAuth token thành công cho user {spawner.user.name}")

    gitlab_user_email = ""
    if 'gitlab_user' in auth_state and 'email' in auth_state['gitlab_user']:
        gitlab_user_email = auth_state['gitlab_user']['email']

    # 2. Truyền các biến môi trường vào container của user
    spawner.environment['GITLAB_URL'] = env("GITLAB_URL", "http://gitlab.local:8929")
    spawner.environment['GITLAB_USERNAME'] = spawner.user.name
    spawner.environment['GITLAB_USER_EMAIL'] = gitlab_user_email
    spawner.environment['GITLAB_USER_TOKEN'] = gitlab_user_token
    spawner.environment['GITLAB_AUTOCLONE_ENABLED'] = env("GITLAB_AUTOCLONE_ENABLED", "true")
    spawner.environment['GITLAB_AUTOCLONE_FETCH_EXISTING'] = env("GITLAB_AUTOCLONE_FETCH_EXISTING", "false")

c.Spawner.pre_spawn_hook = pre_spawn_hook
