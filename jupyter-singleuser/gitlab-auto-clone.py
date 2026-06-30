#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import ssl
import urllib.request
import urllib.error
from urllib.parse import urlparse

def log(msg):
    # Mask bất kỳ token nào có thể xuất hiện trong log để đảm bảo an toàn
    token = os.environ.get("GITLAB_USER_TOKEN", "")
    if token and token in msg:
        msg = msg.replace(token, "********")
    print(f"[Auto-Clone] {msg}", flush=True)

def run_cmd(cmd, cwd=None, mask_token=True):
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip()
        if mask_token:
            token = os.environ.get("GITLAB_USER_TOKEN", "")
            if token and token in error_msg:
                error_msg = error_msg.replace(token, "********")
        log(f"LỖI khi chạy lệnh {cmd[0]}: {error_msg}")
        raise
 
def fetch_user_projects(gitlab_url, token, username):
    # Gọi GitLab API lấy danh sách projects mà user có quyền Developer (min_access_level=30)
    api_url = f"{gitlab_url.rstrip('/')}/api/v4/projects?membership=true&min_access_level=30&per_page=100"
    log(f"Đang gọi API GitLab để truy vấn dự án học viên...")
    
    req = urllib.request.Request(api_url)
    req.add_header("Authorization", f"Bearer {token}")
    
    # Bỏ qua kiểm tra chứng chỉ SSL để hoạt động tốt với các cert tự cấp (Self-signed)
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.URLError as e:
        log(f"LỖI gọi GitLab API: {e}")
        raise

def main():
    log("Bắt đầu tiến trình tự động quét và clone bài lab...")

    autoclone_enabled = os.environ.get("GITLAB_AUTOCLONE_ENABLED", "true").lower() == "true"
    if not autoclone_enabled:
        log("Tính năng Auto-Clone đang bị tắt. Bỏ qua.")
        return

    gitlab_url = os.environ.get("GITLAB_URL", "http://gitlab.local:8929")
    gitlab_username = os.environ.get("GITLAB_USERNAME")
    gitlab_user_email = os.environ.get("GITLAB_USER_EMAIL")
    gitlab_user_token = os.environ.get("GITLAB_USER_TOKEN")
    fetch_existing = os.environ.get("GITLAB_AUTOCLONE_FETCH_EXISTING", "false").lower() == "true"

    if not gitlab_user_token or not gitlab_username:
        log("CẢNH BÁO: Thiếu GITLAB_USER_TOKEN hoặc GITLAB_USERNAME. Bỏ qua.")
        return

    # 1. Gọi API để lấy danh sách projects được phân quyền
    try:
        raw_projects = fetch_user_projects(gitlab_url, gitlab_user_token, gitlab_username)
    except Exception as e:
        log(f"Không thể kết nối tới GitLab API để lấy danh sách bài làm: {e}")
        return

    if not raw_projects:
        log("Không tìm thấy bài lab nào được gán cho tài khoản này trên GitLab.")
        return

    # Lọc và chuẩn hóa thông tin bài lab
    repos = []
    seen_project_ids = set()
    for project in raw_projects:
        project_id = project.get("id")
        if not project_id:
            continue
        if project_id in seen_project_ids:
            continue
        seen_project_ids.add(project_id)

        # Bỏ qua các repo thuộc Namespace cá nhân của sinh viên
        namespace_path = project.get("namespace", {}).get("path", "")
        if namespace_path == gitlab_username:
            log(f"Bỏ qua repo cá nhân: {project.get('path_with_namespace')}")
            continue

        project_path = project.get("path", "")
        path_with_namespace = project.get("path_with_namespace", "")
        repo_url = project.get("http_url_to_repo", "")
        if not repo_url:
            continue

        # Ghi đè host/port của repo_url bằng gitlab_url nội bộ để container có thể clone trực tiếp
        if gitlab_url:
            p_repo = urlparse(repo_url)
            p_git = urlparse(gitlab_url)
            repo_url = p_repo._replace(scheme=p_git.scheme, netloc=p_git.netloc).geturl()


        # Tạo cấu trúc thư mục phân cấp tương tự GitLab để tránh trùng lặp
        parts = path_with_namespace.split("/")
        if len(parts) >= 2:
            sub_parts = list(parts[1:])
            leaf = sub_parts[-1]
            suffix = f"-{gitlab_username}"
            if leaf.endswith(suffix):
                sub_parts[-1] = leaf[:-len(suffix)]
            local_dir = "/".join(sub_parts)
        else:
            suffix = f"-{gitlab_username}"
            if project_path.endswith(suffix):
                local_dir = project_path[:-len(suffix)]
            else:
                local_dir = project_path

        repos.append({
            "project_code": project_path,
            "repo_http_url": repo_url,
            "local_dir": local_dir
        })

    if not repos:
        log("Sau khi lọc bỏ các repo cá nhân, không có bài lab nào được phân công.")
        return

    # 2. Cấu hình Git Identity
    log(f"Cấu hình Git identity: {gitlab_username}")
    run_cmd(["git", "config", "--global", "user.name", gitlab_username])
    email = gitlab_user_email if gitlab_user_email else f"{gitlab_username}@local.gitlab"
    run_cmd(["git", "config", "--global", "user.email", email])

    # 3. Cấu hình Git Credential Helper dùng Token
    credentials_path = os.path.expanduser("~/.git-credentials")
    hosts_to_auth = set()
    
    # Phân giải host của GitLab
    p_gitlab = urlparse(gitlab_url)
    if p_gitlab.netloc:
        hosts_to_auth.add(p_gitlab.netloc)
        
    for repo in repos:
        p_repo = urlparse(repo["repo_http_url"])
        if p_repo.netloc:
            hosts_to_auth.add(p_repo.netloc)

    log("Thiết lập Git Credential Store sử dụng OAuth Token...")
    try:
        with open(credentials_path, "w", encoding="utf-8") as f:
            for host in hosts_to_auth:
                f.write(f"http://oauth2:{gitlab_user_token}@{host}\n")
                f.write(f"https://oauth2:{gitlab_user_token}@{host}\n")
        
        os.chmod(credentials_path, 0o600)
        run_cmd(["git", "config", "--global", "credential.helper", "store"])
    except Exception as e:
        log(f"LỖI cấu hình credential helper: {e}")
        return

    # 4. Thực hiện Clone bài làm
    work_dir = os.path.expanduser("~/work")
    os.makedirs(work_dir, exist_ok=True)

    for repo in repos:
        project_code = repo["project_code"]
        repo_url = repo["repo_http_url"]
        local_dir = repo["local_dir"]
        target_path = os.path.join(work_dir, local_dir)

        log(f"Đang xử lý [{project_code}] -> Thư mục local: work/{local_dir}")

        if not os.path.exists(target_path):
            log(f"  Thư mục work/{local_dir} chưa tồn tại. Tiến hành clone...")
            try:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                run_cmd(["git", "clone", repo_url, target_path])
                log(f"  Clone thành công bài lab [{project_code}].")
            except Exception:
                log(f"  LỖI: Không thể clone [{project_code}]. Bỏ qua.")
        elif os.path.exists(os.path.join(target_path, ".git")):
            log(f"  Thư mục work/{local_dir} đã tồn tại Git. Giữ nguyên để tránh mất bài sinh viên.")
            if fetch_existing:
                log("  Đang fetch cập nhật...")
                try:
                    run_cmd(["git", "fetch"], cwd=target_path)
                except Exception:
                    log("  Fetch thất bại.")
        else:
            log(f"  CẢNH BÁO: Thư mục work/{local_dir} đã tồn tại nhưng không phải Git. Bỏ qua.")

    log("Hoàn thành tiến trình auto-clone.")

if __name__ == "__main__":
    main()
