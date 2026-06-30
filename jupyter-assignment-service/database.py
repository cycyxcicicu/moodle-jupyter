import os
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

DATABASE_URL = os.getenv("ASSIGNMENT_DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("Lỗi: Thiếu biến ASSIGNMENT_DATABASE_URL trong môi trường!")

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600
)

# Khởi tạo cấu trúc cơ sở dữ liệu cho PostgreSQL
def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS courses_mapping (
                moodle_course_id VARCHAR(255) PRIMARY KEY,
                nbgrader_course_id VARCHAR(255) NOT NULL,
                course_title VARCHAR(555)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS assignments_mapping (
                moodle_resource_link_id VARCHAR(255) PRIMARY KEY,
                assignment_id VARCHAR(255) NOT NULL,
                moodle_course_id VARCHAR(255) NOT NULL,
                notebook_name VARCHAR(255),
                type VARCHAR(50) DEFAULT 'ASSIGNMENT',
                start_time TIMESTAMP WITH TIME ZONE,
                end_time TIMESTAMP WITH TIME ZONE,
                duration_minutes INTEGER DEFAULT 0,
                assignment_title VARCHAR(555)
            )
        """))
        
        # Đồng bộ các cột bổ sung của bảng courses_mapping (Migrations)
        try:
            conn.execute(text("ALTER TABLE courses_mapping ADD COLUMN IF NOT EXISTS course_title VARCHAR(555)"))
        except Exception as e:
            print(f"Migration notice for courses_mapping.course_title: {e}")

        # Đồng bộ các cột bổ sung của bảng assignments_mapping (Migrations)
        for col, col_type in [
            ("type", "VARCHAR(50) DEFAULT 'ASSIGNMENT'"),
            ("start_time", "TIMESTAMP WITH TIME ZONE"),
            ("end_time", "TIMESTAMP WITH TIME ZONE"),
            ("duration_minutes", "INTEGER DEFAULT 0"),
            ("assignment_title", "VARCHAR(555)"),
            ("randomize_variants", "BOOLEAN DEFAULT FALSE")
        ]:
            try:
                conn.execute(text(f"ALTER TABLE assignments_mapping ADD COLUMN IF NOT EXISTS {col} {col_type}"))
            except Exception as e:
                print(f"Migration notice for column {col}: {e}")

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                moodle_course_id VARCHAR(255) NOT NULL,
                assignment_id VARCHAR(255) NOT NULL,
                status VARCHAR(50) NOT NULL,
                submitted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                score DOUBLE PRECISION,
                max_score DOUBLE PRECISION
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS grading_jobs (
                assignment_id VARCHAR(255) NOT NULL,
                moodle_course_id VARCHAR(255) NOT NULL,
                status VARCHAR(50) NOT NULL,
                error_msg TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (assignment_id, moodle_course_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS active_sessions (
                username VARCHAR(255) PRIMARY KEY,
                moodle_course_id VARCHAR(255) NOT NULL,
                moodle_resource_link_id VARCHAR(255) NOT NULL,
                role TEXT
            )
        """))
        try:
            conn.execute(text("ALTER TABLE active_sessions ALTER COLUMN role TYPE TEXT"))
        except Exception as e:
            print(f"Migration notice: {e}")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS grades (
                student_id VARCHAR(255) NOT NULL,
                safe_student_id VARCHAR(255) NOT NULL,
                safe_course_id VARCHAR(255) NOT NULL,
                safe_activity_id VARCHAR(255) NOT NULL,
                attempt_no INTEGER,
                score DOUBLE PRECISION,
                comment TEXT,
                graded_by VARCHAR(255),
                graded_at VARCHAR(100),
                grading_status VARCHAR(50) NOT NULL,
                PRIMARY KEY (safe_student_id, safe_course_id, safe_activity_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS grading_sessions (
                session_id VARCHAR(255) PRIMARY KEY,
                teacher_username VARCHAR(255) NOT NULL,
                student_id VARCHAR(255) NOT NULL,
                safe_student_id VARCHAR(255) NOT NULL,
                safe_course_id VARCHAR(255) NOT NULL,
                safe_activity_id VARCHAR(255) NOT NULL,
                attempt_no INTEGER NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS exam_attempts (
                username VARCHAR(255) NOT NULL,
                moodle_course_id VARCHAR(255) NOT NULL,
                moodle_resource_link_id VARCHAR(255) NOT NULL,
                started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                effective_deadline TIMESTAMP WITH TIME ZONE,
                status VARCHAR(50) DEFAULT 'IN_PROGRESS',
                PRIMARY KEY (username, moodle_course_id, moodle_resource_link_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS grade_audit_logs (
                id SERIAL PRIMARY KEY,
                safe_student_id VARCHAR(255) NOT NULL,
                safe_course_id VARCHAR(255) NOT NULL,
                safe_activity_id VARCHAR(255) NOT NULL,
                attempt_no INTEGER,
                old_score DOUBLE PRECISION,
                new_score DOUBLE PRECISION,
                old_comment TEXT,
                new_comment TEXT,
                changed_by VARCHAR(255) NOT NULL,
                changed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                reason TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS student_exam_variants (
                username VARCHAR(255) NOT NULL,
                moodle_course_id VARCHAR(255) NOT NULL,
                moodle_resource_link_id VARCHAR(255) NOT NULL,
                assigned_notebook VARCHAR(255) NOT NULL,
                PRIMARY KEY (username, moodle_course_id, moodle_resource_link_id)
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS enrollment_queue (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) NOT NULL,
                email VARCHAR(100),
                moodle_course_id VARCHAR(50) NOT NULL,
                course_title VARCHAR(150),
                role VARCHAR(20) NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_enrollment_queue_status ON enrollment_queue(status, attempts)"))

        # Tạo các chỉ mục (Indexes) để tối ưu hóa truy vấn
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_submissions_username ON submissions(username)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_submissions_course_assign ON submissions(moodle_course_id, assignment_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_active_sessions_course ON active_sessions(moodle_course_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_grades_course_activity ON grades(safe_course_id, safe_activity_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_exam_attempts_user_course ON exam_attempts(username, moodle_course_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_student_exam_variants_link ON student_exam_variants(moodle_resource_link_id)"))
