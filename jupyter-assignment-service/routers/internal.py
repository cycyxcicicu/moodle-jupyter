import os
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import text

from database import engine
from services.helpers import safe_id

router = APIRouter()

JUPYTERHUB_API_TOKEN = os.getenv("JUPYTERHUB_API_TOKEN", "super-secret-token")

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
