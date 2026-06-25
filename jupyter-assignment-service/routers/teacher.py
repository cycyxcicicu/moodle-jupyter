import os
import json
import shutil
import datetime
import tempfile
import uuid
from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy import text

from database import engine
from auth import get_current_user, get_current_lti_session
from services.helpers import safe_id, resolve_course_activity_from_session, check_session_verification
from services.nbgrader import (
    resolve_container,
    is_container_running,
    copy_file_to_container,
    exec_container_nbgrader,
    exec_container_cmd,
    trigger_user_server_start,
    bg_autograde
)

router = APIRouter()

# API: Get List of Assignments
@router.get("/assignments")
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
@router.post("/upload")
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
    
    import re
    orig_filename = file.filename or "assignment.ipynb"
    base, ext = os.path.splitext(orig_filename)
    safe_base = re.sub(r'[^a-zA-Z0-9_-]', '_', base)
    if not safe_base:
        safe_base = "assignment"
    safe_filename = safe_base + ext.lower()
    
    target_path = os.path.join(target_dir, safe_filename)
    with open(target_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        os.chown(target_path, 1000, 1000)
        os.chown(target_dir, 1000, 1000)
    except Exception as e:
        print(f"Warning: chown failed: {e}")
        
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO assignments_mapping (moodle_resource_link_id, assignment_id, moodle_course_id, notebook_name)
                VALUES (:link_id, :assign_id, :course_id, :notebook_name)
                ON CONFLICT (moodle_resource_link_id)
                DO UPDATE SET assignment_id = EXCLUDED.assignment_id, moodle_course_id = EXCLUDED.moodle_course_id, notebook_name = EXCLUDED.notebook_name
            """),
            {
                "link_id": session["moodle_resource_link_id"],
                "assign_id": safe_activity_id,
                "course_id": session["moodle_course_id"],
                "notebook_name": safe_filename
            }
        )
    
    return {"message": "Tải lên đề bài thành công!", "path": target_path}

# API: Teacher Create Empty Assignment
@router.post("/create-assignment")
async def teacher_create_assignment(request: Request, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden: Teacher access only")
        
    data = await request.json()
    moodle_course_id = data.get("moodle_course_id")
    assignment_id = data.get("assignment_id")
    filename = data.get("filename", "assignment")
    
    username = current_user["name"]
    check_session_verification(username, moodle_course_id, assignment_id)
    
    session = get_current_lti_session(username)
    ctx = resolve_course_activity_from_session(session, username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    target_dir = f"/srv/nbgrader/courses/{safe_course_id}/source/{safe_activity_id}"
    os.makedirs(target_dir, exist_ok=True)
    
    import re
    if filename.endswith(".ipynb"):
        filename = filename[:-6]
    safe_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', filename)
    if not safe_filename:
        safe_filename = "assignment"
    safe_filename = safe_filename + ".ipynb"
    
    target_path = os.path.join(target_dir, safe_filename)
    
    # Generate a simple empty jupyter notebook JSON structure
    empty_notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    f"# Bài tập/Bài thi: {safe_filename}\n",
                    "Hãy thực hiện viết code và trả lời các câu hỏi bên dưới."
                ]
            }
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3 (ipykernel)",
                "language": "python",
                "name": "python3"
            },
            "language_info": {
                "name": "python"
            }
        },  
        "nbformat": 4,
        "nbformat_minor": 5
    }
    
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(empty_notebook, f, indent=4)
        
    try:
        os.chown(target_path, 1000, 1000)
        os.chown(target_dir, 1000, 1000)
    except Exception as e:
        print(f"Warning: chown failed: {e}")
        
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO assignments_mapping (moodle_resource_link_id, assignment_id, moodle_course_id, notebook_name)
                VALUES (:link_id, :assign_id, :course_id, :notebook_name)
                ON CONFLICT (moodle_resource_link_id)
                DO UPDATE SET assignment_id = EXCLUDED.assignment_id, moodle_course_id = EXCLUDED.moodle_course_id, notebook_name = EXCLUDED.notebook_name
            """),
            {
                "link_id": session["moodle_resource_link_id"],
                "assign_id": safe_activity_id,
                "course_id": session["moodle_course_id"],
                "notebook_name": safe_filename
            }
        )
        
    return {"message": f"Khởi tạo đề bài '{safe_filename}' thành công!", "path": target_path}

# API: Get Assignment Settings
@router.get("/assignment-settings")
async def get_assignment_settings(moodle_course_id: str, assignment_id: str, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    with engine.begin() as conn:
        res = conn.execute(
            text("""
                SELECT type, start_time, end_time, duration_minutes, randomize_variants 
                FROM assignments_mapping 
                WHERE moodle_course_id = :moodle_course_id AND assignment_id = :assignment_id
            """),
            {"moodle_course_id": moodle_course_id, "assignment_id": assignment_id}
        )
        row = res.fetchone()
        if not row:
            # Try by moodle_resource_link_id too
            res = conn.execute(
                text("""
                    SELECT type, start_time, end_time, duration_minutes, randomize_variants 
                    FROM assignments_mapping 
                    WHERE moodle_resource_link_id = :assignment_id
                """),
                {"assignment_id": assignment_id}
            )
            row = res.fetchone()
            
        if not row:
            return {
                "type": "ASSIGNMENT",
                "start_time": "",
                "end_time": "",
                "duration_minutes": "",
                "randomize_variants": False
            }
            
        asg_type, start_time, end_time, duration_minutes, randomize_variants = row
        
        local_tz = datetime.timezone(datetime.timedelta(hours=7))
        start_local = None
        if start_time:
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=datetime.timezone.utc)
            start_local = start_time.astimezone(local_tz)
            
        end_local = None
        if end_time:
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=datetime.timezone.utc)
            end_local = end_time.astimezone(local_tz)

        return {
            "type": asg_type or "ASSIGNMENT",
            "start_time": start_local.strftime("%Y-%m-%dT%H:%M") if start_local else "",
            "end_time": end_local.strftime("%Y-%m-%dT%H:%M") if end_local else "",
            "duration_minutes": duration_minutes if duration_minutes is not None else "",
            "randomize_variants": bool(randomize_variants)
        }

# API: Update Assignment Settings
@router.post("/update-assignment-settings")
async def update_assignment_settings(request: Request, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    data = await request.json()
    moodle_course_id = data.get("moodle_course_id")
    assignment_id = data.get("assignment_id") # this is assignment_id/resource_link_id
    asg_type = data.get("type", "ASSIGNMENT")
    start_time_str = data.get("start_time")
    end_time_str = data.get("end_time")
    duration_minutes = data.get("duration_minutes")
    randomize_variants = bool(data.get("randomize_variants", False))
    
    local_tz = datetime.timezone(datetime.timedelta(hours=7))
    
    start_time = None
    if start_time_str and start_time_str.strip():
        try:
            dt = datetime.datetime.fromisoformat(start_time_str)
            if dt.tzinfo is None:
                start_time = dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
            else:
                start_time = dt.astimezone(datetime.timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Thời gian bắt đầu không hợp lệ.")
            
    end_time = None
    if end_time_str and end_time_str.strip():
        try:
            dt = datetime.datetime.fromisoformat(end_time_str)
            if dt.tzinfo is None:
                end_time = dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
            else:
                end_time = dt.astimezone(datetime.timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Thời gian kết thúc không hợp lệ.")
            
    dur = None
    if duration_minutes is not None and str(duration_minutes).strip() != "":
        try:
            dur = int(duration_minutes)
        except ValueError:
            raise HTTPException(status_code=400, detail="Thời lượng làm bài phải là số nguyên.")
    
    # Validation cho EXAM
    if asg_type == 'EXAM':
        if not start_time:
            raise HTTPException(status_code=400, detail="Bài thi bắt buộc phải có thời gian bắt đầu.")
        if not end_time and not dur:
            raise HTTPException(status_code=400, detail="Bài thi bắt buộc phải có thời gian kết thúc hoặc thời lượng làm bài (phút).")
        if start_time and end_time and start_time >= end_time:
            raise HTTPException(status_code=400, detail="Thời gian bắt đầu phải trước thời gian kết thúc.")
        if start_time and end_time and dur:
            total_minutes = int((end_time - start_time).total_seconds() / 60)
            if total_minutes < dur:
                local_tz = datetime.timezone(datetime.timedelta(hours=7))
                st_local = start_time.astimezone(local_tz).strftime("%H:%M")
                et_local = end_time.astimezone(local_tz).strftime("%H:%M")
                raise HTTPException(
                    status_code=400, 
                    detail=f"Thời gian mở đề thi (từ {st_local} đến {et_local} là {total_minutes} phút) không được nhỏ hơn thời lượng làm bài ({dur} phút)."
                )
            
    with engine.begin() as conn:
        # Check if record exists
        res = conn.execute(
            text("SELECT 1 FROM assignments_mapping WHERE moodle_resource_link_id = :assignment_id"),
            {"assignment_id": assignment_id}
        )
        if not res.fetchone():
            # insert fallback mapping
            conn.execute(
                text("""
                    INSERT INTO assignments_mapping 
                    (moodle_resource_link_id, assignment_id, moodle_course_id, notebook_name, type, start_time, end_time, duration_minutes, randomize_variants)
                    VALUES (:assignment_id, :assignment_id, :moodle_course_id, 'assignment.ipynb', :type, :start_time, :end_time, :duration_minutes, :randomize_variants)
                """),
                {
                    "assignment_id": assignment_id,
                    "moodle_course_id": moodle_course_id,
                    "type": asg_type,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_minutes": dur,
                    "randomize_variants": randomize_variants
                }
            )
        else:
            conn.execute(
                text("""
                    UPDATE assignments_mapping 
                    SET type = :type, start_time = :start_time, end_time = :end_time, duration_minutes = :duration_minutes, randomize_variants = :randomize_variants
                    WHERE moodle_resource_link_id = :assignment_id
                """),
                {
                    "assignment_id": assignment_id,
                    "type": asg_type,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_minutes": dur,
                    "randomize_variants": randomize_variants
                }
            )
            
    return {"message": "Cập nhật cấu hình bài làm thành công!"}

# API: Teacher release assignment
@router.post("/release")
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
    
    source_dir = f"/srv/nbgrader/courses/{safe_course_id}/source/{safe_activity_id}"
    if not os.path.exists(source_dir) or not any(f.endswith(".ipynb") for f in os.listdir(source_dir) if os.path.isfile(os.path.join(source_dir, f))):
        raise HTTPException(status_code=400, detail="Không tìm thấy file đề bài (.ipynb) nào trong source. Hãy upload hoặc tạo đề bài trước.")
        
    release_dir = f"/srv/nbgrader/courses/{safe_course_id}/release/{safe_activity_id}"
    exchange_dir = f"/srv/nbgrader/exchange/{safe_course_id}/outbound/{safe_activity_id}"
    
    # Dọn dẹp thư mục release và exchange cũ trước khi tạo mới
    if os.path.exists(release_dir):
        shutil.rmtree(release_dir)
    os.makedirs(release_dir, exist_ok=True)
    
    if os.path.exists(exchange_dir):
        shutil.rmtree(exchange_dir)
    os.makedirs(exchange_dir, exist_ok=True)
    
    # Tiến hành chạy nbgrader generate_assignment
    container_name = resolve_container(username)
    generated_via_nbgrader = False
    
    if container_name and is_container_running(container_name):
        print(f"Teacher container {container_name} is running, attempting nbgrader generate_assignment...")
        success, stdout, stderr = exec_container_nbgrader(
            container_name,
            ["generate_assignment", safe_activity_id, f"--config=/srv/nbgrader/courses/{safe_course_id}/nbgrader_config.py", "--force"],
            f"/srv/nbgrader/courses/{safe_course_id}"
        )
        if success:
            generated_via_nbgrader = True
            print("Successfully generated clean assignment using nbgrader.")
        else:
            print(f"Warning: nbgrader generate_assignment failed (stdout: {stdout}, stderr: {stderr}). Falling back to copy...")
    else:
        print("Teacher container not running, falling back to direct copy...")
        
    if generated_via_nbgrader:
        # Copy toàn bộ file sinh ra trong release vào exchange
        for item in os.listdir(release_dir):
            src_item = os.path.join(release_dir, item)
            dst_item = os.path.join(exchange_dir, item)
            if os.path.isdir(src_item):
                shutil.copytree(src_item, dst_item)
            else:
                shutil.copy2(src_item, dst_item)
    else:
        # Chế độ dự phòng: copy trực tiếp toàn bộ thư mục source sang release và exchange
        for item in os.listdir(source_dir):
            src_item = os.path.join(source_dir, item)
            dst_rel = os.path.join(release_dir, item)
            dst_exc = os.path.join(exchange_dir, item)
            if os.path.isdir(src_item):
                shutil.copytree(src_item, dst_rel)
                shutil.copytree(src_item, dst_exc)
            else:
                shutil.copy2(src_item, dst_rel)
                shutil.copy2(src_item, dst_exc)
                
    try:
        def chown_recursive(path, uid, gid):
            os.chown(path, uid, gid)
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    for d in dirs:
                        os.chown(os.path.join(root, d), uid, gid)
                    for f in files:
                        os.chown(os.path.join(root, f), uid, gid)
        chown_recursive(release_dir, 1000, 1000)
        chown_recursive(exchange_dir, 1000, 1000)
        os.chown(os.path.dirname(exchange_dir), 1000, 1000)
        os.chown(os.path.dirname(os.path.dirname(exchange_dir)), 1000, 1000)
    except Exception as e:
        print(f"Warning: chown during release failed: {e}")
        
    if generated_via_nbgrader:
        return {"message": "Phát hành đề bài thành công (Đã chạy nbgrader làm sạch đáp án cho tất cả các đề)."}
    else:
        return {
            "message": "Phát hành đề bài thành công (Chế độ dự phòng sao chép trực tiếp toàn bộ các đề, chưa qua nbgrader).",
            "warning": "Môi trường Jupyter của Giáo viên chưa sẵn sàng để làm sạch đáp án tự động."
        }

# API: Teacher collect submissions
@router.post("/collect")
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
            if os.path.exists(student_sub_dir):
                nb_files = [f for f in os.listdir(student_sub_dir) if f.endswith(".ipynb") and os.path.isfile(os.path.join(student_sub_dir, f))]
                if nb_files:
                    notebook_file = os.path.join(student_sub_dir, nb_files[0])
                    meta_file = os.path.join(student_sub_dir, "submission.json")
                student_id = safe_student_id
                submitted_at = datetime.datetime.now(datetime.timezone.utc)
                if os.path.exists(meta_file):
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                            student_id = meta.get("student_id", safe_student_id)
                            submitted_at_str = meta.get("submitted_at")
                            if submitted_at_str:
                                try:
                                    submitted_at = datetime.datetime.strptime(submitted_at_str, "%Y-%m-%d_%H-%M-%S")
                                except Exception:
                                    try:
                                        submitted_at = datetime.datetime.fromisoformat(submitted_at_str)
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                
                with engine.begin() as conn:
                    result = conn.execute(
                        text("SELECT id FROM submissions WHERE username = :username AND moodle_course_id = :moodle_course_id AND assignment_id = :assignment_id"),
                        {"username": student_id, "moodle_course_id": moodle_course_id, "assignment_id": assignment_id}
                    )
                    row = result.fetchone()
                    if not row:
                        conn.execute(
                            text("INSERT INTO submissions (username, moodle_course_id, assignment_id, status, submitted_at) VALUES (:username, :moodle_course_id, :assignment_id, :status, :submitted_at)"),
                            {"username": student_id, "moodle_course_id": moodle_course_id, "assignment_id": assignment_id, "status": "SUBMITTED", "submitted_at": submitted_at}
                        )
                collected_count += 1
                        
    return {"message": f"Đã đồng bộ và làm mới danh sách bài nộp ({collected_count} bài làm)!"}

# API: Teacher trigger autograding
@router.post("/autograde")
async def teacher_autograde(request: Request, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
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
    
    container_name = resolve_container(username)
    if not container_name or not is_container_running(container_name):
        raise HTTPException(status_code=400, detail="JupyterLab của giáo viên chưa được bật. Vui lòng mở JupyterLab soạn đề trước để kích hoạt container.")
        
    background_tasks.add_task(bg_autograde, container_name, safe_activity_id, moodle_course_id)
    return {"message": "Đang chạy chấm điểm tự động trong nền. Vui lòng đợi trong giây lát và làm mới bảng điểm."}

# API: Teacher submissions grades
@router.get("/submissions")
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
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT username, submitted_at, status 
                FROM submissions 
                WHERE moodle_course_id = :moodle_course_id AND assignment_id = :assignment_id
            """),
            {"moodle_course_id": moodle_course_id, "assignment_id": assignment_id}
        )
        db_rows = result.fetchall()
    
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
            student_sub_dir = f"{submitted_dir}/{safe_student_id}/{safe_activity_id}"
            if os.path.exists(student_sub_dir):
                nb_files = [f for f in os.listdir(student_sub_dir) if f.endswith(".ipynb") and os.path.isfile(os.path.join(student_sub_dir, f))]
                if nb_files:
                    file_path = os.path.join(student_sub_dir, nb_files[0])
                    meta_file = f"{student_sub_dir}/submission.json"
                orig_username = safe_student_id
                submitted_at = "Quét từ thư mục"
                attempt_no = 1
                if os.path.exists(meta_file):
                    try:
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
    with engine.connect() as conn:
        for safe_student_id, s_info in students_dict.items():
            result = conn.execute(
                text("""
                    SELECT score, comment, grading_status, attempt_no
                    FROM grades
                    WHERE safe_student_id = :safe_student_id AND safe_course_id = :safe_course_id AND safe_activity_id = :safe_activity_id
                """),
                {"safe_student_id": safe_student_id, "safe_course_id": safe_course_id, "safe_activity_id": safe_activity_id}
            )
            grade_row = result.fetchone()
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
    
    job_status = "IDLE"
    job_error = None
    with engine.connect() as conn:
        job_res = conn.execute(
            text("""
                SELECT status, error_msg 
                FROM grading_jobs 
                WHERE assignment_id = :assignment_id AND moodle_course_id = :course_id
            """),
            {"assignment_id": safe_activity_id, "course_id": moodle_course_id}
        )
        job_row = job_res.fetchone()
        if job_row:
            job_status = job_row[0]
            job_error = job_row[1]

    return {
        "job_status": job_status,
        "job_error": job_error,
        "students": list(students_dict.values())
    }

# API: Teacher open submission for grading
@router.post("/open-submission")
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
    if not os.path.exists(submitted_dir):
        raise HTTPException(status_code=400, detail="Học sinh chưa nộp bài hoặc file không tồn tại.")
        
    nb_files = [f for f in os.listdir(submitted_dir) if f.endswith(".ipynb") and os.path.isfile(os.path.join(submitted_dir, f))]
    if not nb_files:
        raise HTTPException(status_code=400, detail="Học sinh chưa nộp bài hoặc không tìm thấy file notebook.")
        
    notebook_name = nb_files[0]
        
    attempt_no = 1
    meta_path = f"{submitted_dir}/submission.json"
    if os.path.exists(meta_path):
        try:
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
    
    # Sao chép TOÀN BỘ file notebook đã nộp vào container giáo viên
    for nb_f in nb_files:
        src_path = os.path.join(submitted_dir, nb_f)
        check_cmd = ["test", "-f", f"{dest_dir}/{nb_f}"]
        exists_ok, _, _ = exec_container_cmd(teacher_container, check_cmd, "/home/jovyan")
        if not exists_ok or reset:
            if reset:
                exec_container_cmd(teacher_container, ["rm", "-f", f"{dest_dir}/{nb_f}"], "/home/jovyan")
            success = copy_file_to_container(teacher_container, src_path, dest_dir, nb_f)
            if not success:
                raise HTTPException(status_code=500, detail=f"Không thể sao chép bài nộp {nb_f} vào workspace.")
            
    grading_session_id = uuid.uuid4().hex
    
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO grading_sessions 
                (session_id, teacher_username, student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no)
                VALUES (:session_id, :teacher_username, :student_id, :safe_student_id, :safe_course_id, :safe_activity_id, :attempt_no)
                ON CONFLICT (session_id)
                DO UPDATE SET teacher_username = EXCLUDED.teacher_username, student_id = EXCLUDED.student_id,
                               safe_student_id = EXCLUDED.safe_student_id, safe_course_id = EXCLUDED.safe_course_id,
                               safe_activity_id = EXCLUDED.safe_activity_id, attempt_no = EXCLUDED.attempt_no
            """),
            {
                "session_id": grading_session_id,
                "teacher_username": teacher_username,
                "student_id": student_id,
                "safe_student_id": safe_student_id,
                "safe_course_id": safe_course_id,
                "safe_activity_id": safe_activity_id,
                "attempt_no": attempt_no
            }
        )
    
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
        
    notebook_url = f"/user/{teacher_username}/lab/tree/grading/{safe_activity_id}/{safe_student_id}/{notebook_name}"
    return {
        "status": "READY",
        "grading_session_id": grading_session_id,
        "notebook_url": notebook_url,
        "notebooks": nb_files
    }

# API: Teacher save grade and comment
@router.post("/save-grade")
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
    
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                SELECT student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no, teacher_username
                FROM grading_sessions
                WHERE session_id = :session_id
            """),
            {"session_id": grading_session_id}
        )
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiên chấm bài tương ứng. Vui lòng mở lại bài nộp.")
            
        student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no, session_teacher = row
        
        if session_teacher != teacher_username:
            raise HTTPException(status_code=403, detail="Forbidden: Phiên chấm bài này không thuộc về bạn.")
            
        # Get assignment type
        asg_res = conn.execute(
            text("""
                SELECT am.type 
                FROM assignments_mapping am
                JOIN courses_mapping cm ON am.moodle_course_id = cm.moodle_course_id
                WHERE cm.nbgrader_course_id = :safe_course_id AND am.assignment_id = :safe_activity_id
            """),
            {"safe_course_id": safe_course_id, "safe_activity_id": safe_activity_id}
        )
        asg_row = asg_res.fetchone()
        asg_type = asg_row[0] if asg_row else 'ASSIGNMENT'
        
        # Check if old grade exists
        old_grade_res = conn.execute(
            text("""
                SELECT score, comment 
                FROM grades 
                WHERE safe_student_id = :safe_student_id AND safe_course_id = :safe_course_id AND safe_activity_id = :safe_activity_id
            """),
            {"safe_student_id": safe_student_id, "safe_course_id": safe_course_id, "safe_activity_id": safe_activity_id}
        )
        old_row = old_grade_res.fetchone()
        
        if asg_type == 'EXAM' and old_row is not None:
            reason = data.get("reason")
            if not reason or not reason.strip():
                raise HTTPException(status_code=400, detail="Bài thi đã được chấm trước đó. Bạn phải cung cấp lý do thay đổi điểm số.")
            
            old_score, old_comment = old_row
            conn.execute(
                text("""
                    INSERT INTO grade_audit_logs 
                    (safe_student_id, safe_course_id, safe_activity_id, attempt_no, old_score, new_score, old_comment, new_comment, changed_by, reason)
                    VALUES (:safe_student_id, :safe_course_id, :safe_activity_id, :attempt_no, :old_score, :new_score, :old_comment, :new_comment, :changed_by, :reason)
                """),
                {
                    "safe_student_id": safe_student_id,
                    "safe_course_id": safe_course_id,
                    "safe_activity_id": safe_activity_id,
                    "attempt_no": attempt_no,
                    "old_score": old_score,
                    "new_score": score,
                    "old_comment": old_comment,
                    "new_comment": comment,
                    "changed_by": teacher_username,
                    "reason": reason
                }
            )

        graded_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        conn.execute(
            text("""
                INSERT INTO grades
                (student_id, safe_student_id, safe_course_id, safe_activity_id, attempt_no, score, comment, graded_by, graded_at, grading_status)
                VALUES (:student_id, :safe_student_id, :safe_course_id, :safe_activity_id, :attempt_no, :score, :comment, :graded_by, :graded_at, :grading_status)
                ON CONFLICT (safe_student_id, safe_course_id, safe_activity_id)
                DO UPDATE SET student_id = EXCLUDED.student_id, attempt_no = EXCLUDED.attempt_no, score = EXCLUDED.score,
                              comment = EXCLUDED.comment, graded_by = EXCLUDED.graded_by, graded_at = EXCLUDED.graded_at,
                              grading_status = EXCLUDED.grading_status
            """),
            {
                "student_id": student_id,
                "safe_student_id": safe_student_id,
                "safe_course_id": safe_course_id,
                "safe_activity_id": safe_activity_id,
                "attempt_no": attempt_no,
                "score": score,
                "comment": comment,
                "graded_by": teacher_username,
                "graded_at": graded_at,
                "grading_status": "GRADED"
            }
        )
    
    return {"message": "Lưu điểm và nhận xét thành công!"}

# API: Teacher download direct assignment file
@router.get("/download-submission")
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
    
    submitted_dir = f"/srv/nbgrader/courses/{safe_course_id}/submitted/{safe_student_id}/{safe_activity_id}"
    notebook_name = "assignment.ipynb"
    if os.path.exists(submitted_dir):
        notebooks = [f for f in os.listdir(submitted_dir) if f.endswith(".ipynb")]
        if notebooks:
            notebook_name = notebooks[0]
            
    file_path = os.path.join(submitted_dir, notebook_name)
    
    real_path = os.path.realpath(file_path)
    base_courses_dir = os.path.realpath("/srv/nbgrader/courses")
    if not real_path.startswith(base_courses_dir):
        raise HTTPException(status_code=400, detail="Yêu cầu tải file không hợp lệ (Directory Traversal).")
        
    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy file bài nộp.")
        
    return FileResponse(
        real_path,
        media_type="application/octet-stream",
        filename=f"{notebook_name[:-6]}_{student_id}_{safe_activity_id}.ipynb"
    )

# API: Teacher Get Files of Assignment
@router.get("/assignment-files")
async def get_assignment_files(moodle_course_id: str, assignment_id: str, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    username = current_user["name"]
    check_session_verification(username, moodle_course_id, assignment_id)
    
    ctx = resolve_course_activity_from_session(get_current_lti_session(username), username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    source_dir = f"/srv/nbgrader/courses/{safe_course_id}/source/{safe_activity_id}"
    files = []
    
    if os.path.exists(source_dir):
        for item in os.listdir(source_dir):
            item_path = os.path.join(source_dir, item)
            if os.path.isfile(item_path):
                stat = os.stat(item_path)
                files.append({
                    "name": item,
                    "size": stat.st_size,
                    "is_notebook": item.endswith(".ipynb")
                })
                
    # Sort: notebooks first, then alphabetically
    files.sort(key=lambda x: (not x["is_notebook"], x["name"]))
    return {"files": files}

# API: Teacher Delete Assignment File
@router.post("/delete-file")
async def delete_assignment_file(request: Request, current_user: dict = Depends(get_current_user)):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    data = await request.json()
    moodle_course_id = data.get("moodle_course_id")
    assignment_id = data.get("assignment_id")
    filename = data.get("filename")
    
    if not filename or "/" in filename or "\\" in filename or filename == "." or filename == "..":
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ.")
        
    username = current_user["name"]
    check_session_verification(username, moodle_course_id, assignment_id)
    
    ctx = resolve_course_activity_from_session(get_current_lti_session(username), username)
    safe_course_id = ctx["safe_course_id"]
    safe_activity_id = ctx["safe_activity_id"]
    
    file_path = f"/srv/nbgrader/courses/{safe_course_id}/source/{safe_activity_id}/{filename}"
    if os.path.exists(file_path):
        os.remove(file_path)
        return {"message": f"Xóa file {filename} thành công."}
    else:
        raise HTTPException(status_code=404, detail="File không tồn tại.")
