#!/usr/bin/env bash
# =========================================================================
# GitLab Local Reset Script
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

# Load environment
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "No .env file found. Nothing to reset."
    exit 0
fi

# Confirmation
CONFIRM=${1:-""}
if [ "${CONFIRM}" != "-f" ]; then
    echo "========================================================================="
    echo "WARNING: This will DESTROY all GitLab repositories, config, logs, "
    echo "         and MinIO buckets/data. This action is IRREVERSIBLE!"
    echo "========================================================================="
    read -rp "Are you sure you want to reset the environment? (y/N): " choice
    case "$choice" in 
      y|Y ) echo "Proceeding with reset...";;
      * ) echo "Reset cancelled."; exit 0;;
    esac
fi

echo "Stopping services and removing containers..."
docker compose down --volumes --remove-orphans

echo "Removing mount directories..."
# Remove configuration files, logs, and user data
if [ -d "${GITLAB_CONFIG_DIR}" ]; then
    echo "Removing config: ${GITLAB_CONFIG_DIR}"
    sudo rm -rf "${GITLAB_CONFIG_DIR}"
fi
if [ -d "${GITLAB_LOGS_DIR}" ]; then
    echo "Removing logs: ${GITLAB_LOGS_DIR}"
    sudo rm -rf "${GITLAB_LOGS_DIR}"
fi
if [ -d "${GITLAB_DATA_DIR}" ]; then
    echo "Removing data: ${GITLAB_DATA_DIR}"
    sudo rm -rf "${GITLAB_DATA_DIR}"
fi
if [ -d "${MINIO_DATA_DIR}" ]; then
    echo "Removing MinIO data: ${MINIO_DATA_DIR}"
    sudo rm -rf "${MINIO_DATA_DIR}"
fi

# Tìm thư mục infra-data
INFRA_DATA_DIR=""
if [ -d "../infra-data" ]; then
    INFRA_DATA_DIR="../infra-data"
elif [ -d "../../infra-data" ]; then
    INFRA_DATA_DIR="../../infra-data"
fi

# Optional drop database if external
if [ "${USE_EXTERNAL_DB_REDIS}" = "true" ]; then
    if docker ps --format '{{.Names}}' | grep -q '^infra-postgres$'; then
        # Đọc cấu hình superuser và db mặc định từ infra-data/.env nếu có
        INFRA_POSTGRES_USER="postgres"
        INFRA_POSTGRES_DB="postgres"
        if [ -n "${INFRA_DATA_DIR}" ] && [ -f "${INFRA_DATA_DIR}/.env" ]; then
            INFRA_POSTGRES_USER=$(grep -E '^POSTGRES_USER=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
            INFRA_POSTGRES_DB=$(grep -E '^POSTGRES_DB=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
        fi
        INFRA_POSTGRES_USER=${INFRA_POSTGRES_USER:-postgres}
        INFRA_POSTGRES_DB=${INFRA_POSTGRES_DB:-postgres}

        echo "Dropping external database: ${EXTERNAL_DB_NAME}..."
        docker exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d "${INFRA_POSTGRES_DB}" <<SQL
DROP DATABASE IF EXISTS "${EXTERNAL_DB_NAME}";
DROP ROLE IF EXISTS "${EXTERNAL_DB_USER}";
SQL
    fi
fi

echo "========================================================================="
echo "Reset completed! The environment is now clean."
echo "You can re-run ./init.sh to setup."
echo "========================================================================="
