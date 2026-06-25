import os
import re
import io
import tarfile
import shutil
import docker
import httpx
from fastapi import HTTPException
from sqlalchemy import text

from database import engine
from auth import JUPYTERHUB_API_TOKEN, JUPYTERHUB_API_URL
from services.helpers import safe_id

# Khởi tạo Docker Client
try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"Warning: Docker client could not start: {e}")
    docker_client = None

# Hàm phụ trợ: phân giải tên container động của người dùng
def resolve_container(username: str) -> str:
    if not docker_client:
        return None
    try:
        # Thử tìm theo nhãn (label) trước
        containers = docker_client.containers.list(filters={"label": f"hub.jupyter.org/username={username}"})
        if containers:
            return containers[0].name
        
        # Phương án dự phòng: escape ký tự đặc biệt của username (JupyterHub escape "_" thành "-5f", "." thành "-2e")
        escaped = username.replace("_", "-5f").replace(".", "-2e")
        pattern = re.compile(rf"^jupyter-{escaped}$", re.IGNORECASE)
        for c in docker_client.containers.list():
            if pattern.match(c.name):
                return c.name
    except Exception as e:
        print(f"Error resolving container: {e}")
    return None

# Hàm phụ trợ: kiểm tra trạng thái hoạt động của container
def is_container_running(container_name: str) -> bool:
    if not docker_client or not container_name:
        return False
    try:
        container = docker_client.containers.get(container_name)
        return container.status == "running"
    except Exception:
        return False

# Hàm phụ trợ: sao chép file từ container docker đang chạy về máy host
def copy_file_from_container(container_name: str, src_path: str, dest_path: str) -> bool:
    try:
        container = docker_client.containers.get(container_name)
        
        # Kiểm tra xem file có thực sự tồn tại trong container trước
        check_res = container.exec_run(cmd=["test", "-f", src_path], user="jovyan")
        if check_res.exit_code != 0:
            print(f"Error copy_file_from_container: File {src_path} does not exist inside container {container_name}")
            return False
            
        stream, stat = container.get_archive(src_path)
        
        file_like = io.BytesIO()
        for chunk in stream:
            file_like.write(chunk)
        file_like.seek(0)
        
        with tarfile.open(fileobj=file_like) as tar:
            for member in tar.getmembers():
                basename = os.path.basename(src_path)
                if member.name == basename or member.name.endswith("/" + basename):
                    f = tar.extractfile(member)
                    if f:
                        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                        with open(dest_path, "wb") as out:
                            out.write(f.read())
                        return True
        return False
    except Exception as e:
        print(f"Error copying file from container {container_name}: {e}")
        return False

# Hàm phụ trợ: thu thập bài nộp notebook của học sinh từ container (nếu đang chạy) hoặc thư mục người dùng trên host (phương án dự phòng)
def collect_student_notebook(username: str, safe_course_id: str, safe_activity_id: str, dest_file_path: str, notebook_name: str = "assignment.ipynb") -> bool:
    container_name = resolve_container(username)
    src_file_in_container = f"/home/jovyan/work/assignments/{safe_activity_id}/{notebook_name}"
    
    # 1. Thử sao chép qua Docker exec nếu container đang chạy
    if container_name and is_container_running(container_name):
        print(f"Container {container_name} is running, attempting container copy of {notebook_name}...")
        success = copy_file_from_container(container_name, src_file_in_container, dest_file_path)
        if success:
            return True
            
    # 2. Phương án dự phòng: sao chép trực tiếp từ host nếu container ngoại tuyến hoặc sao chép lỗi
    host_src_path = f"/srv/jupyter/users/{username}/assignments/{safe_activity_id}/{notebook_name}"
    print(f"Container copy failed or offline. Attempting fallback to host workspace path: {host_src_path}")
    if os.path.exists(host_src_path):
        try:
            os.makedirs(os.path.dirname(dest_file_path), exist_ok=True)
            shutil.copy2(host_src_path, dest_file_path)
            print(f"Successfully copied notebook from host path to {dest_file_path}")
            return True
        except Exception as e:
            print(f"Error copying from host path fallback: {e}")
    else:
        print(f"Host path file does not exist: {host_src_path}")
            
    return False

# Hàm phụ trợ: sao chép file từ host vào container docker đang chạy
def copy_file_to_container(container_name: str, src_path: str, dest_dir: str, dest_filename: str) -> bool:
    try:
        container = docker_client.containers.get(container_name)
        
        # Đảm bảo thư mục đích tồn tại bên trong container
        container.exec_run(cmd=["mkdir", "-p", dest_dir], user="jovyan")
        
        # Tạo file tar lưu tạm trong bộ nhớ (in-memory)
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode='w') as tar:
            tar.add(src_path, arcname=dest_filename)
        tar_stream.seek(0)
        
        # Tải lên file tar
        success = container.put_archive(dest_dir, tar_stream.getvalue())
        return success
    except Exception as e:
        print(f"Error copying file to container {container_name}: {e}")
        return False

# Hàm phụ trợ: thực thi lệnh qua docker exec (sử dụng Python SDK)
def exec_container_nbgrader(container_name: str, args: list, workdir: str) -> tuple:
    try:
        container = docker_client.containers.get(container_name)
        cmd = ["nbgrader"] + args
        print(f"Executing SDK exec in {container_name}: {' '.join(cmd)} at {workdir}")
        exec_res = container.exec_run(
            cmd=cmd,
            user="jovyan",
            workdir=workdir,
            demux=True
        )
        exit_code = exec_res.exit_code
        stdout = exec_res.output[0].decode('utf-8', errors='ignore') if exec_res.output[0] else ""
        stderr = exec_res.output[1].decode('utf-8', errors='ignore') if exec_res.output[1] else ""
        return exit_code == 0, stdout, stderr
    except Exception as e:
        return False, "", str(e)

# Hàm phụ trợ: thực thi lệnh shell chung trong container (sử dụng Python SDK)
def exec_container_cmd(container_name: str, cmd_args: list, workdir: str) -> tuple:
    try:
        container = docker_client.containers.get(container_name)
        print(f"Executing SDK exec in {container_name}: {' '.join(cmd_args)} at {workdir}")
        exec_res = container.exec_run(
            cmd=cmd_args,
            user="jovyan",
            workdir=workdir,
            demux=True
        )
        exit_code = exec_res.exit_code
        stdout = exec_res.output[0].decode('utf-8', errors='ignore') if exec_res.output[0] else ""
        stderr = exec_res.output[1].decode('utf-8', errors='ignore') if exec_res.output[1] else ""
        return exit_code == 0, stdout, stderr
    except Exception as e:
        return False, "", str(e)

# Hàm phụ trợ: Kích hoạt khởi động container JupyterLab của người dùng nếu chưa chạy
async def trigger_user_server_start(username: str) -> bool:
    headers = {"Authorization": f"token {JUPYTERHUB_API_TOKEN}"}
    async with httpx.AsyncClient() as client:
        try:
            url = f"{JUPYTERHUB_API_URL}/users/{username}/server"
            response = await client.post(url, headers=headers, timeout=10.0)
            print(f"DEBUG trigger_user_server_start for {username}: status={response.status_code}")
            return response.status_code in (201, 202, 400)
        except Exception as e:
            print(f"Error starting user server for {username}: {e}")
            return False

# Tác vụ chạy ngầm để tự động chấm điểm (Autograding)
def bg_autograde(container_name: str, assignment_id: str, course_id: str):
    safe_course_id = safe_id(course_id, "course")
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO grading_jobs (assignment_id, moodle_course_id, status, error_msg)
                VALUES (:assignment_id, :course_id, :status, NULL)
                ON CONFLICT (assignment_id, moodle_course_id)
                DO UPDATE SET status = EXCLUDED.status, error_msg = NULL, updated_at = CURRENT_TIMESTAMP
            """),
            {"assignment_id": assignment_id, "course_id": course_id, "status": "RUNNING"}
        )

    # Chạy lệnh autograde
    success, stdout, stderr = exec_container_nbgrader(
        container_name,
        ["autograde", assignment_id, f"--config=/srv/nbgrader/courses/{safe_course_id}/nbgrader_config.py"],
        f"/srv/nbgrader/courses/{safe_course_id}"
    )

    with engine.begin() as conn:
        status_val = "COMPLETED" if success else "FAILED"
        err_val = None if success else (stderr or stdout)
        conn.execute(
            text("""
                INSERT INTO grading_jobs (assignment_id, moodle_course_id, status, error_msg)
                VALUES (:assignment_id, :course_id, :status, :error_msg)
                ON CONFLICT (assignment_id, moodle_course_id)
                DO UPDATE SET status = EXCLUDED.status, error_msg = EXCLUDED.error_msg, updated_at = CURRENT_TIMESTAMP
            """),
            {"assignment_id": assignment_id, "course_id": course_id, "status": status_val, "error_msg": err_val}
        )

    if success:
        import sqlite3
        import datetime
        sqlite_db_path = f"/srv/nbgrader/courses/{safe_course_id}/gradebook.db"
        if os.path.exists(sqlite_db_path):
            try:
                sqlite_conn = sqlite3.connect(sqlite_db_path)
                sqlite_cursor = sqlite_conn.cursor()
                
                sqlite_query = """
                SELECT 
                    sa.student_id,
                    SUM(COALESCE(g.manual_score, g.auto_score, 0.0) + COALESCE(g.extra_credit, 0.0)) as total_score
                FROM submitted_assignment sa
                JOIN assignment a ON sa.assignment_id = a.id
                JOIN submitted_notebook sn ON sn.assignment_id = sa.id
                JOIN grade g ON g.notebook_id = sn.id
                WHERE a.name = ?
                GROUP BY sa.student_id
                """
                
                sqlite_cursor.execute(sqlite_query, (assignment_id,))
                rows = sqlite_cursor.fetchall()
                sqlite_conn.close()
                
                with engine.begin() as conn:
                    for row in rows:
                        safe_student_id = row[0]
                        total_score = row[1]
                        
                        student_id = safe_student_id
                        if safe_student_id.startswith("student_"):
                            student_id = safe_student_id[len("student_"):]
                            
                        attempt_no = 1
                        
                        graded_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        conn.execute(
                            text("""
                                INSERT INTO grades
                                (student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no, score, comment, graded_by, graded_at, grading_status)
                                VALUES (:student_id, :safe_student_id, :safe_course_id, :safe_activity_id, :attempt_no, :score, :comment, :graded_by, :graded_at, :grading_status)
                                ON CONFLICT (safe_student_id, safe_course_id, safe_activity_id)
                                DO UPDATE SET student_id = EXCLUDED.student_id, score = EXCLUDED.score,
                                              graded_by = EXCLUDED.graded_by, graded_at = EXCLUDED.graded_at,
                                              grading_status = EXCLUDED.grading_status
                            """),
                            {
                                "student_id": student_id,
                                "safe_student_id": safe_student_id,
                                "safe_course_id": safe_course_id,
                                "safe_activity_id": assignment_id,
                                "attempt_no": attempt_no,
                                "score": total_score,
                                "comment": "Chấm tự động bởi nbgrader",
                                "graded_by": "nbgrader",
                                "graded_at": graded_at,
                                "grading_status": "GRADED"
                            }
                        )
            except Exception as e:
                print(f"Error syncing grades from gradebook.db: {e}")
