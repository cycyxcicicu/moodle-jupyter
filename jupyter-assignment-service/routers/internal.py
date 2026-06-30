import os
import httpx
import logging
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import text

from database import engine
from services.helpers import safe_id

router = APIRouter()

JUPYTERHUB_API_TOKEN = os.getenv("JUPYTERHUB_API_TOKEN", "super-secret-token")
GITLAB_URL = os.getenv("GITLAB_URL", "http://gitlab.local:8929").rstrip('/')
GITLAB_ADMIN_TOKEN = os.getenv("GITLAB_ADMIN_TOKEN", "")

# Webhook nhận sự kiện khởi chạy LTI từ Moodle
@router.post("/lti-launch")
async def internal_lti_launch(request: Request):
    auth_header = request.headers.get("Authorization")
    if auth_header != f"Bearer {JUPYTERHUB_API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    data = await request.json()
    username = data["username"]
    moodle_course_id = str(data["moodle_course_id"])
    moodle_resource_link_id = str(data["moodle_resource_link_id"])
    role = data.get("role", "")
    course_title = data.get("moodle_course_title", "")
    assignment_title = data.get("moodle_resource_link_title", "")
    
    nbgrader_course_id = safe_id(moodle_course_id, "course")
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO active_sessions (username, moodle_course_id, moodle_resource_link_id, role) 
                VALUES (:username, :moodle_course_id, :moodle_resource_link_id, :role)
                ON CONFLICT (username)
                DO UPDATE SET moodle_course_id = EXCLUDED.moodle_course_id, 
                              moodle_resource_link_id = EXCLUDED.moodle_resource_link_id, 
                              role = EXCLUDED.role
            """),
            {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": moodle_resource_link_id, "role": role}
        )
        conn.execute(
            text("""
                INSERT INTO courses_mapping (moodle_course_id, nbgrader_course_id, course_title)
                VALUES (:moodle_course_id, :nbgrader_course_id, :course_title)
                ON CONFLICT (moodle_course_id)
                DO UPDATE SET nbgrader_course_id = EXCLUDED.nbgrader_course_id,
                              course_title = EXCLUDED.course_title
            """),
            {"moodle_course_id": moodle_course_id, "nbgrader_course_id": nbgrader_course_id, "course_title": course_title}
        )
        conn.execute(
            text("""
                INSERT INTO assignments_mapping (moodle_resource_link_id, assignment_id, moodle_course_id, assignment_title)
                VALUES (:moodle_resource_link_id, :assignment_id, :moodle_course_id, :assignment_title)
                ON CONFLICT (moodle_resource_link_id)
                DO UPDATE SET assignment_id = EXCLUDED.assignment_id, 
                              moodle_course_id = EXCLUDED.moodle_course_id,
                              assignment_title = EXCLUDED.assignment_title
            """),
            {"moodle_resource_link_id": moodle_resource_link_id, "assignment_id": safe_id(moodle_resource_link_id, "activity"), "moodle_course_id": moodle_course_id, "assignment_title": assignment_title}
        )
    
    return {"status": "success"}


# Webhook nhận sự kiện từ Moodle: Ghi vào DB queue trạng thái 'pending' và phản hồi 200 ngay lập tức
@router.post("/moodle-enrollment-webhook")
async def moodle_enrollment_webhook(request: Request):
    auth_header = request.headers.get("Authorization")
    if auth_header != f"Bearer {JUPYTERHUB_API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()
    username = data.get("username")
    email = data.get("email")
    moodle_course_id = str(data.get("moodle_course_id"))
    course_title = data.get("moodle_course_title", "")
    role = data.get("role", "student") # student hoặc teacher

    if not username or not moodle_course_id:
        raise HTTPException(status_code=400, detail="Missing username or course ID")

    # Lưu thông tin sự kiện vào PostgreSQL queue
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO enrollment_queue (username, email, moodle_course_id, course_title, role, status)
                VALUES (:username, :email, :moodle_course_id, :course_title, :role, 'pending')
            """),
            {
                "username": username,
                "email": email,
                "moodle_course_id": moodle_course_id,
                "course_title": course_title,
                "role": role
            }
        )

    return {"status": "queued", "detail": "Enrollment event queued successfully."}


# API xem trạng thái của các task trong Queue
@router.get("/enrollment-queue/status")
async def get_enrollment_queue_status():
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT status, COUNT(*) as count 
                FROM enrollment_queue 
                GROUP BY status
            """)
        ).mappings().all()
        
        detail = conn.execute(
            text("""
                SELECT id, username, moodle_course_id, status, attempts, last_error, updated_at 
                FROM enrollment_queue 
                WHERE status = 'failed' OR status = 'pending'
                ORDER BY updated_at DESC LIMIT 50
            """)
        ).mappings().all()
        
    return {"summary": result, "recent_unresolved": detail}


# Hàm xử lý thực tế gọi API GitLab (được gọi bởi Background Worker)
async def process_enrollment_event(username: str, email: str, moodle_course_id: str, course_title: str, role: str):
    headers = {"Private-Token": GITLAB_ADMIN_TOKEN}

    async with httpx.AsyncClient(verify=False) as client:
        # Bước 1: Tạo/Kiểm tra Group khóa học trên GitLab (ví dụ: course-123)
        group_path = f"course-{moodle_course_id}"
        group_name = f"Khóa học {course_title or moodle_course_id}"
        
        r_group = await client.get(f"{GITLAB_URL}/api/v4/groups?search={group_path}", headers=headers)
        r_group.raise_for_status()
        groups = [g for g in r_group.json() if g["path"] == group_path]
        
        if not groups:
            r_create_group = await client.post(
                f"{GITLAB_URL}/api/v4/groups",
                json={"name": group_name, "path": group_path, "visibility": "private"},
                headers=headers
            )
            r_create_group.raise_for_status()
            group_id = r_create_group.json()["id"]
        else:
            group_id = groups[0]["id"]

        # Bước 2: Kiểm tra/Tạo User trên GitLab và liên kết LDAP Identity
        r_user = await client.get(f"{GITLAB_URL}/api/v4/users?username={username}", headers=headers)
        r_user.raise_for_status()
        users = r_user.json()

        if not users:
            user_data = {
                "email": email or f"{username}@school.local",
                "username": username,
                "name": username,
                "password": "TemporaryPassword123!", # Mật khẩu tạm, authenticate thực tế qua LDAP
                "skip_confirmation": True,
                "provider": "ldapmain",
                "extern_uid": f"uid={username},ou=users,dc=school,dc=local"
            }
            r_create_user = await client.post(f"{GITLAB_URL}/api/v4/users", json=user_data, headers=headers)
            r_create_user.raise_for_status()
            user_id = r_create_user.json()["id"]
        else:
            user_id = users[0]["id"]

        # Bước 3: Tạo Repository bài tập cho sinh viên trong Group (nếu là học sinh)
        if role == "student":
            project_path = f"assignment-{username}"
            project_name = f"Bài tập - {username}"
            
            r_proj = await client.get(f"{GITLAB_URL}/api/v4/projects/{group_path}%2F{project_path}", headers=headers)
            if r_proj.status_code == 404:
                proj_data = {
                    "name": project_name,
                    "path": project_path,
                    "namespace_id": group_id,
                    "visibility": "private",
                    "initialize_with_readme": True
                }
                r_create_proj = await client.post(f"{GITLAB_URL}/api/v4/projects", json=proj_data, headers=headers)
                r_create_proj.raise_for_status()
                project_id = r_create_proj.json()["id"]
            else:
                project_id = r_proj.json()["id"]

            # Bước 4: Add học sinh vào repository làm Developer (Access level 30)
            member_url = f"{GITLAB_URL}/api/v4/projects/{project_id}/members"
            r_mem = await client.get(f"{member_url}?query={username}", headers=headers)
            if not any(m["username"] == username for m in r_mem.json()):
                await client.post(
                    member_url,
                    json={"user_id": user_id, "access_level": 30},
                    headers=headers
                )
        else:
            # Nếu là Giáo viên, add vào Group khóa học với tư cách Maintainer (Access level 40)
            member_url = f"{GITLAB_URL}/api/v4/groups/{group_id}/members"
            r_mem = await client.get(f"{member_url}?query={username}", headers=headers)
            if not any(m["username"] == username for m in r_mem.json()):
                await client.post(
                    member_url,
                    json={"user_id": user_id, "access_level": 40},
                    headers=headers
                )


# Hàm chạy ngầm quét hàng đợi và xử lý (Background Worker chạy mỗi 5 phút)
async def run_enrollment_queue_worker():
    # Tự động reset các task bị kẹt ở trạng thái 'processing' quá 10 phút về 'failed' để retry
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE enrollment_queue 
                SET status = 'failed', last_error = 'Timeout: task bị kẹt quá 10 phút', updated_at = NOW()
                WHERE status = 'processing' 
                AND updated_at < NOW() - INTERVAL '10 minutes'
            """)
        )

    # Lấy các task đang ở trạng thái 'pending' hoặc 'failed' nhưng số lần thử lại < 5
    with engine.connect() as conn:
        tasks = conn.execute(
            text("""
                SELECT id, username, email, moodle_course_id, course_title, role, attempts 
                FROM enrollment_queue 
                WHERE status IN ('pending', 'failed') AND attempts < 5
                ORDER BY created_at ASC
            """)
        ).mappings().all()

    for task in tasks:
        task_id = task["id"]
        # Update trạng thái sang 'processing' để tránh trùng lặp
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE enrollment_queue SET status = 'processing', updated_at = NOW() WHERE id = :id"),
                {"id": task_id}
            )

        try:
            # Gọi API GitLab để cấp phát tài nguyên
            await process_enrollment_event(
                username=task["username"],
                email=task["email"],
                moodle_course_id=task["moodle_course_id"],
                course_title=task["course_title"],
                role=task["role"]
            )
            
            # Đánh dấu hoàn thành
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE enrollment_queue SET status = 'completed', updated_at = NOW() WHERE id = :id"),
                    {"id": task_id}
                )
        except Exception as e:
            # Lưu lại lỗi và chuyển về trạng thái 'failed' để thực hiện thử lại sau
            error_msg = str(e)
            new_attempts = task["attempts"] + 1
            new_status = "failed" if new_attempts < 5 else "abandoned"
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE enrollment_queue 
                        SET status = :status, attempts = :attempts, last_error = :error, updated_at = NOW() 
                        WHERE id = :id
                    """),
                    {"status": new_status, "attempts": new_attempts, "error": error_msg, "id": task_id}
                )
