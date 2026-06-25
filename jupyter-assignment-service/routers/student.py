import os
import json
import shutil
import datetime
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text

from database import engine
from auth import get_current_user, get_current_lti_session
from services.helpers import safe_id, resolve_course_activity_from_session, check_session_verification
from services.nbgrader import (
    resolve_container,
    is_container_running,
    copy_file_to_container,
    collect_student_notebook,
    exec_container_cmd,
    trigger_user_server_start
)

router = APIRouter()

# API: Student open assignment
@router.post("/open")
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
    
    # 1. Fetch assignment timing & type
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                SELECT type, start_time, end_time, duration_minutes, randomize_variants 
                FROM assignments_mapping 
                WHERE moodle_resource_link_id = :resource_link_id
            """),
            {"resource_link_id": assignment_id}
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Bài tập chưa được liên kết đồng bộ.")
            
        asg_type, start_time, end_time, duration_minutes, randomize_variants = row

    current_time = datetime.datetime.now(datetime.timezone.utc)
    
    # 2. Check timing constraints for EXAM
    if asg_type == 'EXAM':
        if start_time and current_time < start_time:
            local_tz = datetime.timezone(datetime.timedelta(hours=7))
            local_start = start_time.astimezone(local_tz)
            start_str = local_start.strftime("%H:%M:%S ngày %d/%m/%Y")
            raise HTTPException(
                status_code=403, 
                detail=f"Bài thi chưa bắt đầu. Vui lòng quay lại vào lúc {start_str}."
            )
        if end_time and current_time > end_time:
            raise HTTPException(
                status_code=403, 
                detail="Bài thi đã kết thúc."
            )

        # Check / create exam attempt
        with engine.begin() as conn:
            result_attempt = conn.execute(
                text("""
                    SELECT started_at, effective_deadline, status 
                    FROM exam_attempts 
                    WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                """),
                {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
            )
            row_attempt = result_attempt.fetchone()
            
            if row_attempt:
                started_at, effective_deadline, status = row_attempt
                if status in ["SUBMITTED", "AUTO_SUBMITTED", "EXPIRED", "LOCKED"]:
                    raise HTTPException(
                        status_code=403, 
                        detail=f"Bài thi đã kết thúc (Trạng thái: {status}). Bạn không thể tiếp tục làm bài."
                    )
                if effective_deadline and current_time > effective_deadline:
                    raise HTTPException(
                        status_code=403,
                        detail="Thời gian làm bài thi của bạn đã hết."
                    )
            else:
                # First time starting the exam! Calculate effective_deadline
                started_at = current_time
                effective_deadline = None
                if duration_minutes and duration_minutes > 0:
                    dur_delta = datetime.timedelta(minutes=duration_minutes)
                    started_plus_dur = started_at + dur_delta
                    if end_time:
                        effective_deadline = min(end_time, started_plus_dur)
                    else:
                        effective_deadline = started_plus_dur
                else:
                    effective_deadline = end_time
                
                conn.execute(
                    text("""
                        INSERT INTO exam_attempts (username, moodle_course_id, moodle_resource_link_id, started_at, effective_deadline, status)
                        VALUES (:username, :moodle_course_id, :moodle_resource_link_id, :started_at, :effective_deadline, 'IN_PROGRESS')
                    """),
                    {
                        "username": username,
                        "moodle_course_id": moodle_course_id,
                        "moodle_resource_link_id": assignment_id,
                        "started_at": started_at,
                        "effective_deadline": effective_deadline
                    }
                )

    # 3. Xác thực thư mục đề bài được phát hành
    release_dir = f"/srv/nbgrader/courses/{safe_course_id}/release/{safe_activity_id}"
    if not os.path.exists(release_dir):
        raise HTTPException(status_code=400, detail="Đề bài chưa được phát hành bởi Giáo viên.")
        
    notebooks = [f for f in os.listdir(release_dir) if f.endswith(".ipynb") and os.path.isfile(os.path.join(release_dir, f))]
    if not notebooks:
        raise HTTPException(status_code=400, detail="Thư mục đề bài không chứa file notebook (.ipynb) nào.")
        
    # Sắp xếp danh sách file notebook theo bảng chữ cái để nhất quán
    notebooks.sort()

    assigned_notebook = None
    if randomize_variants:
        # Cơ chế trộn đề ngẫu nhiên chia đều (Balanced Randomization)
        with engine.begin() as conn:
            res_var = conn.execute(
                text("""
                    SELECT assigned_notebook 
                    FROM student_exam_variants 
                    WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                """),
                {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
            )
            row_var = res_var.fetchone()
            if row_var:
                assigned_notebook = row_var[0]
                # Nếu đề đã gán trước đó bị giáo viên xóa khỏi release, tính toán gán lại đề mới
                if assigned_notebook not in notebooks:
                    assigned_notebook = None
            
            if not assigned_notebook:
                # Đếm số lượng học sinh được phát của từng đề thi hiện tại trong lớp
                res_cnt = conn.execute(
                    text("""
                        SELECT assigned_notebook, COUNT(*) as cnt 
                        FROM student_exam_variants 
                        WHERE moodle_resource_link_id = :resource_link_id 
                        GROUP BY assigned_notebook
                    """),
                    {"resource_link_id": assignment_id}
                )
                counts = {r[0]: r[1] for r in res_cnt.fetchall()}
                
                # Bản đồ đếm số lượng cho các đề thi đang khả dụng
                nb_counts = []
                for nb in notebooks:
                    cnt = counts.get(nb, 0)
                    nb_counts.append((nb, cnt))
                
                # Tìm lượt phát thấp nhất
                min_cnt = min(c for nb, c in nb_counts)
                # Các đề thi có lượt phát ít nhất (đang bị thiếu)
                candidates = [nb for nb, c in nb_counts if c == min_cnt]
                
                # Lựa chọn ngẫu nhiên 1 đề trong danh sách thiếu
                import random
                assigned_notebook = random.choice(candidates)
                
                # Lưu vào cơ sở dữ liệu
                conn.execute(
                    text("""
                        INSERT INTO student_exam_variants (username, moodle_course_id, moodle_resource_link_id, assigned_notebook)
                        VALUES (:username, :moodle_course_id, :moodle_resource_link_id, :assigned_notebook)
                        ON CONFLICT (username, moodle_course_id, moodle_resource_link_id)
                        DO UPDATE SET assigned_notebook = EXCLUDED.assigned_notebook
                    """),
                    {
                        "username": username,
                        "moodle_course_id": moodle_course_id,
                        "moodle_resource_link_id": assignment_id,
                        "assigned_notebook": assigned_notebook
                    }
                )
    else:
        # Nếu không chọn trộn đề, mặc định mở đề đầu tiên trong danh sách
        assigned_notebook = notebooks[0]

    # 4. Kiểm tra trạng thái hoạt động của Container học sinh
    container_name = resolve_container(username)
    if not container_name or not is_container_running(container_name):
        # Kích hoạt khởi động container nếu ngoại tuyến
        await trigger_user_server_start(username)
        return JSONResponse(content={
            "status": "WAITING",
            "message": "Môi trường của bạn đang được khởi tạo. Vui lòng bấm mở lại sau khoảng 5-10 giây..."
        })
        
    dest_dir = f"/home/jovyan/work/assignments/{safe_activity_id}"
    notebook_url = f"/user/{username}/lab/tree/assignments/{safe_activity_id}/{assigned_notebook}"
    
    # 5. Tiến hành sao chép đề bài vào workspace học sinh
    if randomize_variants:
        # Chỉ copy đúng file đề thi được gán và các tài nguyên (không copy các file .ipynb khác)
        check_cmd = ["test", "-f", f"{dest_dir}/{assigned_notebook}"]
        check_ok, _, _ = exec_container_cmd(container_name, check_cmd, "/home/jovyan")
        if not check_ok:
            src_path = os.path.join(release_dir, assigned_notebook)
            success = copy_file_to_container(container_name, src_path, dest_dir, assigned_notebook)
            if not success:
                raise HTTPException(status_code=500, detail=f"Lỗi: Không thể sao chép đề thi '{assigned_notebook}' vào workspace.")
                
        # Sao chép các file tài nguyên phi notebook (ví dụ: data.csv, images...)
        for item in os.listdir(release_dir):
            if not item.endswith(".ipynb"):
                src_path = os.path.join(release_dir, item)
                if os.path.isfile(src_path):
                    copy_file_to_container(container_name, src_path, dest_dir, item)
    else:
        # Không trộn đề: sao chép tất cả các file có trong release sang cho học sinh
        first_nb = notebooks[0]
        check_cmd = ["test", "-f", f"{dest_dir}/{first_nb}"]
        check_ok, _, _ = exec_container_cmd(container_name, check_cmd, "/home/jovyan")
        if not check_ok:
            for item in os.listdir(release_dir):
                src_path = os.path.join(release_dir, item)
                if os.path.isfile(src_path):
                    copy_file_to_container(container_name, src_path, dest_dir, item)

    # 6. Tính toán thời gian đếm ngược còn lại ( EXAM )
    remaining_seconds = None
    if asg_type == 'EXAM':
        with engine.begin() as conn:
            result_attempt = conn.execute(
                text("""
                    SELECT effective_deadline 
                    FROM exam_attempts 
                    WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                """),
                {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
            )
            row_attempt = result_attempt.fetchone()
            if row_attempt and row_attempt[0]:
                eff_dl = row_attempt[0]
                remaining_seconds = int((eff_dl - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
                if remaining_seconds < 0:
                    remaining_seconds = 0

    return {
        "status": "READY",
        "notebook_url": notebook_url,
        "type": asg_type,
        "remaining_seconds": remaining_seconds
    }

# API: Student submit assignment
@router.post("/submit")
async def student_submit(request: Request, current_user: dict = Depends(get_current_user)):
    data = await request.json()
    username = current_user["name"]
    moodle_course_id = str(data.get("moodle_course_id"))
    assignment_id = str(data.get("assignment_id"))
    
    check_session_verification(username, moodle_course_id, assignment_id)
    
    ctx = resolve_course_activity_from_session(get_current_lti_session(username), username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    # 1. Fetch assignment timing & type
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                SELECT type, end_time, randomize_variants 
                FROM assignments_mapping 
                WHERE moodle_resource_link_id = :resource_link_id
            """),
            {"resource_link_id": assignment_id}
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Bài tập chưa được liên kết đồng bộ.")
        asg_type, end_time, randomize_variants = row

    current_time = datetime.datetime.now(datetime.timezone.utc)
    
    # 2. Handle EXAM single submission enforcement & transaction state locking
    if asg_type == 'EXAM':
        with engine.begin() as conn:
            # Use ROW-LEVEL LOCKING to select and lock the exam attempt row
            result_lock = conn.execute(
                text("""
                    SELECT status, effective_deadline 
                    FROM exam_attempts 
                    WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                    FOR UPDATE
                """),
                {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
            )
            row_attempt = result_lock.fetchone()
            if not row_attempt:
                raise HTTPException(status_code=400, detail="Không tìm thấy phiên làm bài thi hợp lệ.")
            
            status, effective_deadline = row_attempt
            if status in ["SUBMITTING", "SUBMITTED", "AUTO_SUBMITTING", "AUTO_SUBMITTED", "EXPIRED", "LOCKED"]:
                raise HTTPException(status_code=403, detail="Bài thi đã được nộp hoặc đã hết thời gian.")
            
            # Check deadline
            if effective_deadline and current_time > effective_deadline:
                # Too late for manual submit, transition status to AUTO_SUBMITTING to collect work
                conn.execute(
                    text("""
                        UPDATE exam_attempts SET status = 'AUTO_SUBMITTING' 
                        WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                    """),
                    {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
                )
                status = "AUTO_SUBMITTING"
            else:
                # Transition status to SUBMITTING to lock out other parallel requests
                conn.execute(
                    text("""
                        UPDATE exam_attempts SET status = 'SUBMITTING' 
                        WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                    """),
                    {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
                )
                status = "SUBMITTING"

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_student_id = safe_id(username, "student")
    
    # 3. Resolve attempt number
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
    
    # Xác định các file notebook cần thu thập
    notebook_files = []
    if randomize_variants:
        # Lấy đề thi đã gán cho học sinh này
        with engine.begin() as conn:
            res_var = conn.execute(
                text("""
                    SELECT assigned_notebook FROM student_exam_variants 
                    WHERE username = :username AND moodle_course_id = :course_id AND moodle_resource_link_id = :link_id
                """),
                {"username": username, "course_id": moodle_course_id, "link_id": assignment_id}
            )
            row_var = res_var.fetchone()
            if row_var:
                notebook_files = [row_var[0]]
            else:
                # Nếu không tìm thấy, quét thư mục release để làm phương án dự phòng
                release_dir = f"/srv/nbgrader/courses/{safe_course_id}/release/{safe_activity_id}"
                if os.path.exists(release_dir):
                    notebook_files = [f for f in os.listdir(release_dir) if f.endswith(".ipynb")]
                else:
                    notebook_files = ["assignment.ipynb"]
    else:
        # Lấy toàn bộ các file .ipynb có trong release
        release_dir = f"/srv/nbgrader/courses/{safe_course_id}/release/{safe_activity_id}"
        if os.path.exists(release_dir):
            notebook_files = [f for f in os.listdir(release_dir) if f.endswith(".ipynb")]
        if not notebook_files:
            notebook_files = ["assignment.ipynb"]

    # 4. Thu thập toàn bộ các file notebook được chỉ định
    success_count = 0
    collected_files = []
    for notebook_name in notebook_files:
        dest_file_main = f"{dest_submitted_dir}/{notebook_name}"
        success = collect_student_notebook(username, safe_course_id, safe_activity_id, dest_file_main, notebook_name)
        if success:
            success_count += 1
            collected_files.append(notebook_name)
            
            # Copy sang thư mục attempt và exchange
            dest_file_attempt = f"{dest_attempt_dir}/{notebook_name}"
            dest_file_exchange = f"{dest_exchange_dir}/{notebook_name}"
            shutil.copy2(dest_file_main, dest_file_attempt)
            shutil.copy2(dest_file_main, dest_file_exchange)
            
            # Chuyển quyền file đề bài trong container của học sinh thành Read-only (chỉ đọc)
            try:
                container_name = resolve_container(username)
                if container_name and is_container_running(container_name):
                    exec_container_cmd(container_name, ["chmod", "444", f"/home/jovyan/work/assignments/{safe_activity_id}/{notebook_name}"], "/home/jovyan")
            except Exception as e:
                print(f"Warning: chmod read-only failed for {notebook_name}: {e}")
            
            
    if success_count == 0:
        # Khôi phục trạng thái làm bài nếu không thu thập được file nào
        if asg_type == 'EXAM':
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE exam_attempts SET status = 'IN_PROGRESS' 
                        WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                    """),
                    {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
                )
        raise HTTPException(status_code=500, detail="Không thể thu thập bất kỳ file bài làm nào từ workspace hoặc máy chủ.")
        
    # Tạo metadata submission.json
    submission_metadata = {
        "student_id": username,
        "safe_student_id": safe_student_id,
        "activity_id": safe_activity_id,
        "submitted_at": timestamp,
        "attempt_no": attempt_no,
        "submitted_files": collected_files
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
        
    # 5. Save submission record and transition status to final state
    final_status = "SUBMITTED"
    if asg_type == 'EXAM':
        final_attempt_status = "AUTO_SUBMITTED" if status == "AUTO_SUBMITTING" else "SUBMITTED"
        final_status = final_attempt_status
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE exam_attempts SET status = :status
                    WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                """),
                {"status": final_attempt_status, "username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
            )
            
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO submissions (username, moodle_course_id, assignment_id, status, submitted_at) VALUES (:username, :moodle_course_id, :assignment_id, :status, :submitted_at)"),
            {"username": username, "moodle_course_id": moodle_course_id, "assignment_id": assignment_id, "status": final_status, "submitted_at": datetime.datetime.now(datetime.timezone.utc)}
        )
        
    if final_status == "AUTO_SUBMITTED":
        raise HTTPException(status_code=403, detail="Bài thi đã hết hạn làm bài. Bài làm lưu cuối cùng của bạn đã được tự động nộp thành công.")
    
    return {"message": "Nộp bài thành công!"}

# API: Student status check
@router.get("/status")
async def student_status(request: Request, moodle_course_id: str, assignment_id: str, current_user: dict = Depends(get_current_user)):
    username = current_user["name"]
    check_session_verification(username, moodle_course_id, assignment_id)
    
    with engine.connect() as conn:
        # Get assignment type
        asg_res = conn.execute(
            text("SELECT type FROM assignments_mapping WHERE moodle_resource_link_id = :assignment_id"),
            {"assignment_id": assignment_id}
        )
        asg_row = asg_res.fetchone()
        asg_type = asg_row[0] if asg_row else 'ASSIGNMENT'
        
        # Get last submission
        result = conn.execute(
            text("""
                SELECT status, submitted_at 
                FROM submissions 
                WHERE username = :username AND moodle_course_id = :moodle_course_id AND assignment_id = :assignment_id 
                ORDER BY id DESC LIMIT 1
            """),
            {"username": username, "moodle_course_id": moodle_course_id, "assignment_id": assignment_id}
        )
        row = result.fetchone()
        
        # Get exam attempt if EXAM
        exam_status = None
        remaining_seconds = None
        if asg_type == 'EXAM':
            attempt_res = conn.execute(
                text("""
                    SELECT status, effective_deadline 
                    FROM exam_attempts 
                    WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                """),
                {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
            )
            att_row = attempt_res.fetchone()
            if att_row:
                exam_status = att_row[0]
                eff_dl = att_row[1]
                if eff_dl:
                    remaining_seconds = int((eff_dl - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
                    if remaining_seconds < 0:
                        remaining_seconds = 0
            else:
                exam_status = "NOT_STARTED"
                
    response_data = {
        "status": row[0] if row else "NOT_STARTED",
        "submitted_at": row[1].isoformat() if (row and row[1]) else None,
        "type": asg_type
    }
    if asg_type == 'EXAM':
        response_data["exam_status"] = exam_status
        response_data["remaining_seconds"] = remaining_seconds
    return response_data
