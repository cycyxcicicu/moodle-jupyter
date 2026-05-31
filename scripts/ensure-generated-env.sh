#!/bin/bash
# Đảm bảo thư mục generated và tệp lti.env tồn tại để tránh lỗi docker compose config/up

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

mkdir -p "$PROJECT_ROOT/generated"
if [ ! -f "$PROJECT_ROOT/generated/lti.env" ]; then
    touch "$PROJECT_ROOT/generated/lti.env"
fi
