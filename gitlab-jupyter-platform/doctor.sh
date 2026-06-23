#!/usr/bin/env bash
# =========================================================================
# GitLab Local Doctor/Verification Script
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

echo "========================================================================="
echo "Running GitLab Local Doctor (System Check)"
echo "========================================================================="

# Helper functions
success() { echo -e "\e[32m[PASS]\e[0m $1"; }
warning() { echo -e "\e[33m[WARN]\e[0m $1"; }
error() { echo -e "\e[31m[FAIL]\e[0m $1"; }

# 1. Load environment variables
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
    success "Loaded .env file."
else
    error ".env file does not exist. Run ./init.sh first."
    exit 1
fi

# 2. Check Docker daemon & Compose
if ! docker info >/dev/null 2>&1; then
    error "Docker daemon is not running. Please start Docker Desktop/Daemon."
    exit 1
else
    success "Docker daemon is running."
fi

if ! docker compose version >/dev/null 2>&1; then
    error "Docker Compose V2 is not installed."
    exit 1
else
    success "Docker Compose is installed."
fi

# 3. Check mount directories permissions
echo "Checking mount directories..."
for dir in "${GITLAB_CONFIG_DIR}" "${GITLAB_LOGS_DIR}" "${GITLAB_DATA_DIR}" "${MINIO_DATA_DIR}"; do
    if [ ! -d "${dir}" ]; then
        warning "Directory '${dir}' does not exist yet."
    else
        # Try writing a temporary file to test permissions
        if touch "${dir}/.doctor_write_test" 2>/dev/null; then
            rm -f "${dir}/.doctor_write_test"
            success "Directory '${dir}' is writeable."
        else
            error "Directory '${dir}' IS NOT writeable! If this is on a Windows D-drive mount, consider moving GITLAB_DATA_DIR to ext4 (e.g. /home/huynhduc/gitlab-jupyter-platform/gitlab/data)."
        fi
    fi
done

# 4. Check External DB/Redis connection (if enabled)
if [ "${USE_EXTERNAL_DB_REDIS}" = "true" ]; then
    echo "Checking external connections (USE_EXTERNAL_DB_REDIS=true)..."

    # PostgreSQL check
    if ! docker ps --format '{{.Names}}' | grep -q '^infra-postgres$'; then
        error "infra-postgres container is not running!"
    else
        success "infra-postgres container is running."
        
        # Test psql connection
        if docker exec -i infra-postgres pg_isready -U postgres >/dev/null 2>&1; then
            success "infra-postgres is accepting connections."
            
            # Check version
            PG_VER=$(docker exec -i infra-postgres psql -U postgres -t -A -c "SHOW server_version;" | cut -d'.' -f1)
            if [ "${PG_VER}" -ge 14 ]; then
                success "PostgreSQL version is ${PG_VER} (>= 14 requirement met)."
            else
                warning "PostgreSQL version is ${PG_VER}. GitLab CE 17 requires >= 14."
            fi

            # Check database and owner
            DB_EXISTS=$(docker exec -i infra-postgres psql -U postgres -t -A -c "SELECT 1 FROM pg_database WHERE datname='${EXTERNAL_DB_NAME}'")
            if [ "${DB_EXISTS}" = "1" ]; then
                success "Database '${EXTERNAL_DB_NAME}' exists."
                
                # Check extensions
                EXT_TRGM=$(docker exec -i infra-postgres psql -U postgres -d "${EXTERNAL_DB_NAME}" -t -A -c "SELECT 1 FROM pg_extension WHERE extname='pg_trgm'")
                EXT_BIST=$(docker exec -i infra-postgres psql -U postgres -d "${EXTERNAL_DB_NAME}" -t -A -c "SELECT 1 FROM pg_extension WHERE extname='btree_gist'")
                
                if [ "${EXT_TRGM}" = "1" ] && [ "${EXT_BIST}" = "1" ]; then
                    success "PostgreSQL extensions (pg_trgm, btree_gist) are enabled in database '${EXTERNAL_DB_NAME}'."
                else
                    error "Required PostgreSQL extensions are missing in database '${EXTERNAL_DB_NAME}'. Please run ./init.sh to enable them."
                fi
            else
                error "Database '${EXTERNAL_DB_NAME}' does not exist."
            fi
        else
            error "Cannot connect to infra-postgres. Check if container is healthy."
        fi
    fi

    # Redis check
    if ! docker ps --format '{{.Names}}' | grep -q '^infra-redis$'; then
        error "infra-redis container is not running!"
    else
        success "infra-redis container is running."
        
        # Test connection & authentication
        REDIS_PING=$(docker exec -i infra-redis redis-cli -a "${EXTERNAL_REDIS_PASSWORD}" ping 2>/dev/null || true)
        if [ "${REDIS_PING}" = "PONG" ]; then
            success "infra-redis connection verified (ping-pong successful)."
        else
            error "infra-redis authentication failed or redis not responding."
        fi
    fi
fi

# 5. Check MinIO container and buckets
echo "Checking MinIO status..."
if ! docker ps --format '{{.Names}}' | grep -q '^gitlab-minio$'; then
    warning "gitlab-minio container is not running."
else
    success "gitlab-minio container is running."
    
    # Check if minio-creator completed successfully
    CREATOR_STATUS=$(docker ps -a --filter "name=gitlab-minio-creator" --format "{{.State.Status}}" || true)
    CREATOR_EXIT=$(docker ps -a --filter "name=gitlab-minio-creator" --format "{{.State.ExitCode}}" || true)
    
    if [ "${CREATOR_STATUS}" = "exited" ] && [ "${CREATOR_EXIT}" = "0" ]; then
        success "MinIO buckets initialization script finished successfully."
    else
        warning "MinIO bucket creator status: ${CREATOR_STATUS} (exit code: ${CREATOR_EXIT})."
    fi
fi

# 6. Check GitLab container status
echo "Checking GitLab CE status..."
if ! docker ps --format '{{.Names}}' | grep -q '^gitlab-ce$'; then
    warning "gitlab-ce container is not running."
else
    success "gitlab-ce container is running."
    GITLAB_HEALTH=$(docker inspect -f '{{.State.Health.Status}}' gitlab-ce 2>/dev/null || echo "no-healthcheck")
    case "${GITLAB_HEALTH}" in
        healthy) success "gitlab-ce container is healthy.";;
        starting) warning "gitlab-ce container is starting (it might take several minutes).";;
        unhealthy) error "gitlab-ce container is unhealthy! Check logs: ./logs.sh gitlab";;
        no-healthcheck) warning "gitlab-ce container has no healthcheck configured.";;
    esac
fi

echo "========================================================================="
echo "Doctor checks completed."
echo "========================================================================="
