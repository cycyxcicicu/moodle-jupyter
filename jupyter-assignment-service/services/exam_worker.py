import os
import json
import shutil
import datetime
from sqlalchemy import text

from database import engine
from services.helpers import safe_id
from services.nbgrader import collect_student_notebook, resolve_container, is_container_running, exec_container_cmd

async def run_auto_submit_check():
    # 1. Cố gắng lấy khóa tư vấn (advisory lock) để tránh xung đột giữa các luồng/instance
    with engine.begin() as conn:
        lock_res = conn.execute(text("SELECT pg_try_advisory_lock(552199)")).fetchone()
        if not lock_res or not lock_res[0]:
            # Khóa đang bận, bỏ qua lượt này
            return
            
        try:
            current_time = datetime.datetime.now(datetime.timezone.utc)
            
            expired_res = conn.execute(
                text("""
                    SELECT username, moodle_course_id, moodle_resource_link_id 
                    FROM exam_attempts 
                    WHERE status = 'IN_PROGRESS' AND effective_deadline IS NOT NULL AND effective_deadline < :current_time
                """),
                {"current_time": current_time}
            )
            expired_attempts = expired_res.fetchall()
            
            for att in expired_attempts:
                username, moodle_course_id, assignment_id = att
                print(f"Background worker: Detected expired attempt for student {username}, assignment {assignment_id}. Triggering auto-submit...", flush=True)
                
                # Cập nhật trạng thái thành AUTO_SUBMITTING để ngăn các luồng/worker khác xử lý trùng lặp
                conn.execute(
                    text("""
                        UPDATE exam_attempts SET status = 'AUTO_SUBMITTING' 
                        WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                        AND status = 'IN_PROGRESS'
                    """),
                    {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
                )
                
                # Phân giải các safe ID của khóa học và bài tập
                course_map_res = conn.execute(
                    text("SELECT nbgrader_course_id FROM courses_mapping WHERE moodle_course_id = :moodle_course_id"),
                    {"moodle_course_id": moodle_course_id}
                )
                c_row = course_map_res.fetchone()
                if not c_row:
                    print(f"Skipping auto-submit for {username}: course mapping not found for {moodle_course_id}", flush=True)
                    continue
                safe_course_id = c_row[0]
                
                asg_map_res = conn.execute(
                    text("SELECT assignment_id, randomize_variants FROM assignments_mapping WHERE moodle_resource_link_id = :assignment_id"),
                    {"assignment_id": assignment_id}
                )
                a_row = asg_map_res.fetchone()
                if not a_row:
                    print(f"Skipping auto-submit for {username}: assignment mapping not found for {assignment_id}", flush=True)
                    continue
                safe_activity_id, randomize_variants = a_row
                
                # Xác định các file notebook cần thu thập
                notebook_files = []
                if randomize_variants:
                    # Lấy đề thi đã gán cho học sinh này
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
                
                # Thực hiện thu thập file
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                safe_student_id = safe_id(username, "student")
                
                attempts_dir = f"/srv/nbgrader/courses/{safe_course_id}/submitted/{safe_student_id}/{safe_activity_id}/attempts"
                attempt_no = 1
                if os.path.exists(attempts_dir):
                    existing = [d for d in os.listdir(attempts_dir) if d.startswith("attempt_")]
                    attempt_no = len(existing) + 1
                    
                attempt_str = f"attempt_{attempt_no:03d}"
                
                dest_submitted_dir = f"/srv/nbgrader/courses/{safe_course_id}/submitted/{safe_student_id}/{safe_activity_id}"
                dest_attempt_dir = f"{dest_submitted_dir}/attempts/{attempt_str}"
                dest_exchange_dir = f"/srv/nbgrader/exchange/{safe_course_id}/inbound/{safe_student_id}/{safe_activity_id}/{attempt_str}"
                
                os.makedirs(dest_submitted_dir, exist_ok=True)
                os.makedirs(dest_attempt_dir, exist_ok=True)
                os.makedirs(dest_exchange_dir, exist_ok=True)
                
                success_count = 0
                collected_files = []
                for notebook_name in notebook_files:
                    dest_file_main = f"{dest_submitted_dir}/{notebook_name}"
                    success = collect_student_notebook(username, safe_course_id, safe_activity_id, dest_file_main, notebook_name)
                    if success:
                        success_count += 1
                        collected_files.append(notebook_name)
                        
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
                            print(f"Warning: chmod read-only failed in worker for {notebook_name}: {e}", flush=True)
                
                if success_count > 0:
                    submission_metadata = {
                        "student_id": username,
                        "safe_student_id": safe_student_id,
                        "activity_id": safe_activity_id,
                        "submitted_at": timestamp,
                        "attempt_no": attempt_no,
                        "submitted_files": collected_files,
                        "auto_submitted": True
                    }
                    
                    metadata_main_path = f"{dest_submitted_dir}/submission.json"
                    metadata_attempt_path = f"{dest_attempt_dir}/submission.json"
                    
                    with open(metadata_main_path, "w", encoding="utf-8") as f:
                        json.dump(submission_metadata, f, indent=4)
                    with open(metadata_attempt_path, "w", encoding="utf-8") as f:
                        json.dump(submission_metadata, f, indent=4)
                        
                    for path in [dest_submitted_dir, dest_exchange_dir]:
                        try:
                            for root, dirs, files in os.walk(path):
                                for d in dirs:
                                    os.chown(os.path.join(root, d), 1000, 1000)
                                for f_name in files:
                                    os.chown(os.path.join(root, f_name), 1000, 1000)
                            os.chown(path, 1000, 1000)
                        except Exception as e:
                            print(f"Warning: chown failed: {e}", flush=True)
                            
                    # Chuyển trạng thái sang AUTO_SUBMITTED khi thành công
                    conn.execute(
                        text("""
                            UPDATE exam_attempts SET status = 'AUTO_SUBMITTED' 
                            WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                        """),
                        {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
                    )
                    
                    conn.execute(
                        text("INSERT INTO submissions (username, moodle_course_id, assignment_id, status, submitted_at) VALUES (:username, :moodle_course_id, :assignment_id, :status, :submitted_at)"),
                        {"username": username, "moodle_course_id": moodle_course_id, "assignment_id": assignment_id, "status": "AUTO_SUBMITTED", "submitted_at": datetime.datetime.now(datetime.timezone.utc)}
                    )
                    print(f"Background worker: Successfully auto-submitted notebooks for student {username}", flush=True)
                else:
                    # Đánh dấu là EXPIRED nếu không tìm thấy file bài làm để thu thập
                    conn.execute(
                        text("""
                            UPDATE exam_attempts SET status = 'EXPIRED' 
                            WHERE username = :username AND moodle_course_id = :moodle_course_id AND moodle_resource_link_id = :moodle_resource_link_id
                        """),
                        {"username": username, "moodle_course_id": moodle_course_id, "moodle_resource_link_id": assignment_id}
                    )
                    print(f"Background worker: No work found. Transitioned student {username} attempt to EXPIRED.", flush=True)
        finally:
            # 3. Giải phóng khóa (Unlock)
            conn.execute(text("SELECT pg_advisory_unlock(552199)"))
