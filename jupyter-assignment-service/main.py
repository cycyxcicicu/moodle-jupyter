import os
import re
import sqlite3
import subprocess
import shutil
import asyncio
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Depends, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import docker

app = FastAPI(title="Jupyter Assignment Service")

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JUPYTERHUB_API_URL = os.getenv("JUPYTERHUB_API_URL", "http://jupyterhub:8000/hub/api")
JUPYTERHUB_API_TOKEN = os.getenv("JUPYTERHUB_API_TOKEN", "super-secret-token")
DB_PATH = "/app/assignment_service.db"

# Initialize SQLite database for mapping
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS courses_mapping (
            moodle_course_id TEXT PRIMARY KEY,
            nbgrader_course_id TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assignments_mapping (
            moodle_resource_link_id TEXT PRIMARY KEY,
            assignment_id TEXT NOT NULL,
            moodle_course_id TEXT NOT NULL,
            notebook_name TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            moodle_course_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL,
            status TEXT NOT NULL,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            score REAL,
            max_score REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS grading_jobs (
            assignment_id TEXT NOT NULL,
            moodle_course_id TEXT NOT NULL,
            status TEXT NOT NULL,
            error_msg TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (assignment_id, moodle_course_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_sessions (
            username TEXT PRIMARY KEY,
            moodle_course_id TEXT NOT NULL,
            moodle_resource_link_id TEXT NOT NULL,
            role TEXT
        )
    """)
    try:
        cursor.execute("ALTER TABLE active_sessions ADD COLUMN role TEXT")
    except Exception:
        pass
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS grades (
            student_id TEXT NOT NULL,
            safe_student_id TEXT NOT NULL,
            safe_course_id TEXT NOT NULL,
            safe_activity_id TEXT NOT NULL,
            attempt_no INTEGER,
            score REAL,
            comment TEXT,
            graded_by TEXT,
            graded_at TEXT,
            grading_status TEXT NOT NULL,
            PRIMARY KEY (safe_student_id, safe_course_id, safe_activity_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS grading_sessions (
            session_id TEXT PRIMARY KEY,
            teacher_username TEXT NOT NULL,
            student_id TEXT NOT NULL,
            safe_student_id TEXT NOT NULL,
            safe_course_id TEXT NOT NULL,
            safe_activity_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Docker Client
try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"Warning: Docker client could not start: {e}")
    docker_client = None

# Helper: resolve container name dynamic
def resolve_container(username: str) -> str:
    if not docker_client:
        return None
    try:
        # Try by label first
        containers = docker_client.containers.list(filters={"label": f"hub.jupyter.org/username={username}"})
        if containers:
            return containers[0].name
        
        # Fallback: escape username characters (JupyterHub escapes "_" as "-5f", "." as "-2e")
        escaped = username.replace("_", "-5f").replace(".", "-2e")
        pattern = re.compile(rf"^jupyter-{escaped}$", re.IGNORECASE)
        for c in docker_client.containers.list():
            if pattern.match(c.name):
                return c.name
    except Exception as e:
        print(f"Error resolving container: {e}")
    return None

# Helper: check container state
def is_container_running(container_name: str) -> bool:
    if not docker_client or not container_name:
        return False
    try:
        container = docker_client.containers.get(container_name)
        return container.status == "running"
    except Exception:
        return False

# Helper: copy file from running docker container to host
def copy_file_from_container(container_name: str, src_path: str, dest_path: str) -> bool:
    try:
        import io
        import tarfile
        container = docker_client.containers.get(container_name)
        
        # Check if file exists first in the container
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

# Helper: copy file from host to running docker container
def copy_file_to_container(container_name: str, src_path: str, dest_dir: str, dest_filename: str) -> bool:
    try:
        import io
        import tarfile
        container = docker_client.containers.get(container_name)
        
        # Ensure dest_dir exists inside the container
        container.exec_run(cmd=["mkdir", "-p", dest_dir], user="jovyan")
        
        # Create tar file in-memory
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode='w') as tar:
            tar.add(src_path, arcname=dest_filename)
        tar_stream.seek(0)
        
        # Upload tar
        success = container.put_archive(dest_dir, tar_stream.getvalue())
        return success
    except Exception as e:
        print(f"Error copying file to container {container_name}: {e}")
        return False

# Helper: run command via docker exec (using Python SDK)
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

# Helper: run generic shell check inside container (using Python SDK)
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

# Helper: Authenticate JupyterHub user from Request Cookie
async def get_current_user(request: Request):
    cookie_names = list(request.cookies.keys())
    print(f"DEBUG get_current_user: request cookie names={cookie_names}")
    cookie_name = None
    cookie_value = None
    for k, v in request.cookies.items():
        if k.startswith("jupyterhub-hub-login"):
            cookie_name = k
            cookie_value = v
            break
            
    if not cookie_value:
        for k, v in request.cookies.items():
            if k == "jupyterhub-session-id":
                cookie_name = k
                cookie_value = v
                break
            
    # Allow local testing bypass if explicitly passed via header
    test_user = request.headers.get("X-Test-User")
    if test_user:
        return {"name": test_user, "admin": "teacher" in test_user.lower() or "admin" in test_user.lower()}

    if not cookie_value:
        raise HTTPException(status_code=401, detail="Unauthorized: No JupyterHub cookie found")
        
    import urllib.parse
    encoded_cookie_value = urllib.parse.quote(cookie_value, safe='')
    headers = {"Authorization": f"token {JUPYTERHUB_API_TOKEN}"}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{JUPYTERHUB_API_URL}/authorizations/cookie/{cookie_name}/{encoded_cookie_value}",
                headers=headers
            )
            print(f"DEBUG get_current_user: JupyterHub API status={response.status_code}")
            if response.status_code == 200:
                user_data = response.json()
                username = user_data.get("name", "")
                
                # Check session to set admin flag correctly (integrated LTI roles)
                try:
                    session = get_current_lti_session(username)
                    user_data["admin"] = is_teacher_session(session, username)
                except Exception:
                    user_data["admin"] = "teacher" in username.lower() or "admin" in username.lower()
                
                return user_data
        except Exception as e:
            print(f"JupyterHub API auth error: {e}")
            
    raise HTTPException(status_code=401, detail="Unauthorized: Invalid session")

# Helper: safe_id sanitization
def safe_id(value: str, prefix: str) -> str:
    if not value or str(value).strip() == "" or str(value).lower() == "none":
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {prefix} cannot be empty or None")
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", str(value))
    sanitized = sanitized.lower()
    if len(sanitized) > 50:
        sanitized = sanitized[:50]
    return f"{prefix}_{sanitized}"

# Helper: get current LTI session
def get_current_lti_session(username: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT moodle_course_id, moodle_resource_link_id, role FROM active_sessions WHERE username = ?",
        (username,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="No active LTI session found. Please open this activity from Moodle.")
    return {
        "moodle_course_id": row[0],
        "moodle_resource_link_id": row[1],
        "role": row[2] or ""
    }

# Helper: check is teacher session
def is_teacher_session(session: dict, username: str) -> bool:
    role_str = session.get("role", "").lower()
    teacher_roles = ["instructor", "teacher", "administrator", "staff"]
    is_teacher = any(r in role_str for r in teacher_roles)
    
    if not is_teacher:
        normalized_user = username.lower()
        if "teacher" in normalized_user or "admin" in normalized_user:
            is_teacher = True
            print(f"DEBUG is_teacher_session: Dev fallback triggered for username={username}")
            
    return is_teacher

# Helper: resolve course and activity safe IDs
def resolve_course_activity_from_session(session: dict, username: str) -> dict:
    moodle_course_id = session["moodle_course_id"]
    moodle_resource_link_id = session["moodle_resource_link_id"]
    
    safe_course = safe_id(moodle_course_id, "course")
    safe_activity = safe_id(moodle_resource_link_id, "activity")
    safe_student = safe_id(username, "user") # safe_student_id
    
    return {
        "moodle_course_id": moodle_course_id,
        "moodle_resource_link_id": moodle_resource_link_id,
        "safe_course_id": safe_course,
        "safe_activity_id": safe_activity,
        "safe_student_id": safe_student
    }

# Helper: strictly verify LTI context
def verify_session_context(session: dict, username: str, request_course_id: str = None, request_assignment_id: str = None):
    ctx = resolve_course_activity_from_session(session, username)
    if request_course_id:
        c_val = str(request_course_id).strip()
        if c_val != str(ctx["moodle_course_id"]) and c_val != ctx["safe_course_id"]:
            raise HTTPException(status_code=403, detail="Forbidden: course_id mismatch LTI session context")
    if request_assignment_id:
        a_val = str(request_assignment_id).strip()
        if a_val != str(ctx["moodle_resource_link_id"]) and a_val != ctx["safe_activity_id"]:
            raise HTTPException(status_code=403, detail="Forbidden: assignment_id mismatch LTI session context")

# Background task for Autograding
def bg_autograde(container_name: str, assignment_id: str, course_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO grading_jobs (assignment_id, moodle_course_id, status) VALUES (?, ?, ?)",
        (assignment_id, course_id, "RUNNING")
    )
    conn.commit()
    conn.close()

    # Chạy lệnh autograde
    success, stdout, stderr = exec_container_nbgrader(
        container_name,
        ["autograde", assignment_id, f"--config=/srv/nbgrader/courses/moodle_course_{course_id}/nbgrader_config.py"],
        f"/srv/nbgrader/courses/moodle_course_{course_id}"
    )

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if success:
        cursor.execute(
            "INSERT OR REPLACE INTO grading_jobs (assignment_id, moodle_course_id, status) VALUES (?, ?, ?)",
            (assignment_id, course_id, "COMPLETED")
        )
    else:
        cursor.execute(
            "INSERT OR REPLACE INTO grading_jobs (assignment_id, moodle_course_id, status, error_msg) VALUES (?, ?, ?, ?)",
            (assignment_id, course_id, "FAILED", stderr or stdout)
        )
    conn.commit()
    conn.close()

# API: Helper to trigger spawning user container if not running
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

# API: Student open assignment
@app.post("/services/assignment-service/api/student/open")
async def student_open(request: Request, current_user: dict = Depends(get_current_user)):
    data = await request.json()
    username = current_user["name"]
    moodle_course_id = str(data.get("moodle_course_id"))
    assignment_id = str(data.get("assignment_id"))
    
    # Context verification
    check_session_verification(username, moodle_course_id, assignment_id)
    
    ctx = resolve_course_activity_from_session(get_current_lti_session(username), username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    # Verify released file exists
    release_file = f"/srv/nbgrader/courses/{safe_course_id}/release/{safe_activity_id}/assignment.ipynb"
    if not os.path.exists(release_file):
        raise HTTPException(status_code=400, detail="Đề bài chưa được phát hành bởi Giáo viên.")
        
    container_name = resolve_container(username)
    if not container_name or not is_container_running(container_name):
        # Trigger spawn of student server
        await trigger_user_server_start(username)
        return JSONResponse(content={
            "status": "WAITING",
            "message": "Môi trường của bạn đang được khởi tạo. Vui lòng bấm mở lại sau khoảng 5-10 giây..."
        })
        
    # Copy assignment into workspace if it does not already exist
    check_cmd = ["test", "-f", f"/home/jovyan/work/assignments/{safe_activity_id}/assignment.ipynb"]
    check_ok, _, _ = exec_container_cmd(container_name, check_cmd, "/home/jovyan")
    if not check_ok:
        dest_dir = f"/home/jovyan/work/assignments/{safe_activity_id}"
        src_path = f"/srv/nbgrader/courses/{safe_course_id}/release/{safe_activity_id}/assignment.ipynb"
        success = copy_file_to_container(container_name, src_path, dest_dir, "assignment.ipynb")
        if not success:
            raise HTTPException(status_code=500, detail="Lỗi: Không thể sao chép đề bài vào workspace của bạn.")
        
    notebook_url = f"/user/{username}/lab/tree/assignments/{safe_activity_id}/assignment.ipynb"
    return {
        "status": "READY",
        "notebook_url": notebook_url
    }

# API: Student submit assignment
@app.post("/services/assignment-service/api/student/submit")
async def student_submit(request: Request, current_user: dict = Depends(get_current_user)):
    data = await request.json()
    username = current_user["name"]
    moodle_course_id = str(data.get("moodle_course_id"))
    assignment_id = str(data.get("assignment_id"))
    
    check_session_verification(username, moodle_course_id, assignment_id)
    
    ctx = resolve_course_activity_from_session(get_current_lti_session(username), username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    container_name = resolve_container(username)
    if not container_name or not is_container_running(container_name):
        raise HTTPException(status_code=400, detail="Môi trường Jupyter chưa hoạt động. Hãy mở bài trước.")
        
    # Check if student file exists
    check_cmd = ["test", "-f", f"/home/jovyan/work/assignments/{safe_activity_id}/assignment.ipynb"]
    check_ok, _, _ = exec_container_cmd(container_name, check_cmd, "/home/jovyan")
    if not check_ok:
        raise HTTPException(status_code=400, detail="Không tìm thấy file bài làm assignment.ipynb trong workspace của bạn.")
        
    import datetime
    import json
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_student_id = safe_id(username, "student")
    
    # 1. Resolve attempt number
    attempts_dir = f"/srv/nbgrader/courses/{safe_course_id}/submitted/{safe_student_id}/{safe_activity_id}/attempts"
    attempt_no = 1
    if os.path.exists(attempts_dir):
        existing = [d for d in os.listdir(attempts_dir) if d.startswith("attempt_")]
        attempt_no = len(existing) + 1
        
    attempt_str = f"attempt_{attempt_no:03d}"
    
    # Define file paths
    dest_submitted_dir = f"/srv/nbgrader/courses/{safe_course_id}/submitted/{safe_student_id}/{safe_activity_id}"
    dest_attempt_dir = f"{dest_submitted_dir}/attempts/{attempt_str}"
    dest_exchange_dir = f"/srv/nbgrader/exchange/{safe_course_id}/inbound/{safe_student_id}/{safe_activity_id}/{attempt_str}"
    
    # Create all directories
    os.makedirs(dest_submitted_dir, exist_ok=True)
    os.makedirs(dest_attempt_dir, exist_ok=True)
    os.makedirs(dest_exchange_dir, exist_ok=True)
    
    # Copy file from student container to host
    src_file_in_container = f"/home/jovyan/work/assignments/{safe_activity_id}/assignment.ipynb"
    dest_file_main = f"{dest_submitted_dir}/assignment.ipynb"
    
    success = copy_file_from_container(container_name, src_file_in_container, dest_file_main)
    if not success:
        raise HTTPException(status_code=500, detail="Không thể tải file bài làm từ workspace của bạn.")
        
    # Copy to attempt and exchange directories
    dest_file_attempt = f"{dest_attempt_dir}/assignment.ipynb"
    dest_file_exchange = f"{dest_exchange_dir}/assignment.ipynb"
    shutil.copy2(dest_file_main, dest_file_attempt)
    shutil.copy2(dest_file_main, dest_file_exchange)
    
    # Write submission.json metadata file to both locations
    submission_metadata = {
        "student_id": username,
        "safe_student_id": safe_student_id,
        "activity_id": safe_activity_id,
        "submitted_at": timestamp,
        "attempt_no": attempt_no,
        "file_path": dest_file_attempt
    }
    
    metadata_main_path = f"{dest_submitted_dir}/submission.json"
    metadata_attempt_path = f"{dest_attempt_dir}/submission.json"
    
    with open(metadata_main_path, "w", encoding="utf-8") as f:
        json.dump(submission_metadata, f, indent=4)
    with open(metadata_attempt_path, "w", encoding="utf-8") as f:
        json.dump(submission_metadata, f, indent=4)
        
    # Set permissions
    for path in [dest_submitted_dir, dest_exchange_dir]:
        try:
            for root, dirs, files in os.walk(path):
                for d in dirs:
                    os.chown(os.path.join(root, d), 1000, 1000)
                for f_name in files:
                    os.chown(os.path.join(root, f_name), 1000, 1000)
            os.chown(path, 1000, 1000)
        except Exception as e:
            print(f"Warning: chown during student submit failed: {e}")
        
    # Save submission record
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO submissions (username, moodle_course_id, assignment_id, status, submitted_at) VALUES (?, ?, ?, ?, ?)",
        (username, moodle_course_id, assignment_id, "SUBMITTED", timestamp)
    )
    conn.commit()
    conn.close()
    
    return {"message": "Nộp bài thành công!"}

# API: Student status check
@app.get("/services/assignment-service/api/student/status")
async def student_status(request: Request, moodle_course_id: str, assignment_id: str, current_user: dict = Depends(get_current_user)):
    username = current_user["name"]
    check_session_verification(username, moodle_course_id, assignment_id)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT status, submitted_at 
        FROM submissions 
        WHERE username = ? AND moodle_course_id = ? AND assignment_id = ? 
        ORDER BY id DESC LIMIT 1
    """, (username, moodle_course_id, assignment_id))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "status": row[0],
            "submitted_at": row[1]
        }
        
    return {
        "status": "NOT_STARTED",
        "submitted_at": None
    }

# Helper: check active session verification for security check
def check_session_verification(username: str, moodle_course_id: str, assignment_id: str):
    session = get_current_lti_session(username)
    verify_session_context(session, username, moodle_course_id, assignment_id)

# API: Teacher list assignments in source
@app.get("/services/assignment-service/api/teacher/assignments")
async def teacher_assignments(current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden: Teacher access only")
    
    username = current_user["name"]
    session = get_current_lti_session(username)
    ctx = resolve_course_activity_from_session(session, username)
    safe_course_id = ctx["safe_course_id"]
    
    source_dir = f"/srv/nbgrader/courses/{safe_course_id}/source"
    assignments = []
    
    if os.path.exists(source_dir):
        for item in os.listdir(source_dir):
            item_path = os.path.join(source_dir, item)
            if os.path.isdir(item_path):
                release_file = f"/srv/nbgrader/courses/{safe_course_id}/release/{item}/assignment.ipynb"
                is_released = os.path.exists(release_file)
                assignments.append({
                    "assignment_id": item,
                    "is_released": is_released
                })
    return {"assignments": assignments}

# API: Teacher Upload Assignment File
@app.post("/services/assignment-service/api/teacher/upload")
async def teacher_upload(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden: Teacher access only")
        
    username = current_user["name"]
    session = get_current_lti_session(username)
    ctx = resolve_course_activity_from_session(session, username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    target_dir = f"/srv/nbgrader/courses/{safe_course_id}/source/{safe_activity_id}"
    os.makedirs(target_dir, exist_ok=True)
    
    target_path = os.path.join(target_dir, "assignment.ipynb")
    with open(target_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        os.chown(target_path, 1000, 1000)
        os.chown(target_dir, 1000, 1000)
    except Exception as e:
        print(f"Warning: chown failed: {e}")
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO assignments_mapping (moodle_resource_link_id, assignment_id, moodle_course_id, notebook_name) VALUES (?, ?, ?, ?)",
        (session["moodle_resource_link_id"], safe_activity_id, session["moodle_course_id"], "assignment.ipynb")
    )
    conn.commit()
    conn.close()
    
    return {"message": "Tải lên đề bài thành công!", "path": target_path}

# API: Teacher release assignment
@app.post("/services/assignment-service/api/teacher/release")
async def teacher_release(request: Request, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    data = await request.json()
    moodle_course_id = data.get("moodle_course_id")
    assignment_id = data.get("assignment_id")
    
    username = current_user["name"]
    check_session_verification(username, moodle_course_id, assignment_id)
    
    ctx = resolve_course_activity_from_session(get_current_lti_session(username), username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    source_file = f"/srv/nbgrader/courses/{safe_course_id}/source/{safe_activity_id}/assignment.ipynb"
    if not os.path.exists(source_file):
        raise HTTPException(status_code=400, detail="Không tìm thấy đề bài trong source. Hãy upload đề bài trước.")
        
    release_dir = f"/srv/nbgrader/courses/{safe_course_id}/release/{safe_activity_id}"
    os.makedirs(release_dir, exist_ok=True)
    release_file = os.path.join(release_dir, "assignment.ipynb")
    shutil.copy2(source_file, release_file)
    
    exchange_dir = f"/srv/nbgrader/exchange/{safe_course_id}/outbound/{safe_activity_id}"
    os.makedirs(exchange_dir, exist_ok=True)
    exchange_file = os.path.join(exchange_dir, "assignment.ipynb")
    shutil.copy2(source_file, exchange_file)
    
    try:
        os.chown(release_file, 1000, 1000)
        os.chown(release_dir, 1000, 1000)
        os.chown(exchange_file, 1000, 1000)
        os.chown(exchange_dir, 1000, 1000)
        os.chown(os.path.dirname(exchange_dir), 1000, 1000)
        os.chown(os.path.dirname(os.path.dirname(exchange_dir)), 1000, 1000)
    except Exception as e:
        print(f"Warning: chown during release failed: {e}")
        
    return {"message": "Phát hành đề bài thành công!"}

# API: Teacher collect submissions
@app.post("/services/assignment-service/api/teacher/collect")
async def teacher_collect(request: Request, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    data = await request.json()
    moodle_course_id = data.get("moodle_course_id")
    assignment_id = data.get("assignment_id")
    
    username = current_user["name"]
    check_session_verification(username, moodle_course_id, assignment_id)
    
    ctx = resolve_course_activity_from_session(get_current_lti_session(username), username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    submitted_base_dir = f"/srv/nbgrader/courses/{safe_course_id}/submitted"
    collected_count = 0
    
    if os.path.exists(submitted_base_dir):
        for safe_student_id in os.listdir(submitted_base_dir):
            student_sub_dir = os.path.join(submitted_base_dir, safe_student_id, safe_activity_id)
            notebook_file = os.path.join(student_sub_dir, "assignment.ipynb")
            if os.path.exists(notebook_file):
                meta_file = os.path.join(student_sub_dir, "submission.json")
                student_id = safe_student_id
                submitted_at = None
                if os.path.exists(meta_file):
                    try:
                        import json
                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                            student_id = meta.get("student_id", safe_student_id)
                            submitted_at = meta.get("submitted_at")
                    except Exception:
                        pass
                
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM submissions WHERE username = ? AND moodle_course_id = ? AND assignment_id = ?",
                    (student_id, moodle_course_id, assignment_id)
                )
                row = cursor.fetchone()
                if not row:
                    cursor.execute(
                        "INSERT INTO submissions (username, moodle_course_id, assignment_id, status, submitted_at) VALUES (?, ?, ?, ?, ?)",
                        (student_id, moodle_course_id, assignment_id, "SUBMITTED", submitted_at)
                    )
                    conn.commit()
                conn.close()
                collected_count += 1
                        
    return {"message": f"Đã đồng bộ và làm mới danh sách bài nộp ({collected_count} bài làm)!"}

# API: Teacher submissions grades
@app.get("/services/assignment-service/api/teacher/submissions")
async def teacher_submissions(moodle_course_id: str, assignment_id: str, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    username = current_user["name"]
    check_session_verification(username, moodle_course_id, assignment_id)
    
    ctx = resolve_course_activity_from_session(get_current_lti_session(username), username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    students_dict = {}
    
    # 1. Check submissions from DB
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT username, submitted_at, status 
        FROM submissions 
        WHERE moodle_course_id = ? AND assignment_id = ?
    """, (moodle_course_id, assignment_id))
    db_rows = cursor.fetchall()
    
    for r in db_rows:
        student_username = r[0]
        safe_student_id = safe_id(student_username, "student")
        students_dict[safe_student_id] = {
            "username": student_username,
            "safe_student_id": safe_student_id,
            "submitted_at": r[1] if r[1] else "-",
            "status": r[2],
            "attempt_no": None,
            "score": None,
            "comment": None,
            "grading_status": "UNGRADED"
        }
        
    # 2. Check submitted dir on disk for any others, and to parse submission.json
    submitted_dir = f"/srv/nbgrader/courses/{safe_course_id}/submitted"
    if os.path.exists(submitted_dir):
        for safe_student_id in os.listdir(submitted_dir):
            file_path = f"{submitted_dir}/{safe_student_id}/{safe_activity_id}/assignment.ipynb"
            if os.path.exists(file_path):
                meta_file = f"{submitted_dir}/{safe_student_id}/{safe_activity_id}/submission.json"
                orig_username = safe_student_id
                submitted_at = "Quét từ thư mục"
                attempt_no = 1
                if os.path.exists(meta_file):
                    try:
                        import json
                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                            orig_username = meta.get("student_id", safe_student_id)
                            submitted_at = meta.get("submitted_at", "Quét từ thư mục")
                            attempt_no = meta.get("attempt_no", 1)
                    except Exception:
                        pass
                
                if safe_student_id not in students_dict:
                    students_dict[safe_student_id] = {
                        "username": orig_username,
                        "safe_student_id": safe_student_id,
                        "submitted_at": submitted_at,
                        "status": "SUBMITTED",
                        "attempt_no": attempt_no,
                        "score": None,
                        "comment": None,
                        "grading_status": "UNGRADED"
                    }
                else:
                    students_dict[safe_student_id]["attempt_no"] = attempt_no
                    if students_dict[safe_student_id]["submitted_at"] == "-":
                        students_dict[safe_student_id]["submitted_at"] = submitted_at

    # 3. Join grades from database
    for safe_student_id, s_info in students_dict.items():
        cursor.execute("""
            SELECT score, comment, grading_status, attempt_no
            FROM grades
            WHERE safe_student_id = ? AND safe_course_id = ? AND safe_activity_id = ?
        """, (safe_student_id, safe_course_id, safe_activity_id))
        grade_row = cursor.fetchone()
        if grade_row:
            s_info["score"] = grade_row[0]
            s_info["comment"] = grade_row[1]
            s_info["grading_status"] = grade_row[2]
            if grade_row[3] is not None:
                s_info["graded_attempt_no"] = grade_row[3]
            else:
                s_info["graded_attempt_no"] = s_info["attempt_no"]
        else:
            s_info["graded_attempt_no"] = None

    conn.close()
    
    return {
        "job_status": "IDLE",
        "job_error": None,
        "students": list(students_dict.values())
    }

# API: Teacher open submission for grading
@app.post("/services/assignment-service/api/teacher/open-submission")
async def open_submission(request: Request, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    data = await request.json()
    student_id = str(data.get("student_id"))
    reset = bool(data.get("reset", False))
    teacher_username = current_user["name"]
    
    session = get_current_lti_session(teacher_username)
    if not session:
        raise HTTPException(status_code=400, detail="Không tìm thấy phiên làm việc LTI.")
        
    ctx = resolve_course_activity_from_session(session, teacher_username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    safe_student_id = safe_id(student_id, "student")
    
    submitted_dir = f"/srv/nbgrader/courses/{safe_course_id}/submitted/{safe_student_id}/{safe_activity_id}"
    src_notebook = f"{submitted_dir}/assignment.ipynb"
    if not os.path.exists(src_notebook):
        raise HTTPException(status_code=400, detail="Học sinh chưa nộp bài hoặc file không tồn tại.")
        
    attempt_no = 1
    meta_path = f"{submitted_dir}/submission.json"
    if os.path.exists(meta_path):
        try:
            import json
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                attempt_no = int(meta.get("attempt_no", 1))
        except Exception:
            pass
            
    teacher_container = resolve_container(teacher_username)
    if not teacher_container or not is_container_running(teacher_container):
        await trigger_user_server_start(teacher_username)
        return JSONResponse(content={
            "status": "WAITING",
            "message": "Đang khởi tạo container JupyterLab của bạn. Vui lòng bấm chấm bài lại sau 5-10 giây..."
        })
        
    dest_dir = f"/home/jovyan/work/grading/{safe_activity_id}/{safe_student_id}"
    
    check_cmd = ["test", "-f", f"{dest_dir}/assignment.ipynb"]
    exists_ok, _, _ = exec_container_cmd(teacher_container, check_cmd, "/home/jovyan")
    
    if not exists_ok or reset:
        if reset:
            exec_container_cmd(teacher_container, ["rm", "-f", f"{dest_dir}/assignment.ipynb"], "/home/jovyan")
            
        success = copy_file_to_container(teacher_container, src_notebook, dest_dir, "assignment.ipynb")
        if not success:
            raise HTTPException(status_code=500, detail="Không thể sao chép bài nộp vào workspace chấm bài của bạn.")
            
    import uuid
    grading_session_id = uuid.uuid4().hex
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO grading_sessions 
        (session_id, teacher_username, student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (grading_session_id, teacher_username, student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no))
    conn.commit()
    conn.close()
    
    import datetime
    import tempfile
    meta_data = {
        "session_id": grading_session_id,
        "teacher": teacher_username,
        "student": student_id,
        "safe_student_id": safe_student_id,
        "attempt_no": attempt_no,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(meta_data, tmp, indent=4)
        tmp_path = tmp.name
        
    copy_file_to_container(teacher_container, tmp_path, dest_dir, "grading_session.json")
    try:
        os.remove(tmp_path)
    except Exception:
        pass
        
    notebook_url = f"/user/{teacher_username}/lab/tree/grading/{safe_activity_id}/{safe_student_id}/assignment.ipynb"
    return {
        "status": "READY",
        "grading_session_id": grading_session_id,
        "notebook_url": notebook_url
    }

# API: Teacher save grade and comment
@app.post("/services/assignment-service/api/teacher/save-grade")
async def save_grade(request: Request, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    data = await request.json()
    grading_session_id = data.get("grading_session_id")
    try:
        score = float(data.get("score"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Điểm số phải là số thực.")
    comment = str(data.get("comment", ""))
    teacher_username = current_user["name"]
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no, teacher_username
        FROM grading_sessions
        WHERE session_id = ?
    """, (grading_session_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên chấm bài tương ứng. Vui lòng mở lại bài nộp.")
        
    student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no, session_teacher = row
    
    if session_teacher != teacher_username:
        conn.close()
        raise HTTPException(status_code=403, detail="Forbidden: Phiên chấm bài này không thuộc về bạn.")
        
    import datetime
    graded_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
        INSERT OR REPLACE INTO grades
        (student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no, score, comment, graded_by, graded_at, grading_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no, score, comment, teacher_username, graded_at, "GRADED"))
    
    conn.commit()
    conn.close()
    
    return {"message": "Lưu điểm và nhận xét thành công!"}

# API: Teacher download direct assignment file
from fastapi.responses import FileResponse
@app.get("/services/assignment-service/api/teacher/download-submission")
async def download_submission(student_id: str, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    teacher_username = current_user["name"]
    session = get_current_lti_session(teacher_username)
    if not session:
        raise HTTPException(status_code=400, detail="Không tìm thấy phiên làm việc LTI.")
        
    ctx = resolve_course_activity_from_session(session, teacher_username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    safe_student_id = safe_id(student_id, "student")
    
    file_path = f"/srv/nbgrader/courses/{safe_course_id}/submitted/{safe_student_id}/{safe_activity_id}/assignment.ipynb"
    
    real_path = os.path.realpath(file_path)
    base_courses_dir = os.path.realpath("/srv/nbgrader/courses")
    if not real_path.startswith(base_courses_dir):
        raise HTTPException(status_code=400, detail="Yêu cầu tải file không hợp lệ (Directory Traversal).")
        
    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy file bài nộp.")
        
    return FileResponse(
        real_path,
        media_type="application/octet-stream",
        filename=f"assignment_{student_id}_{safe_activity_id}.ipynb"
    )

# API: Internal LTI Launch Receiver
@app.post("/services/assignment-service/api/internal/lti-launch")
async def internal_lti_launch(request: Request):
    auth_header = request.headers.get("Authorization")
    if auth_header != f"Bearer {JUPYTERHUB_API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    data = await request.json()
    username = data["username"]
    moodle_course_id = str(data["moodle_course_id"])
    moodle_resource_link_id = str(data["moodle_resource_link_id"])
    role = data.get("role", "")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO active_sessions (username, moodle_course_id, moodle_resource_link_id, role) VALUES (?, ?, ?, ?)",
        (username, moodle_course_id, moodle_resource_link_id, role)
    )
    # Also save in mapping tables
    nbgrader_course_id = f"moodle_course_{moodle_course_id}"
    cursor.execute("INSERT OR REPLACE INTO courses_mapping VALUES (?, ?)", (moodle_course_id, nbgrader_course_id))
    cursor.execute("INSERT OR REPLACE INTO assignments_mapping (moodle_resource_link_id, assignment_id, moodle_course_id) VALUES (?, ?, ?)",
                   (moodle_resource_link_id, moodle_resource_link_id, moodle_course_id))
    conn.commit()
    conn.close()
    
    return {"status": "success"}

# RENDER CUSTOM HTML UI (Moodle embedded)
@app.get("/services/assignment-service/gui", response_class=HTMLResponse)
async def serve_gui(request: Request, moodle_course_id: str = None, assignment_id: str = None, current_user: dict = Depends(get_current_user)):
    username = current_user["name"]
    is_teacher = current_user.get("admin", False)

    try:
        session = get_current_lti_session(username)
        ctx = resolve_course_activity_from_session(session, username)
        moodle_course_id = ctx["moodle_course_id"]
        assignment_id = ctx["moodle_resource_link_id"]
        safe_course_id = ctx["safe_course_id"]
        safe_activity_id = ctx["safe_activity_id"]
    except Exception:
        # Fallback dev bypass
        moodle_course_id = moodle_course_id or "demo"
        assignment_id = assignment_id or "lab01_function"
        safe_course_id = safe_id(moodle_course_id, "course")
        safe_activity_id = safe_id(assignment_id, "activity")

    if is_teacher:
        # RENDER TEACHER UI
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Teacher Panel - Moodle Jupyter Assignment</title>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
            <style>
                body {{
                    font-family: 'Inter', sans-serif;
                    background-color: #f4f6f9;
                    margin: 0;
                    padding: 20px;
                    color: #1e293b;
                }}
                .card {{
                    background: white;
                    border-radius: 12px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.05);
                    padding: 24px;
                    margin-bottom: 20px;
                }}
                h1, h2 {{
                    margin-top: 0;
                    color: #0f172a;
                    font-weight: 700;
                }}
                .btn {{
                    background: #2563eb;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    border-radius: 6px;
                    font-size: 14px;
                    font-weight: 600;
                    cursor: pointer;
                    margin-right: 10px;
                    transition: background 0.2s, transform 0.1s;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    text-decoration: none;
                }}
                .btn:hover {{
                    background: #1d4ed8;
                }}
                .btn:active {{
                    transform: scale(0.98);
                }}
                .btn:disabled {{
                    background: #cbd5e1;
                    cursor: not-allowed;
                }}
                .btn-secondary {{
                    background: #64748b;
                }}
                .btn-secondary:hover {{
                    background: #475569;
                }}
                .btn-success {{
                    background: #10b981;
                }}
                .btn-success:hover {{
                    background: #059669;
                }}
                .btn-danger {{
                    background: #ef4444;
                }}
                .btn-danger:hover {{
                    background: #dc2626;
                }}
                .btn-sm {{
                    padding: 6px 12px;
                    font-size: 12px;
                    border-radius: 4px;
                    margin-right: 5px;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 20px;
                }}
                th, td {{
                    padding: 12px 16px;
                    text-align: left;
                    border-bottom: 1px solid #e2e8f0;
                }}
                th {{
                    background-color: #f8fafc;
                    font-weight: 600;
                    color: #475569;
                    font-size: 13px;
                    text-transform: uppercase;
                    letter-spacing: 0.05em;
                }}
                tr:hover {{
                    background-color: #f8fafc;
                }}
                .status-badge {{
                    padding: 4px 8px;
                    border-radius: 9999px;
                    font-size: 12px;
                    font-weight: 600;
                    display: inline-block;
                }}
                .status-graded {{ background: #dcfce7; color: #166534; }}
                .status-submitted {{ background: #dbeafe; color: #1e40af; }}
                .status-notstarted {{ background: #f1f5f9; color: #475569; }}
                .alert {{
                    background: #eff6ff;
                    border-left: 4px solid #3b82f6;
                    padding: 14px 18px;
                    border-radius: 6px;
                    margin-bottom: 20px;
                    font-size: 14px;
                    color: #1e3a8a;
                    line-height: 1.5;
                }}
                
                /* Modal Styling */
                .modal {{
                    display: none;
                    position: fixed;
                    top: 0; left: 0; width: 100vw; height: 100vh;
                    background: rgba(15, 23, 42, 0.65);
                    backdrop-filter: blur(4px);
                    z-index: 9999;
                    align-items: center;
                    justify-content: center;
                }}
                .modal-content {{
                    background: white;
                    width: 96vw;
                    height: 94vh;
                    border-radius: 16px;
                    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
                    display: flex;
                    flex-direction: column;
                    overflow: hidden;
                }}
                .modal-header {{
                    padding: 16px 24px;
                    border-bottom: 1px solid #e2e8f0;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    background: #f8fafc;
                }}
                .modal-title {{
                    margin: 0;
                    font-size: 18px;
                    font-weight: 700;
                    color: #0f172a;
                }}
                .close-btn {{
                    background: transparent;
                    border: none;
                    font-size: 28px;
                    cursor: pointer;
                    color: #64748b;
                    transition: color 0.2s;
                    line-height: 1;
                }}
                .close-btn:hover {{
                    color: #0f172a;
                }}
                .modal-body {{
                    flex: 1;
                    position: relative;
                    background: #f1f5f9;
                }}
                .modal-body iframe {{
                    width: 100%;
                    height: 100%;
                    border: none;
                }}
                .modal-footer {{
                    padding: 16px 24px;
                    border-top: 1px solid #e2e8f0;
                    background: #f8fafc;
                }}
                .grading-form {{
                    display: flex;
                    align-items: center;
                    gap: 16px;
                    width: 100%;
                }}
                .form-group {{
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }}
                .form-group label {{
                    font-size: 14px;
                    font-weight: 600;
                    color: #475569;
                    white-space: nowrap;
                }}
                .form-control {{
                    padding: 8px 12px;
                    border: 1px solid #cbd5e1;
                    border-radius: 6px;
                    font-size: 14px;
                    outline: none;
                    transition: border-color 0.2s;
                }}
                .form-control:focus {{
                    border-color: #2563eb;
                    box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1);
                }}
                .input-score {{
                    width: 90px;
                }}
                .input-comment {{
                    flex-grow: 1;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Bảng điều khiển Giáo viên (Course: {moodle_course_id})</h1>
                <p>Bài tập đang chọn: <strong>{assignment_id}</strong> | Thư mục an toàn: <code>{safe_activity_id}</code> | Tài khoản: <strong>{username}</strong></p>
                <div class="alert">
                    💡 <strong>Hướng dẫn chấm bài thủ công:</strong> Nhấn nút <strong>"Chấm bài"</strong> của sinh viên tương ứng để mở bài làm trong popup JupyterLab. Bạn có thể tương tác chạy thử và viết nhận xét. Nhập điểm và nhận xét của bạn vào thanh công cụ ở cuối popup và nhấn <strong>"Lưu điểm"</strong>.
                </div>
                <div>
                    <button class="btn" onclick="triggerAction('release')">1. Phát hành đề</button>
                    <button class="btn btn-secondary" onclick="triggerAction('collect')">2. Thu bài học sinh</button>
                    <button class="btn btn-secondary" style="display:none;" onclick="triggerAction('autograde')">3. Chấm bài tự động</button>
                    <button class="btn btn-secondary" style="display:none;" onclick="triggerAction('release-feedback')">4. Phát hành Phản hồi</button>
                    <button class="btn" style="background:#10b981" onclick="loadSubmissions()">Tải lại Bảng điểm</button>
                    <a href="/user/{username}/lab" target="_blank" class="btn" style="background:#4f46e5;">Mở JupyterLab Giáo viên</a>
                </div>
            </div>

            <div class="card">
                <h2>Tải lên / Tạo đề bài (.ipynb)</h2>
                <form id="upload-form" onsubmit="uploadAssignment(event)">
                    <div style="margin-bottom: 12px;">
                        <label>Chọn file Jupyter Notebook (.ipynb):</label><br>
                        <input type="file" id="assignment-file" accept=".ipynb" required style="margin-top: 8px;">
                    </div>
                    <button class="btn" id="upload-btn" type="submit" style="background:#059669">Tải lên đề bài</button>
                </form>
            </div>

            <div class="card">
                <h2>Danh sách bài tập hiện tại trong Source</h2>
                <ul id="assignment-list" style="padding-left: 20px;">
                    <li>Đang tải...</li>
                </ul>
            </div>

            <div class="card">
                <h2>Danh sách sinh viên nộp bài</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Sinh viên</th>
                            <th>Thời gian nộp</th>
                            <th>Lần nộp</th>
                            <th>Trạng thái</th>
                            <th>Điểm số</th>
                            <th>Nhận xét</th>
                            <th>Hành động</th>
                        </tr>
                    </thead>
                    <tbody id="student-table-body">
                        <tr>
                            <td colspan="7">Đang tải danh sách...</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Popup Modal Chấm Điểm -->
            <div id="grading-modal" class="modal">
                <div class="modal-content">
                    <div class="modal-header">
                        <h3 class="modal-title">Chấm bài sinh viên: <span id="grading-student-name" style="color: #2563eb;"></span></h3>
                        <button class="close-btn" onclick="closeGrading()">&times;</button>
                    </div>
                    <div class="modal-body">
                        <iframe id="grading-iframe"></iframe>
                    </div>
                    <div class="modal-footer">
                        <div class="grading-form">
                            <div class="form-group">
                                <label for="grade-input">Điểm số (0-10):</label>
                                <input type="number" id="grade-input" class="form-control input-score" min="0" max="10" step="0.1" placeholder="Điểm">
                            </div>
                            <div class="form-group" style="flex-grow: 1;">
                                <label for="comment-input">Nhận xét:</label>
                                <input type="text" id="comment-input" class="form-control input-comment" placeholder="Nhập nhận xét của giáo viên...">
                            </div>
                            <button class="btn btn-success" id="btn-save-grade" onclick="saveGrade()">Lưu điểm</button>
                            <button class="btn btn-danger" onclick="resetSubmission()">Khôi phục bài gốc</button>
                            <button class="btn btn-secondary" onclick="closeGrading()">Đóng</button>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                let currentGradingSessionId = null;
                let currentStudentId = null;

                async function triggerAction(action) {{
                    const res = await fetch(`/services/assignment-service/api/teacher/${{action}}`, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{
                            moodle_course_id: "{moodle_course_id}",
                            assignment_id: "{assignment_id}"
                        }})
                    }});
                    const data = await res.json();
                    alert(data.message || data.detail);
                    loadSubmissions();
                    loadAssignments();
                }}

                async function loadAssignments() {{
                    try {{
                        const res = await fetch(`/services/assignment-service/api/teacher/assignments`);
                        const data = await res.json();
                        const list = document.getElementById('assignment-list');
                        list.innerHTML = '';
                        if (!data.assignments || data.assignments.length === 0) {{
                            list.innerHTML = '<li>Chưa có đề bài nào trong Source.</li>';
                            return;
                        }}
                        data.assignments.forEach(asg => {{
                            const pubStatus = asg.is_released ? '<span style="color:green;font-weight:bold;">Đã phát hành</span>' : '<span style="color:orange;">Chưa phát hành</span>';
                            list.innerHTML += `<li><strong>${{asg.assignment_id}}</strong> (${{pubStatus}})</li>`;
                        }});
                    }} catch (e) {{
                        console.error(e);
                    }}
                }}

                async function uploadAssignment(event) {{
                    event.preventDefault();
                    const fileInput = document.getElementById('assignment-file');
                    if (fileInput.files.length === 0) return;
                    
                    const formData = new FormData();
                    formData.append('file', fileInput.files[0]);
                    
                    const uploadBtn = document.getElementById('upload-btn');
                    uploadBtn.disabled = true;
                    uploadBtn.innerText = 'Đang tải lên...';
                    
                    try {{
                        const res = await fetch('/services/assignment-service/api/teacher/upload', {{
                            method: 'POST',
                            body: formData
                        }});
                        const data = await res.json();
                        alert(data.message || data.detail);
                        fileInput.value = '';
                        loadAssignments();
                    }} catch (e) {{
                        alert('Upload failed: ' + e);
                    }} finally {{
                        uploadBtn.disabled = false;
                        uploadBtn.innerText = 'Tải lên đề bài';
                    }}
                }}

                async function loadSubmissions() {{
                    const res = await fetch(`/services/assignment-service/api/teacher/submissions?moodle_course_id={moodle_course_id}&assignment_id={assignment_id}`);
                    const data = await res.json();
                    
                    const tbody = document.getElementById('student-table-body');
                    tbody.innerHTML = '';
                    if(!data.students || data.students.length === 0) {{
                        tbody.innerHTML = '<tr><td colspan="7">Chưa có sinh viên nào nộp bài.</td></tr>';
                        return;
                    }}

                    data.students.forEach(st => {{
                        let badgeClass = 'status-notstarted';
                        let gradingText = 'Chưa chấm';
                        if (st.grading_status === 'GRADED') {{
                            badgeClass = 'status-graded';
                            gradingText = 'Đã chấm';
                        }} else if (st.status === 'SUBMITTED') {{
                            badgeClass = 'status-submitted';
                            gradingText = 'Đã nộp';
                        }}

                        const scoreVal = st.score !== null ? st.score : '-';
                        const commentVal = st.comment ? st.comment : '-';
                        const attemptVal = st.attempt_no ? `Lần ${{st.attempt_no}}` : '-';
                        
                        let actionsHtml = '-';
                        if (st.status === 'SUBMITTED' || st.grading_status === 'GRADED') {{
                            actionsHtml = `
                                <button class="btn btn-sm" onclick="openGrading('${{st.username}}')" data-student="${{st.username}}" data-score="${{st.score !== null ? st.score : ''}}" data-comment="${{st.comment ? st.comment : ''}}">Chấm bài</button>
                                <a href="/services/assignment-service/api/teacher/download-submission?student_id=${{st.username}}" class="btn btn-secondary btn-sm" download>Tải file</a>
                            `;
                        }}

                        tbody.innerHTML += `
                            <tr>
                                <td><strong>${{st.username}}</strong></td>
                                <td>${{st.submitted_at}}</td>
                                <td>${{attemptVal}}</td>
                                <td><span class="status-badge ${{badgeClass}}">${{gradingText}}</span></td>
                                <td><strong style="color: #2563eb;">${{scoreVal}}</strong></td>
                                <td><span style="font-size: 13px; color: #64748b;">${{commentVal}}</span></td>
                                <td>${{actionsHtml}}</td>
                            </tr>
                        `;
                    }});
                }}

                async function openGrading(studentId, reset = false) {{
                    if (reset) {{
                        if (!confirm("Bạn có chắc chắn muốn khôi phục bài nháp chấm điểm về file bài nộp gốc không? Mọi sửa đổi nháp của bạn sẽ bị mất.")) {{
                            return;
                        }}
                    }}
                    currentStudentId = studentId;
                    document.getElementById('grading-student-name').innerText = studentId;
                    
                    const rowBtn = document.querySelector(`button[data-student="${{studentId}}"]`);
                    if (rowBtn && !reset) {{
                        document.getElementById('grade-input').value = rowBtn.getAttribute('data-score') || '';
                        document.getElementById('comment-input').value = rowBtn.getAttribute('data-comment') || '';
                    }} else if (reset) {{
                        document.getElementById('grade-input').value = '';
                        document.getElementById('comment-input').value = '';
                    }}

                    const modal = document.getElementById('grading-modal');
                    const iframe = document.getElementById('grading-iframe');
                    iframe.src = '';
                    modal.style.display = 'flex';

                    try {{
                        const res = await fetch('/services/assignment-service/api/teacher/open-submission', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{ student_id: studentId, reset: reset }})
                        }});
                        const data = await res.json();
                        if (data.status === 'WAITING') {{
                            alert(data.message);
                            modal.style.display = 'none';
                        }} else if (data.status === 'READY') {{
                            currentGradingSessionId = data.grading_session_id;
                            iframe.src = data.notebook_url;
                        }} else {{
                            alert(data.detail || "Không mở được bài chấm.");
                            modal.style.display = 'none';
                        }}
                    }} catch (e) {{
                        alert("Lỗi kết nối tới service: " + e);
                        modal.style.display = 'none';
                    }}
                }}

                function closeGrading() {{
                    document.getElementById('grading-modal').style.display = 'none';
                    document.getElementById('grading-iframe').src = '';
                    currentGradingSessionId = null;
                    currentStudentId = null;
                    loadSubmissions();
                }}

                async function saveGrade() {{
                    if (!currentGradingSessionId) {{
                        alert("Phiên chấm điểm không tồn tại. Vui lòng mở lại bài chấm.");
                        return;
                    }}
                    const scoreVal = document.getElementById('grade-input').value;
                    const commentVal = document.getElementById('comment-input').value;
                    if (scoreVal === "") {{
                        alert("Vui lòng nhập điểm.");
                        return;
                    }}

                    const btn = document.getElementById('btn-save-grade');
                    btn.disabled = true;
                    btn.innerText = 'Đang lưu...';

                    try {{
                        const res = await fetch('/services/assignment-service/api/teacher/save-grade', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{
                                grading_session_id: currentGradingSessionId,
                                score: scoreVal,
                                comment: commentVal
                            }})
                        }});
                        const data = await res.json();
                        if (res.ok) {{
                            alert(data.message || "Lưu điểm thành công!");
                            closeGrading();
                        }} else {{
                            alert(data.detail || "Lỗi khi lưu điểm.");
                        }}
                    }} catch (e) {{
                        alert("Lỗi khi lưu điểm: " + e);
                    }} finally {{
                        btn.disabled = false;
                        btn.innerText = 'Lưu điểm';
                    }}
                }}

                async function resetSubmission() {{
                    if (!currentStudentId) return;
                    await openGrading(currentStudentId, true);
                }}

                // Load initial
                loadAssignments();
                loadSubmissions();
                setInterval(loadSubmissions, 12000);
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)
    else:
        # RENDER STUDENT UI WITH IFRAME
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Bài tập Jupyter - Moodle</title>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
            <style>
                body {{
                    font-family: 'Inter', sans-serif;
                    background-color: #f4f6f9;
                    margin: 0;
                    padding: 15px;
                    color: #333;
                    display: flex;
                    flex-direction: column;
                    height: 100vh;
                    box-sizing: border-box;
                }}
                .header-card {{
                    background: white;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                    padding: 15px;
                    margin-bottom: 10px;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }}
                h1 {{
                    font-size: 18px;
                    margin: 0;
                    color: #1a1a1a;
                }}
                .warning-box {{
                    background: #fffbeb;
                    border-left: 4px solid #d97706;
                    padding: 8px 12px;
                    font-size: 13px;
                    color: #b45309;
                    border-radius: 4px;
                    margin-bottom: 10px;
                }}
                .btn {{
                    background: #2563eb;
                    color: white;
                    border: none;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-size: 13px;
                    font-weight: 600;
                    cursor: pointer;
                    margin-left: 5px;
                }}
                .btn:hover {{
                    background: #1d4ed8;
                }}
                .btn-success {{
                    background: #10b981;
                }}
                .btn-success:hover {{
                    background: #059669;
                }}
                .iframe-container {{
                    flex-grow: 1;
                    background: white;
                    border: 1px solid #e2e8f0;
                    border-radius: 8px;
                    overflow: hidden;
                    position: relative;
                }}
                iframe {{
                    width: 100%;
                    height: 100%;
                    border: none;
                }}
                .overlay {{
                    position: absolute;
                    top: 0; left: 0; right: 0; bottom: 0;
                    background: rgba(255,255,255,0.9);
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    align-items: center;
                    z-index: 10;
                }}
                .loader {{
                    border: 4px solid #f3f3f3;
                    border-top: 4px solid #3498db;
                    border-radius: 50%;
                    width: 30px;
                    height: 30px;
                    animation: spin 1s linear infinite;
                    margin-bottom: 10px;
                }}
                @keyframes spin {{
                    0% {{ transform: rotate(0deg); }}
                    100% {{ transform: rotate(360deg); }}
                }}
            </style>
        </head>
        <body>
            <div class="header-card">
                <div>
                    <h1>Bài tập: {assignment_id} (Lớp: {moodle_course_id})</h1>
                    <span id="score-display" style="font-size: 13px; color: #555;">Trạng thái: Đang tải...</span>
                </div>
                <div>
                    <button class="btn" id="btn-open" onclick="openAssignment()">Bắt đầu làm bài</button>
                    <button class="btn btn-success" id="btn-submit" onclick="submitAssignment()" style="display:none;">Nộp bài</button>
                </div>
            </div>

            <div class="warning-box">
                ⚠️ <strong>LƯU Ý QUAN TRỌNG:</strong> Vui lòng nhấn nút <strong>Save (hoặc Ctrl+S)</strong> trong giao diện JupyterLab trước khi bấm nút <strong>Nộp bài</strong>.
            </div>

            <div class="iframe-container">
                <div class="overlay" id="loading-overlay">
                    <div class="loader" id="spinner" style="display:none;"></div>
                    <p id="overlay-text">Hãy click nút "Bắt đầu làm bài" để tải đề bài và mở môi trường học tập.</p>
                </div>
                <iframe id="notebook-iframe"></iframe>
            </div>

            <script>
                async function checkStatus() {{
                    const res = await fetch(`/services/assignment-service/api/student/status?moodle_course_id={moodle_course_id}&assignment_id={assignment_id}`);
                    const data = await res.json();
                    
                    let scoreText = `Trạng thái: <strong>${{data.status}}</strong>`;
                    if (data.submitted_at) {{
                        scoreText += ` | Thời gian nộp: <strong>${{data.submitted_at}}</strong>`;
                    }}
                    document.getElementById('score-display').innerHTML = scoreText;
                }}

                async function openAssignment() {{
                    document.getElementById('btn-open').disabled = true;
                    document.getElementById('overlay-text').innerText = "Đang kiểm tra và chuẩn bị môi trường Jupyter, vui lòng đợi...";
                    document.getElementById('spinner').style.display = 'block';

                    const res = await fetch('/services/assignment-service/api/student/open', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{
                            moodle_course_id: "{moodle_course_id}",
                            assignment_id: "{assignment_id}"
                        }})
                    }});
                    const data = await res.json();
                    
                    if(data.status === 'WAITING') {{
                        document.getElementById('overlay-text').innerText = data.message || "Môi trường đang khởi tạo, vui lòng đợi vài giây...";
                        document.getElementById('btn-open').disabled = false;
                        document.getElementById('spinner').style.display = 'none';
                    }} else if(data.status === 'READY') {{
                        document.getElementById('loading-overlay').style.display = 'none';
                        document.getElementById('notebook-iframe').src = data.notebook_url;
                        document.getElementById('btn-submit').style.display = 'inline-block';
                        document.getElementById('btn-open').innerText = "Mở lại bài";
                        document.getElementById('btn-open').disabled = false;
                    }} else {{
                        document.getElementById('overlay-text').innerText = data.detail || "Không mở được bài tập.";
                        document.getElementById('btn-open').disabled = false;
                        document.getElementById('spinner').style.display = 'none';
                    }}
                    checkStatus();
                }}

                async function submitAssignment() {{
                    if(!confirm("Bạn đã lưu bài làm (Ctrl+S) trong Jupyter và chắc chắn muốn nộp bài?")) return;
                    document.getElementById('btn-submit').disabled = true;
                    const res = await fetch('/services/assignment-service/api/student/submit', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{
                            moodle_course_id: "{moodle_course_id}",
                            assignment_id: "{assignment_id}"
                        }})
                    }});
                    const data = await res.json();
                    alert(data.message || data.detail);
                    document.getElementById('btn-submit').disabled = false;
                    checkStatus();
                }}

                // Load status
                checkStatus();
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)
