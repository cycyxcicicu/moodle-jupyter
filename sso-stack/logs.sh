#!/usr/bin/env bash
# =========================================================================
# Script Theo dõi Logs các dịch vụ SSO Stack
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

if [ $# -gt 0 ]; then
    docker compose logs -f "$@"
else
    docker compose logs -f
fi
