import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from database import engine
from auth import get_current_user, get_current_lti_session
from services.helpers import safe_id, resolve_course_activity_from_session

router = APIRouter()

# Định vị đường dẫn tuyệt đối đến thư mục templates
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates_dir = os.path.join(current_dir, "templates")
templates = Jinja2Templates(directory=templates_dir)

@router.get("/services/assignment-service/gui", response_class=HTMLResponse)
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
        # Cơ chế dự phòng khi chạy thử nghiệm (dev bypass)
        moodle_course_id = moodle_course_id or "demo"
        assignment_id = assignment_id or "lab01_function"
        safe_course_id = safe_id(moodle_course_id, "course")
        safe_activity_id = safe_id(assignment_id, "activity")

    # Truy vấn tên hiển thị thân thiện của khóa học và bài tập từ CSDL
    course_title = ""
    assignment_title = ""
    with engine.connect() as conn:
        try:
            c_res = conn.execute(
                text("SELECT course_title FROM courses_mapping WHERE moodle_course_id = :moodle_course_id"),
                {"moodle_course_id": moodle_course_id}
            ).fetchone()
            if c_res:
                course_title = c_res[0] or ""
        except Exception as e:
            print(f"Error fetching course title: {e}")

        try:
            a_res = conn.execute(
                text("SELECT assignment_title FROM assignments_mapping WHERE moodle_resource_link_id = :assignment_id"),
                {"assignment_id": assignment_id}
            ).fetchone()
            if a_res:
                assignment_title = a_res[0] or ""
        except Exception as e:
            print(f"Error fetching assignment title: {e}")

    if is_teacher:
        return templates.TemplateResponse("teacher.html", {
            "request": request,
            "moodle_course_id": moodle_course_id,
            "course_title": course_title,
            "assignment_id": assignment_id,
            "assignment_title": assignment_title,
            "username": username,
            "safe_course_id": safe_course_id,
            "safe_activity_id": safe_activity_id
        })
    else:
        return templates.TemplateResponse("student.html", {
            "request": request,
            "moodle_course_id": moodle_course_id,
            "course_title": course_title,
            "assignment_id": assignment_id,
            "assignment_title": assignment_title
        })
