import os
import urllib.parse
import httpx
from fastapi import Request, HTTPException, Depends
from sqlalchemy import text
from database import engine

JUPYTERHUB_API_URL = os.getenv("JUPYTERHUB_API_URL", "http://jupyterhub:8000/hub/api")
JUPYTERHUB_API_TOKEN = os.getenv("JUPYTERHUB_API_TOKEN", "super-secret-token")

# Hàm phụ trợ: lấy phiên làm việc LTI hiện tại của người dùng
def get_current_lti_session(username: str) -> dict:
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT moodle_course_id, moodle_resource_link_id, role FROM active_sessions WHERE username = :username"),
            {"username": username}
        )
        row = result.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="No active LTI session found. Please open this activity from Moodle.")
    return {
        "moodle_course_id": row[0],
        "moodle_resource_link_id": row[1],
        "role": row[2] or ""
    }

# Hàm phụ trợ: kiểm tra xem có phải phiên làm việc của Giáo viên không
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

# Hàm phụ trợ: Xác thực người dùng JupyterHub từ Request Cookie
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
            
    # Cho phép bypass xác thực phục vụ kiểm thử cục bộ qua HTTP header
    test_user = request.headers.get("X-Test-User")
    if test_user:
        return {"name": test_user, "admin": "teacher" in test_user.lower() or "admin" in test_user.lower()}

    if not cookie_value:
        raise HTTPException(status_code=401, detail="Unauthorized: No JupyterHub cookie found")
        
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
                
                # Kiểm tra phiên LTI để gán cờ admin chuẩn xác (theo vai trò LTI)
                try:
                    session = get_current_lti_session(username)
                    user_data["admin"] = is_teacher_session(session, username)
                except Exception:
                    user_data["admin"] = "teacher" in username.lower() or "admin" in username.lower()
                
                return user_data
        except Exception as e:
            print(f"JupyterHub API auth error: {e}")
            
    raise HTTPException(status_code=401, detail="Unauthorized: Invalid session")
