#!/usr/bin/env bash
# =========================================================================
# Script Dừng JupyterHub Cô lập
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}/.."

echo "Đang dừng dịch vụ JupyterHub..."
docker compose down

echo "Dịch vụ đã được dừng thành công."
