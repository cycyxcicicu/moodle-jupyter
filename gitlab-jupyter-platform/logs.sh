#!/usr/bin/env bash
# =========================================================================
# GitLab Local Logs Script
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

# If an argument is provided, follow logs for that service, otherwise show all
if [ $# -gt 0 ]; then
    docker compose logs -f "$@"
else
    docker compose logs -f
fi
