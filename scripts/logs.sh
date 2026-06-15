#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Tự động cấp quyền thực thi cho toàn bộ các script trong thư mục scripts
chmod +x "$SCRIPT_DIR"/*.sh 2>/dev/null || true

# Đảm bảo file config LTI tồn tại
./scripts/ensure-generated-env.sh

if [[ $# -gt 0 ]]; then
    docker compose logs -f "$@"
else
    docker compose logs -f
fi
