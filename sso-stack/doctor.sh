#!/usr/bin/env bash
# =========================================================================
# Script Kiểm tra Sức khỏe và Chẩn đoán lỗi SSO Stack
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

echo "========================================================================="
echo "Đang chạy chẩn đoán sức khỏe hệ thống SSO (System Check)"
echo "========================================================================="

# Định nghĩa các hàm thông báo màu sắc
success() { echo -e "\e[32m[PASS]\e[0m $1"; }
warning() { echo -e "\e[33m[WARN]\e[0m $1"; }
error() { echo -e "\e[31m[FAIL]\e[0m $1"; }

# 1. Kiểm tra file cấu hình .env
if [ -f .env ]; then
    set -a
    source <(sed 's/\r$//' .env)
    set +a
    success "Đã tìm thấy tệp .env."
else
    error "Tệp .env không tồn tại. Vui lòng chạy ./init.sh trước."
    exit 1
fi

# 2. Kiểm tra Docker daemon & Docker Compose
if ! docker info >/dev/null 2>&1; then
    error "Docker daemon chưa chạy. Vui lòng bật Docker Desktop/Service."
    exit 1
else
    success "Docker daemon đang hoạt động."
fi

if ! docker compose version >/dev/null 2>&1; then
    error "Docker Compose V2 chưa được cài đặt."
    exit 1
else
    success "Docker Compose đã sẵn sàng."
fi

# 3. Kiểm tra container OpenLDAP
echo "=== Kiểm tra OpenLDAP (sso-openldap) ==="
if ! docker ps --format '{{.Names}}' | grep -q '^sso-openldap$'; then
    error "Container 'sso-openldap' KHÔNG chạy!"
else
    success "Container 'sso-openldap' đang chạy."
    
    # Kiểm tra health status
    LDAP_HEALTH=$(docker inspect -f '{{.State.Health.Status}}' sso-openldap 2>/dev/null || echo "no-healthcheck")
    if [ "${LDAP_HEALTH}" = "healthy" ]; then
        success "OpenLDAP đạt trạng thái healthy."
        
        # Thử chạy ldapsearch kiểm tra dữ liệu
        echo "Đang chạy ldapsearch thử nghiệm bên trong container..."
        if docker exec -i sso-openldap ldapsearch -x -b "${LDAP_BASE_DN}" -h 127.0.0.1 -p 389 >/dev/null 2>&1; then
            success "Truy vấn ldapsearch thành công!"
            
            # Kiểm tra xem user mẫu có tồn tại hay không
            USERS_COUNT=$(docker exec -i sso-openldap ldapsearch -x -b "ou=users,${LDAP_BASE_DN}" -h 127.0.0.1 -p 389 "objectClass=inetOrgPerson" uid | grep -c "uid: " || echo 0)
            if [ "${USERS_COUNT}" -ge 2 ]; then
                success "Đã tìm thấy các tài khoản người dùng mẫu LDAP (Số lượng: ${USERS_COUNT})."
            else
                warning "Không tìm thấy đủ người dùng mẫu LDAP hoặc chưa được import."
            fi
        else
            error "Truy vấn ldapsearch thất bại!"
        fi
    else
        error "OpenLDAP không ở trạng thái healthy! (Trạng thái hiện tại: ${LDAP_HEALTH})"
    fi
fi

# 4. Kiểm tra container Keycloak và kết nối PostgreSQL
echo "=== Kiểm tra Keycloak (sso-keycloak) ==="
if ! docker ps --format '{{.Names}}' | grep -q '^sso-keycloak$'; then
    error "Container 'sso-keycloak' KHÔNG chạy!"
else
    success "Container 'sso-keycloak' đang chạy."
    
    KC_HEALTH=$(docker inspect -f '{{.State.Health.Status}}' sso-keycloak 2>/dev/null || echo "no-healthcheck")
    if [ "${KC_HEALTH}" = "healthy" ]; then
        success "Keycloak đạt trạng thái healthy."
    else
        warning "Keycloak chưa ở trạng thái healthy hoặc đang khởi động. (Trạng thái hiện tại: ${KC_HEALTH})"
    fi
    
    # Kiểm tra PostgreSQL dùng chung
    if docker ps --format '{{.Names}}' | grep -q '^infra-postgres$'; then
        # Kiểm tra sự tồn tại của database keycloak
        INFRA_DATA_DIR=""
        if [ -d "../infra-data" ]; then INFRA_DATA_DIR="../infra-data"; fi
        INFRA_POSTGRES_USER="postgres"
        if [ -n "${INFRA_DATA_DIR}" ] && [ -f "${INFRA_DATA_DIR}/.env" ]; then
            INFRA_POSTGRES_USER=$(grep -E '^POSTGRES_USER=' "${INFRA_DATA_DIR}/.env" | cut -d= -f2 | xargs)
        fi
        INFRA_POSTGRES_USER=${INFRA_POSTGRES_USER:-postgres}

        DB_EXISTS=$(docker exec -i infra-postgres psql -U "${INFRA_POSTGRES_USER}" -d postgres -t -A -c "SELECT 1 FROM pg_database WHERE datname='${KEYCLOAK_DB_NAME}'" 2>/dev/null || echo 0)
        if [ "${DB_EXISTS}" = "1" ]; then
            success "Cơ sở dữ liệu '${KEYCLOAK_DB_NAME}' tồn tại trên PostgreSQL."
        else
            error "Cơ sở dữ liệu '${KEYCLOAK_DB_NAME}' KHÔNG tồn tại!"
        fi
    else
        error "Container PostgreSQL chung 'infra-postgres' KHÔNG chạy!"
    fi

    # Kiểm tra OIDC Discovery Endpoint
    echo "Đang kiểm tra Keycloak OIDC Discovery Endpoint..."
    OIDC_CONFIG_URL="${KC_HOSTNAME_URL:-http://keycloak.school.local:18090}/realms/school/.well-known/openid-configuration"
    if curl -s -f -o /dev/null "${OIDC_CONFIG_URL}"; then
        success "Keycloak OIDC Discovery Endpoint khả dụng tại: ${OIDC_CONFIG_URL}"
        ISSUER_VAL=$(curl -s "${OIDC_CONFIG_URL}" | grep -o '"issuer":"[^"]*' | cut -d'"' -f4 || echo "")
        if [ "${ISSUER_VAL}" = "http://keycloak.school.local:18090/realms/school" ]; then
            success "Keycloak OIDC Issuer khớp chính xác: ${ISSUER_VAL}"
        else
            error "Keycloak OIDC Issuer KHÔNG khớp! Kỳ vọng http://keycloak.school.local:18090/realms/school, thực tế: ${ISSUER_VAL}"
        fi
    else
        error "Không thể kết nối tới Keycloak OIDC Discovery Endpoint tại: ${OIDC_CONFIG_URL}"
    fi
fi

# 5. Kiểm tra container phpLDAPadmin
echo "=== Kiểm tra phpLDAPadmin (sso-ldap-admin) ==="
if ! docker ps --format '{{.Names}}' | grep -q '^sso-ldap-admin$'; then
    error "Container 'sso-ldap-admin' KHÔNG chạy!"
else
    success "Container 'sso-ldap-admin' đang chạy."
    
    PLA_HEALTH=$(docker inspect -f '{{.State.Health.Status}}' sso-ldap-admin 2>/dev/null || echo "no-healthcheck")
    if [ "${PLA_HEALTH}" = "healthy" ]; then
        success "phpLDAPadmin đạt trạng thái healthy."
    else
        warning "phpLDAPadmin chưa ở trạng thái healthy. (Trạng thái hiện tại: ${PLA_HEALTH})"
    fi
fi

echo "========================================================================="
echo "Hoàn thành kiểm tra sức khỏe hệ thống SSO."
echo "========================================================================="
