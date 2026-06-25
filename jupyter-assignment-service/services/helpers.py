import re
from fastapi import HTTPException
from auth import get_current_lti_session

# Hàm phụ trợ: làm sạch chuỗi id và sinh ra safe_id
def safe_id(value: str, prefix: str) -> str:
    if not value or str(value).strip() == "" or str(value).lower() == "none":
        raise HTTPException(status_code=400, detail=f"Invalid parameter: {prefix} cannot be empty or None")
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", str(value))
    sanitized = sanitized.lower()
    if len(sanitized) > 50:
        sanitized = sanitized[:50]
    return f"{prefix}_{sanitized}"

# Hàm phụ trợ: phân tích ID của khóa học và hoạt động học từ session LTI
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

# Hàm phụ trợ: xác thực nghiêm ngặt ngữ cảnh LTI
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

# Hàm phụ trợ: kiểm tra xác thực phiên hoạt động nhằm đảm bảo bảo mật
def check_session_verification(username: str, moodle_course_id: str, assignment_id: str):
    session = get_current_lti_session(username)
    verify_session_context(session, username, moodle_course_id, assignment_id)
