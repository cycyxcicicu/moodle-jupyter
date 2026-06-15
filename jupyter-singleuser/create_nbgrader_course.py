#!/usr/bin/env python3
import os
import sys
import argparse
import re

def main():
    parser = argparse.ArgumentParser(description="Tạo thư mục khóa học nbgrader động")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--moodle-course-id", type=str, help="ID của khóa học trên Moodle")
    group.add_argument("--nbgrader-course-id", type=str, help="ID khóa học nbgrader cụ thể")
    
    args = parser.parse_args()
    
    if args.nbgrader_course_id:
        course_id = args.nbgrader_course_id
    else:
        course_id = f"moodle_course_{args.moodle_course_id}"
        
    # Validate course_id: Chỉ cho phép ký tự a-z, A-Z, 0-9, gạch dưới (_) và gạch ngang (-)
    if not re.match(r"^[a-zA-Z0-9_-]+$", course_id):
        print(f"Lỗi: course_id '{course_id}' không hợp lệ. Chỉ cho phép ký tự a-z, A-Z, 0-9, gạch dưới (_) và gạch ngang (-).", file=sys.stderr)
        sys.exit(1)
        
    course_dir = f"/srv/nbgrader/courses/{course_id}"
    
    # Tạo các thư mục con
    subdirs = ["source", "release", "submitted", "autograded", "feedback"]
    for d in subdirs:
        os.makedirs(os.path.join(course_dir, d), exist_ok=True)
        
    config_content = f"""c = get_config()  # noqa: F821

# Cấu hình cục bộ tự động cho khóa học {course_id}
c.CourseDirectory.root = "{course_dir}"
c.CourseDirectory.course_id = "{course_id}"

# Cấu hình Exchange dùng chung
c.Exchange.root = "/srv/nbgrader/exchange"
c.Exchange.path_includes_course = True
"""
    
    config_path = os.path.join(course_dir, "nbgrader_config.py")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(config_content)
        
    # Cố gắng gán quyền nếu chạy dưới quyền root
    try:
        import pwd
        jovyan_pw = pwd.getpwnam("jovyan")
        uid = jovyan_pw.pw_uid
        gid = jovyan_pw.pw_gid
        
        for root, dirs, files in os.walk(course_dir):
            for d in dirs:
                os.chown(os.path.join(root, d), uid, gid)
            for f in files:
                os.chown(os.path.join(root, f), uid, gid)
            os.chown(root, uid, gid)
    except Exception:
        pass
        
    print(f"Khởi tạo thành công khóa học nbgrader '{course_id}' tại: {course_dir}")

if __name__ == "__main__":
    main()
