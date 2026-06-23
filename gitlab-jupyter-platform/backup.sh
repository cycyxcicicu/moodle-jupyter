#!/usr/bin/env bash
# =========================================================================
# GitLab Local Backup Script
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

# 1. Load environment variables
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "Error: .env file not found!"
    exit 1
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="${SCRIPT_DIR}/backup/backup_${TIMESTAMP}"
mkdir -p "${BACKUP_DIR}/config"

echo "========================================================================="
echo "Starting GitLab & MinIO Local Backup"
echo "Timestamp: ${TIMESTAMP}"
echo "Backup Directory: ${BACKUP_DIR}"
echo "========================================================================="

# 2. Check if gitlab container is running
if ! docker ps --format '{{.Names}}' | grep -q '^gitlab-ce$'; then
    echo "Error: gitlab-ce container is not running! GitLab must be running to perform backup."
    exit 1
fi

# 3. Trigger GitLab Backup (repos + DB if bundled)
echo "Triggering GitLab backup (repositories and databases)..."
docker exec -t gitlab-ce gitlab-backup create

# 4. Copy GitLab configuration & secrets (CRITICAL: gitlab-backup does not include these!)
echo "Backing up configuration and secrets..."
if [ -f "${GITLAB_CONFIG_DIR}/gitlab-secrets.json" ]; then
    cp "${GITLAB_CONFIG_DIR}/gitlab-secrets.json" "${BACKUP_DIR}/config/"
    echo "Saved gitlab-secrets.json"
else
    echo "Warning: gitlab-secrets.json not found in config directory."
fi

if [ -f "${GITLAB_CONFIG_DIR}/gitlab.rb" ]; then
    cp "${GITLAB_CONFIG_DIR}/gitlab.rb" "${BACKUP_DIR}/config/"
    echo "Saved gitlab.rb"
fi

# 5. Locate and move the GitLab backup archive to our backup folder
echo "Locating GitLab backup archive..."
CONTAINER_BACKUPS_DIR="${GITLAB_DATA_DIR}/backups"
if [ -d "${CONTAINER_BACKUPS_DIR}" ]; then
    # Find the most recently modified tar file in backups directory
    LATEST_BACKUP=$(find "${CONTAINER_BACKUPS_DIR}" -name "*_gitlab_backup.tar" -type f -printf '%T@ %p\n' | sort -n | tail -1 | cut -f2- -d' ')
    if [ -n "${LATEST_BACKUP}" ]; then
        echo "Moving latest GitLab backup archive to destination: $(basename "${LATEST_BACKUP}")"
        mv "${LATEST_BACKUP}" "${BACKUP_DIR}/"
    else
        echo "Warning: Could not find generated GitLab backup archive (.tar file) in ${CONTAINER_BACKUPS_DIR}."
    fi
else
    echo "Warning: Backups directory ${CONTAINER_BACKUPS_DIR} does not exist."
fi

# 6. Backup MinIO data (tar compress minio/data)
echo "Backing up MinIO data directory..."
if [ -d "${MINIO_DATA_DIR}" ]; then
    tar -czf "${BACKUP_DIR}/minio_data.tar.gz" -C "$(dirname "${MINIO_DATA_DIR}")" "$(basename "${MINIO_DATA_DIR}")"
    echo "Saved minio_data.tar.gz"
else
    echo "Warning: MinIO data directory ${MINIO_DATA_DIR} does not exist."
fi

echo "========================================================================="
echo "Backup process completed successfully!"
echo "Files are saved at: ${BACKUP_DIR}"
ls -lh "${BACKUP_DIR}"
echo "========================================================================="
