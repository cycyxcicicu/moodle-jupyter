#!/usr/bin/env python3
import os
import sys
import csv
import argparse
import subprocess
import shutil
import uuid
from urllib.parse import urlparse
from dotenv import load_dotenv
import gitlab

# Hằng số cấp độ quyền của GitLab
DEVELOPER_LEVEL = 30
MAINTAINER_LEVEL = 40

def mask_token(text, token):
    """
    Ẩn token nhạy cảm trong các chuỗi log hoặc lỗi.
    """
    if token and token in text:
        return text.replace(token, "********")
    return text

def run_git_cmd(args, token=None, cwd=None):
    """
    Chạy lệnh git CLI và ẩn token nếu có lỗi xảy ra.
    """
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        stdout = mask_token(e.stdout or "", token)
        stderr = mask_token(e.stderr or "", token)
        cmd_str = mask_token(" ".join(args), token)
        raise RuntimeError(f"Lệnh Git thất bại: {cmd_str}\nstderr: {stderr}\nstdout: {stdout}")

def check_git_installed():
    """
    Kiểm tra git CLI có sẵn trên máy chạy hay không.
    """
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except Exception:
        print("LỖI: Git CLI chưa được cài đặt hoặc không tìm thấy trong PATH.")
        print("Vui lòng cài đặt git trước khi chạy script này.")
        sys.exit(1)

def get_auth_url(base_url, token, path):
    """
    Tạo URL HTTPS đã chèn token xác thực cho Git CLI.
    """
    parsed = urlparse(base_url)
    # Định dạng: http://oauth2:<token>@<domain>:<port>/<path>.git
    netloc = f"oauth2:{token}@{parsed.netloc}"
    clean_path = path.strip('/')
    return f"{parsed.scheme}://{netloc}/{clean_path}.git"

def main():
    # Load cấu hình từ file .env cùng thư mục (nếu có)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(script_dir, ".env"))

    # Đọc cấu hình mặc định từ môi trường
    env_gitlab_url = os.getenv("GITLAB_URL", "http://gitlab.local:8929")
    env_gitlab_token = os.getenv("GITLAB_ADMIN_TOKEN")
    env_default_branch = os.getenv("DEFAULT_BRANCH", "main")

    # Cấu hình đối số CLI
    parser = argparse.ArgumentParser(description="GitLab Repository Provisioning Tool for Labs")
    parser.add_argument("--csv", required=True, help="Đường dẫn đến file CSV chứa danh sách sinh viên")
    parser.add_argument("--target-group-path", required=True, help="Đường dẫn group/subgroup GitLab đích (ví dụ: lab-khoa-cntt-2026/lab01-python-basic)")
    parser.add_argument("--template-project-path", required=True, help="Đường dẫn repository đề bài (ví dụ: lab-khoa-cntt-2026/lab01-python-basic/assignment-template)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ chạy thử mô phỏng, không thay đổi trên GitLab")
    parser.add_argument("--skip-template-copy", action="store_true", help="Chỉ tạo repo và phân quyền, không copy bài mẫu")
    parser.add_argument("--force-template-copy", action="store_true", help="Cưỡng bức copy đề bài kể cả khi repo đã có dữ liệu")
    parser.add_argument("--confirm-overwrite", action="store_true", help="Xác nhận ghi đè repo đã có dữ liệu (bắt buộc đi kèm --force-template-copy)")
    parser.add_argument("--default-branch", default=env_default_branch, help="Tên branch mặc định")

    args = parser.parse_args()

    # 1. Kiểm tra cấu hình bắt buộc
    gitlab_url = env_gitlab_url
    gitlab_token = env_gitlab_token
    default_branch = args.default_branch

    if not gitlab_token:
        print("LỖI: Thiếu cấu hình GITLAB_ADMIN_TOKEN trong biến môi trường hoặc file .env.")
        print("Vui lòng tạo file .env dựa trên .env.example và điền token.")
        sys.exit(1)

    # Nếu không phải skip_template_copy, kiểm tra Git CLI trên máy
    if not args.skip_template_copy:
        check_git_installed()

    # 2. Kết nối GitLab
    print(f"Đang kết nối tới GitLab tại {gitlab_url}...")
    try:
        gl = gitlab.Gitlab(url=gitlab_url, private_token=gitlab_token)
        gl.auth()
        print(f"Xác thực thành công. Tài khoản: {gl.user.username} (Is Admin: {gl.user.is_admin})")
    except Exception as e:
        print(f"LỖI: Không thể kết nối hoặc xác thực với GitLab: {e}")
        sys.exit(1)

    # 3. Kiểm tra target group
    target_group_path = args.target_group_path
    print(f"Kiểm tra target group: {target_group_path} ...")
    try:
        group = gl.groups.get(target_group_path)
        print(f"  Tìm thấy target group ID: {group.id}")
    except gitlab.exceptions.GitlabGetError:
        print(f"LỖI: Không tìm thấy target group: '{target_group_path}'")
        sys.exit(1)

    # 4. Kiểm tra template project (nếu không skip copy)
    template_project_path = args.template_project_path
    template_project = None
    if not args.skip_template_copy:
        print(f"Kiểm tra template project: {template_project_path} ...")
        try:
            template_project = gl.projects.get(template_project_path)
            print(f"  Tìm thấy template project ID: {template_project.id}")
        except gitlab.exceptions.GitlabGetError:
            print(f"LỖI: Không tìm thấy template project: '{template_project_path}'")
            sys.exit(1)

    # 5. Đọc và validate file CSV
    csv_path = args.csv
    if not os.path.exists(csv_path):
        print(f"LỖI: Không tìm thấy file CSV tại: {csv_path}")
        sys.exit(1)

    students_list = []
    print(f"Đang đọc danh sách từ {csv_path}...")
    try:
        with open(csv_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            # Validate headers
            headers = [h.strip() for h in reader.fieldnames] if reader.fieldnames else []
            required_fields = ["username", "repo_name", "maintainers"]
            for field in required_fields:
                if field not in headers:
                    print(f"LỖI: File CSV thiếu cột bắt buộc '{field}'. Cột hiện có: {headers}")
                    sys.exit(1)
            
            for line_num, row in enumerate(reader, start=2):
                # Loại bỏ khoảng trắng thừa
                clean_row = {k.strip(): (v.strip() if v else "") for k, v in row.items()}
                # Bỏ qua dòng trống
                if not any(clean_row.values()):
                    continue
                students_list.append((line_num, clean_row))
    except Exception as e:
        print(f"LỖI: Không thể đọc file CSV: {e}")
        sys.exit(1)

    print(f"Tìm thấy {len(students_list)} dòng dữ liệu hợp lệ trong file CSV.")

    # Các thông số thống kê
    total_rows = len(students_list)
    newly_created_repos = 0
    already_existing_repos = 0
    student_permissions_granted = 0
    maintainer_permissions_granted = 0
    template_copies_completed = 0
    error_count = 0
    error_list = []

    # Thư mục tạm dùng chung cho tiến trình sao chép git
    tmp_dir_base = os.path.join(script_dir, ".tmp")

    # 6. Xử lý từng dòng sinh viên
    for line_num, row in students_list:
        student_username = row["username"]
        repo_name = row["repo_name"]
        maintainers_raw = row["maintainers"]
        
        print("-" * 60)
        print(f"Dòng {line_num}: Xử lý sinh viên '{student_username}' -> repo '{repo_name}'")

        # Tìm sinh viên
        try:
            users_found = gl.users.list(username=student_username)
            if not users_found:
                print(f"  [LỖI] Không tìm thấy user GitLab '{student_username}' trên hệ thống. Bỏ qua.")
                error_count += 1
                error_list.append(f"Dòng {line_num}: Không tìm thấy user '{student_username}'")
                continue
            student_user = users_found[0]
            print(f"  Tìm thấy sinh viên ID: {student_user.id}")
        except Exception as e:
            print(f"  [LỖI] Lỗi khi tra cứu user '{student_username}': {e}. Bỏ qua.")
            error_count += 1
            error_list.append(f"Dòng {line_num}: Lỗi tra cứu user '{student_username}'")
            continue

        # Tìm danh sách maintainers
        maintainer_users = []
        if maintainers_raw:
            m_list = [m.strip() for m in maintainers_raw.split(";") if m.strip()]
            for m_username in m_list:
                try:
                    m_users_found = gl.users.list(username=m_username)
                    if not m_users_found:
                        print(f"  [CẢNH BÁO] Không tìm thấy maintainer '{m_username}' trên hệ thống.")
                    else:
                        maintainer_users.append(m_users_found[0])
                except Exception as e:
                    print(f"  [CẢNH BÁO] Lỗi khi tra cứu maintainer '{m_username}': {e}")

        # Tìm dự án đích
        project_full_path = f"{target_group_path}/{repo_name}"
        project = None
        project_existed = False

        try:
            project = gl.projects.get(project_full_path)
            project_existed = True
            print(f"  Project đã tồn tại, dùng lại: {project_full_path} (ID: {project.id})")
            already_existing_repos += 1
        except gitlab.exceptions.GitlabGetError:
            # Dự án chưa tồn tại
            if args.dry_run:
                print(f"  [Dry-run] Sẽ tạo project mới: {project_full_path} (Private)")
                newly_created_repos += 1
            else:
                try:
                    project = gl.projects.create({
                        'name': repo_name,
                        'path': repo_name,
                        'namespace_id': group.id,
                        'visibility': 'private',
                        'initialize_with_readme': False,
                        'default_branch': default_branch
                    })
                    print(f"  Đã tạo mới project private: {project_full_path} (ID: {project.id})")
                    newly_created_repos += 1
                except Exception as create_err:
                    print(f"  [LỖI] Không thể tạo project '{repo_name}': {create_err}")
                    error_count += 1
                    error_list.append(f"Dòng {line_num}: Lỗi tạo project '{repo_name}'")
                    continue

        # Cấu hình Protected Branch để cho phép Developer push lên default_branch (Tránh lỗi push bị từ chối)
        if not args.dry_run and project:
            try:
                # Thử xóa cấu hình bảo vệ cũ của default_branch
                try:
                    project.protectedbranches.delete(default_branch)
                except Exception:
                    pass
                
                # Tạo cấu hình mới cho phép Developer push và merge
                project.protectedbranches.create({
                    'name': default_branch,
                    'push_access_level': DEVELOPER_LEVEL,
                    'merge_access_level': DEVELOPER_LEVEL
                })
                print(f"  Đã cấu hình cho phép Developer push lên nhánh '{default_branch}'")
            except Exception as e:
                print(f"  [CẢNH BÁO] Không thể cấu hình protected branch cho '{default_branch}': {e}")

        # 7. Gán quyền cho sinh viên (Developer)
        student_permission_ok = False
        if args.dry_run:
            print(f"  [Dry-run] Sẽ gán quyền Developer cho sinh viên '{student_username}'")
            student_permission_ok = True
            student_permissions_granted += 1
        else:
            try:
                # Thử tạo mới thành viên
                project.members.create({'user_id': student_user.id, 'access_level': DEVELOPER_LEVEL})
                print(f"  Đã gán quyền Developer cho sinh viên '{student_username}'")
                student_permission_ok = True
                student_permissions_granted += 1
            except gitlab.exceptions.GitlabCreateError as e:
                # Nếu đã tồn tại member trực tiếp, kiểm tra và cập nhật nếu cần
                if "already exists" in str(e).lower() or e.response_code == 409:
                    try:
                        member = project.members.get(student_user.id)
                        if member.access_level != DEVELOPER_LEVEL:
                            member.access_level = DEVELOPER_LEVEL
                            member.save()
                            print(f"  Đã cập nhật quyền sinh viên '{student_username}' lên Developer (từ {member.access_level})")
                        else:
                            print(f"  User '{student_username}' đã có sẵn quyền Developer trong project.")
                        student_permission_ok = True
                        student_permissions_granted += 1
                    except Exception as member_err:
                        print(f"  [LỖI] Không thể cập nhật quyền cho sinh viên '{student_username}': {member_err}")
                        error_list.append(f"Dòng {line_num}: Lỗi cập nhật quyền sinh viên '{student_username}'")
                else:
                    print(f"  [LỖI] Lỗi gán quyền cho sinh viên '{student_username}': {e}")
                    error_list.append(f"Dòng {line_num}: Lỗi gán quyền sinh viên '{student_username}'")

        # 8. Gán quyền cho maintainers
        for m_user in maintainer_users:
            if args.dry_run:
                print(f"  [Dry-run] Sẽ gán quyền Maintainer cho '{m_user.username}'")
                maintainer_permissions_granted += 1
            else:
                try:
                    project.members.create({'user_id': m_user.id, 'access_level': MAINTAINER_LEVEL})
                    print(f"  Đã gán quyền Maintainer cho '{m_user.username}'")
                    maintainer_permissions_granted += 1
                except gitlab.exceptions.GitlabCreateError as e:
                    if "already exists" in str(e).lower() or e.response_code == 409:
                        try:
                            member = project.members.get(m_user.id)
                            if member.access_level != MAINTAINER_LEVEL:
                                member.access_level = MAINTAINER_LEVEL
                                member.save()
                                print(f"  Đã cập nhật quyền '{m_user.username}' lên Maintainer")
                            else:
                                print(f"  User '{m_user.username}' đã có sẵn quyền Maintainer trong project.")
                            maintainer_permissions_granted += 1
                        except Exception as m_update_err:
                            print(f"  [CẢNH BÁO] Không thể cập nhật quyền Maintainer cho '{m_user.username}': {m_update_err}")
                    else:
                        print(f"  [CẢNH BÁO] Không thể gán quyền Maintainer cho '{m_user.username}': {e}")

        # 9. Sao chép đề bài từ assignment-template
        if args.skip_template_copy:
            print("  Bỏ qua copy template do sử dụng --skip-template-copy.")
            continue

        # Kiểm tra xem repo đích đã có commit chưa
        is_empty = True
        if project_existed and not args.dry_run:
            try:
                commits = project.commits.list(per_page=1)
                if len(commits) > 0:
                    is_empty = False
            except gitlab.exceptions.GitlabGetError as e:
                if e.response_code == 404:
                    is_empty = True
                else:
                    print(f"  [CẢNH BÁO] Không thể kiểm tra danh sách commit: {e}. Coi như repo trống.")
            except Exception:
                is_empty = True

        # Quyết định có copy template hay không
        should_copy = False
        if is_empty:
            should_copy = True
        else:
            # Repo không trống
            if args.force_template_copy and args.confirm_overwrite:
                should_copy = True
                print(f"  [CẢNH BÁO] Repo đã có commit, nhưng được cưỡng bức ghi đè qua (--force-template-copy và --confirm-overwrite).")
            elif args.force_template_copy and not args.confirm_overwrite:
                print(f"  [CẢNH BÁO] Repo đã có commit và có cấu hình --force-template-copy nhưng THIẾU --confirm-overwrite. BỎ QUA copy template.")
                error_list.append(f"Dòng {line_num}: Thiếu --confirm-overwrite khi force push repo '{repo_name}'")
            else:
                print(f"  Project đã có nội dung, bỏ qua copy template.")

        if should_copy:
            if args.dry_run:
                action_type = "Force push ghi đè" if not is_empty else "Copy"
                print(f"  [Dry-run] Sẽ thực hiện: {action_type} template từ '{template_project_path}' sang '{project_full_path}'")
                template_copies_completed += 1
            else:
                # Thực hiện copy bằng Git CLI sử dụng thư mục tạm
                os.makedirs(tmp_dir_base, exist_ok=True)
                temp_dir_name = f"repo-{uuid.uuid4()}"
                temp_dir_path = os.path.join(tmp_dir_base, temp_dir_name)

                # Chuẩn bị URL xác thực có chèn token
                auth_template_url = get_auth_url(gitlab_url, gitlab_token, template_project_path)
                auth_target_url = get_auth_url(gitlab_url, gitlab_token, project_full_path)

                # Chuẩn bị URL mask để ghi log
                masked_template_url = mask_token(auth_template_url, gitlab_token)
                masked_target_url = mask_token(auth_target_url, gitlab_token)

                print(f"  Đang tiến hành copy template:")
                print(f"    Từ: {masked_template_url}")
                print(f"    Đến: {masked_target_url}")

                try:
                    # 1. Clone bare template project
                    print("    [Git] Clone bare template...")
                    run_git_cmd(["git", "clone", "--bare", auth_template_url, temp_dir_path], token=gitlab_token)

                    # 2. Push mirror sang repo sinh viên
                    # Nếu force push đè thì git push --mirror sẽ tự động đè các branch
                    print("    [Git] Push mirror sang repo private...")
                    run_git_cmd(["git", "--git-dir", temp_dir_path, "push", "--mirror", auth_target_url], token=gitlab_token)

                    print("    [Git] Đồng bộ template thành công.")
                    template_copies_completed += 1
                except Exception as git_err:
                    err_msg = mask_token(str(git_err), gitlab_token)
                    print(f"  [LỖI] Quá trình copy template bằng Git gặp lỗi: {err_msg}")
                    error_count += 1
                    error_list.append(f"Dòng {line_num}: Lỗi copy template cho '{repo_name}'")
                finally:
                    # Đảm bảo dọn sạch thư mục tạm dù thành công hay thất bại
                    if os.path.exists(temp_dir_path):
                        try:
                            shutil.rmtree(temp_dir_path)
                        except Exception as cleanup_err:
                            print(f"  [CẢNH BÁO] Không thể xóa thư mục tạm {temp_dir_path}: {cleanup_err}")

    # 10. Báo cáo tổng kết
    print("=" * 60)
    print("BÁO CÁO TỔNG KẾT:")
    print(f" - Tổng số dòng CSV xử lý: {total_rows}")
    print(f" - Số repo tạo mới: {newly_created_repos}")
    print(f" - Số repo đã tồn tại: {already_existing_repos}")
    print(f" - Số sinh viên gán/cập nhật quyền thành công: {student_permissions_granted}")
    print(f" - Số maintainer gán/cập nhật quyền thành công: {maintainer_permissions_granted}")
    print(f" - Số repo copy template thành công: {template_copies_completed}")
    print(f" - Số dòng xảy ra lỗi: {error_count}")
    if error_list:
        print("DANH SÁCH LỖI CHI TIẾT:")
        for err in error_list:
            print(f"   * {err}")
    print("=" * 60)

if __name__ == "__main__":
    main()
