#!/usr/bin/env bash
# =========================================================================
# GitLab Local Stop Script
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

echo "Stopping GitLab and MinIO local services..."
docker compose down

echo "Services stopped successfully."
