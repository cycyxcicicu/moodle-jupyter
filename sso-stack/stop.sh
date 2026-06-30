#!/usr/bin/env bash
# =========================================================================
# Script Dừng các dịch vụ SSO Stack
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

echo "Stopping SSO stack services..."
docker compose down

echo "SSO stack services stopped successfully."
