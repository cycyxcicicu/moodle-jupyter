#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script di chuyển và đổi tên thư mục làm việc của người dùng JupyterHub.
Hỗ trợ ánh xạ:
- Moodle LTI cũ: moodle_{username} -> {username} (LDAP uid)
- Moodle LTI hashed: m_{username_truncated}_{hash} -> {username}
- GitLab Volume cũ: Sao chép từ thư mục Docker Volume của GitLab sang thư mục host chung.
"""

import os
import sys
import re
import shutil
import argparse
import logging
import subprocess

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("migration")

def parse_arguments():
    parser = argparse.ArgumentParser(description="Migration JupyterHub user data folders to LDAP uids.")
    parser.add_argument("--data-root", required=True, help="Đường dẫn DATA_ROOT chứa thư mục jupyter/users")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ quét và hiển thị sơ đồ ánh xạ, không di chuyển dữ liệu")
    parser.add_argument("--execute", action="store_true", help="Thực hiện dịch chuyển dữ liệu thực tế")
    parser.add_argument("--gitlab-volumes-dir", default="/var/lib/docker/volumes", help="Đường dẫn chứa Docker Volumes của GitLab JupyterHub")
    return parser.parse_args()

def extract_new_username(old_name):
    # [ĐÃ SỬA v3] CẢNH BÁO: Hàm này chỉ xử lý Moodle folder pattern (moodle_xxx hoặc m_xxx_hash).
    # Dữ liệu GitLab không đi qua hàm này mà được xử lý riêng qua gitlab_mappings bằng cách quét Docker Volume.
    # Trước khi chạy ở chế độ --execute, Admin PHẢI chạy --dry-run và xác nhận thủ công từng mapping GitLab.
    
    # Ánh xạ moodle_xxx -> xxx
    if old_name.startswith("moodle_"):
        return old_name[len("moodle_"):]
    
    # Ánh xạ m_[truncated_username]_[8_chars_hash] -> trích xuất phần truncated_username
    match = re.match(r"^m_(.+)_[a-f0-9]{8}$", old_name)
    if match:
        return match.group(1)
        
    return None

def change_ownership(path):
    # Phân quyền 1000:1000 (jovyan:jovyan) cho thư mục sau khi dịch chuyển thành công
    try:
        subprocess.run(["chown", "-R", "1000:1000", path], check=True, capture_output=True)
        logger.info(f"  -> Đã set quyền chown -R 1000:1000 cho {path}")
    except Exception as e:
        logger.error(f"  -> LỖI khi thực hiện chown cho {path}: {e}")

def merge_directories(src, dst):
    # Copy đè không phá hủy các file có sẵn từ nguồn sang đích
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            if os.path.exists(d):
                merge_directories(s, d)
            else:
                shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

def main():
    args = parse_arguments()
    
    if not args.dry_run and not args.execute:
        logger.error("LỖI: Bạn phải chỉ định tham số --dry-run hoặc --execute.")
        sys.exit(1)
        
    users_dir = os.path.join(args.data_root, "jupyter", "users")
    if not os.path.exists(users_dir):
        logger.error(f"LỖI: Thư mục nguồn {users_dir} không tồn tại!")
        sys.exit(1)

    logger.info(f"=== Bắt đầu quét thư mục Moodle Jupyter tại: {users_dir} ===")
    
    # 1. Thu thập danh sách thư mục Moodle cũ
    moodle_mappings = []
    for item in os.listdir(users_dir):
        full_path = os.path.join(users_dir, item)
        if os.path.isdir(full_path):
            new_uid = extract_new_username(item)
            if new_uid:
                moodle_mappings.append((item, new_uid, full_path))
                
    logger.info(f"Tìm thấy {len(moodle_mappings)} thư mục Moodle cũ phù hợp để đổi tên.")
    
    # 2. Thu thập danh sách dữ liệu từ GitLab Named Volumes
    gitlab_mappings = []
    if os.path.exists(args.gitlab_volumes_dir):
        logger.info(f"=== Bắt đầu quét GitLab Named Volumes tại: {args.gitlab_volumes_dir} ===")
        for vol_name in os.listdir(args.gitlab_volumes_dir):
            if vol_name.startswith("gitlab-jupyter-user-"):
                username = vol_name[len("gitlab-jupyter-user-"):]
                vol_path = os.path.join(args.gitlab_volumes_dir, vol_name, "_data")
                if os.path.exists(vol_path):
                    gitlab_mappings.append((vol_name, username, vol_path))
        logger.info(f"Tìm thấy {len(gitlab_mappings)} volumes GitLab cũ để đồng bộ.")
    else:
        logger.warning(f"Không tìm thấy thư mục Docker Volumes của GitLab tại: {args.gitlab_volumes_dir}. Bỏ qua quét GitLab.")

    # 3. HIỂN THỊ CHẾ ĐỘ DRY-RUN
    if args.dry_run:
        logger.info("--- CHẾ ĐỘ KIỂM TRA (DRY-RUN) ---")
        for old, new, path in moodle_mappings:
            target_path = os.path.join(users_dir, new)
            status = "Sẽ di chuyển"
            if os.path.exists(target_path):
                status = "Sẽ gộp (Destination exists)"
            logger.info(f"[Moodle] Mapping: {old} -> {new} ({status})")
            
        for vol_name, user, path in gitlab_mappings:
            target_path = os.path.join(users_dir, user)
            status = "Sẽ copy mới"
            if os.path.exists(target_path):
                status = "Sẽ gộp vào thư mục đã có"
            logger.info(f"[GitLab] Sync: Volume {vol_name} -> {user} ({status})")
            
        logger.info("Chạy khô kết thúc. Hãy chạy lại với tham số --execute để thực thi.")
        return

    # 4. THỰC THI (EXECUTE) DI CHUYỂN
    if args.execute:
        # [ĐÃ SỬA v3] Hiển thị thông tin mapping dự kiến và yêu cầu xác nhận tay từ Admin
        logger.info("--- BẢN ĐỒ ÁNH XẠ DỰ KIẾN (SẼ THỰC THI) ---")
        for old, new, path in moodle_mappings:
            logger.info(f"  [Moodle] {old} -> {new}")
        for vol_name, user, path in gitlab_mappings:
            logger.info(f"  [GitLab] Volume {vol_name} -> {user}")
            
        confirm = input("\nBạn có chắc chắn muốn thực hiện? (yes/no): ").strip().lower()
        if confirm != "yes":
            logger.info("Hủy bỏ thực thi di chuyển theo yêu cầu của Admin.")
            return

        logger.info("--- BẮT ĐẦU THỰC THI DI CHUYỂN DỮ LIỆU THỰC TẾ ---")
        
        # Di chuyển Moodle folders trước
        for old, new, path in moodle_mappings:
            target_path = os.path.join(users_dir, new)
            logger.info(f"Đang xử lý: {old} -> {new}")
            
            try:
                if os.path.exists(target_path):
                    logger.info(f"  -> Thư mục đích {new} đã tồn tại. Tiến hành gộp dữ liệu...")
                    merge_directories(path, target_path)
                    shutil.rmtree(path)
                    logger.info(f"  -> Gộp và xóa thư mục nguồn {old} thành công.")
                else:
                    os.rename(path, target_path)
                    logger.info(f"  -> Đổi tên thành công: {old} -> {new}")
                
                # Phân quyền
                change_ownership(target_path)
            except Exception as e:
                logger.error(f"  -> LỖI nghiêm trọng khi xử lý user {old}: {e}. Chuyển sang user tiếp theo.")

        # Sao chép dữ liệu từ GitLab Volumes
        for vol_name, user, path in gitlab_mappings:
            target_path = os.path.join(users_dir, user)
            logger.info(f"Đang xử lý GitLab Volume: {vol_name} -> {user}")
            
            try:
                os.makedirs(target_path, exist_ok=True)
                # Gộp dữ liệu từ volume của gitlab vào workspace mới
                merge_directories(path, target_path)
                logger.info(f"  -> Đồng bộ dữ liệu GitLab Volume thành công cho {user}.")
                change_ownership(target_path)
            except Exception as e:
                logger.error(f"  -> LỖI khi đồng bộ GitLab volume cho user {user}: {e}")
                
        logger.info("=== QUÁ TRÌNH DI CHUYỂN DỮ LIỆU HOÀN TẤT ===")

if __name__ == "__main__":
    main()
