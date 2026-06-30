#!/usr/bin/env bash
# =========================================================================
# Script cấu hình Keycloak (Idempotent):
#   - LDAP User Storage Federation + Attribute Mappers
#   - Clients: moodle-client, gitlab-client, jupyterhub-client
#   - Protocol Mappers: preferred_username, email, given_name, family_name
#   - Trigger LDAP full sync
# =========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

echo "========================================================================="
echo " Đang cấu hình Keycloak: LDAP + Clients + Protocol Mappers"
echo "========================================================================="

# 1. Load biến môi trường từ sso-stack/.env
if [ -f .env ]; then
    set -a
    source <(sed 's/\r$//' .env)
    set +a
else
    echo "❌ Lỗi: Không tìm thấy tệp .env trong thư mục sso-stack/."
    exit 1
fi

LDAP_BASE_DN="${LDAP_BASE_DN:-dc=school,dc=local}"
LDAP_ADMIN_PASSWORD="${LDAP_ADMIN_PASSWORD:-adminpwd123}"
KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN:-admin}"
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-adminpwd123}"
MOODLE_WWWROOT="${MOODLE_WWWROOT:-http://moodle.school.local:18080}"
GITLAB_URL="${GITLAB_URL:-http://gitlab.school.local:18088}"
JUPYTERHUB_URL="${JUPYTERHUB_URL:-http://localhost:18000}"
OPENWEBUI_URL="${OPENWEBUI_URL:-http://localhost:3000}"
MOODLE_OIDC_CLIENT_SECRET="${MOODLE_OIDC_CLIENT_SECRET:-moodle-secret-123}"
GITLAB_OIDC_CLIENT_SECRET="${GITLAB_OIDC_CLIENT_SECRET:-gitlab-secret-123}"
JUPYTERHUB_OIDC_CLIENT_SECRET="${JUPYTERHUB_OIDC_CLIENT_SECRET:-jupyterhub-secret-123}"
OPENWEBUI_OIDC_CLIENT_SECRET="${OPENWEBUI_OIDC_CLIENT_SECRET:-openwebui-secret-123}"

# 2. Kiểm tra container Keycloak
if ! docker ps --format '{{.Names}}' | grep -q '^sso-keycloak$'; then
    echo "❌ Lỗi: Container 'sso-keycloak' chưa khởi chạy."
    exit 1
fi

echo "Đang chờ Keycloak sẵn sàng..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' sso-keycloak 2>/dev/null)" = "healthy" ]; do
    echo -n "."
    sleep 2
done
echo " Keycloak đã sẵn sàng!"

# 3. Xác thực Keycloak Admin CLI
echo "Đang xác thực Keycloak Admin CLI..."
docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080 \
    --realm master \
    --user "${KEYCLOAK_ADMIN}" \
    --password "${KEYCLOAK_ADMIN_PASSWORD}"

# Helper: Lấy admin token qua REST API (dùng cho trigger sync)
get_admin_token() {
    docker exec sso-keycloak curl -sf -X POST \
        "http://localhost:8080/realms/master/protocol/openid-connect/token" \
        -d "client_id=admin-cli" \
        -d "username=${KEYCLOAK_ADMIN}" \
        -d "password=${KEYCLOAK_ADMIN_PASSWORD}" \
        -d "grant_type=password" \
        | grep -oP '"access_token":"\K[^"]+' | head -1
}

# Helper: Thêm 4 protocol mappers chuẩn (preferred_username, email, given_name, family_name)
add_protocol_mappers() {
    local client_uuid="$1"
    local client_name="$2"
    echo "  Thêm protocol mappers cho ${client_name}..."

    docker exec -i sso-keycloak /opt/keycloak/bin/kcadm.sh create \
        "clients/${client_uuid}/protocol-mappers/models" -r school -f - << 'EOF'
{"name":"preferred_username","protocol":"openid-connect","protocolMapper":"oidc-usermodel-property-mapper","consentRequired":false,"config":{"userinfo.token.claim":"true","user.attribute":"username","id.token.claim":"true","access.token.claim":"true","claim.name":"preferred_username","jsonType.label":"String"}}
EOF

    docker exec -i sso-keycloak /opt/keycloak/bin/kcadm.sh create \
        "clients/${client_uuid}/protocol-mappers/models" -r school -f - << 'EOF'
{"name":"email","protocol":"openid-connect","protocolMapper":"oidc-usermodel-property-mapper","consentRequired":false,"config":{"userinfo.token.claim":"true","user.attribute":"email","id.token.claim":"true","access.token.claim":"true","claim.name":"email","jsonType.label":"String"}}
EOF

    docker exec -i sso-keycloak /opt/keycloak/bin/kcadm.sh create \
        "clients/${client_uuid}/protocol-mappers/models" -r school -f - << 'EOF'
{"name":"given_name","protocol":"openid-connect","protocolMapper":"oidc-usermodel-property-mapper","consentRequired":false,"config":{"userinfo.token.claim":"true","user.attribute":"firstName","id.token.claim":"true","access.token.claim":"true","claim.name":"given_name","jsonType.label":"String"}}
EOF

    docker exec -i sso-keycloak /opt/keycloak/bin/kcadm.sh create \
        "clients/${client_uuid}/protocol-mappers/models" -r school -f - << 'EOF'
{"name":"family_name","protocol":"openid-connect","protocolMapper":"oidc-usermodel-property-mapper","consentRequired":false,"config":{"userinfo.token.claim":"true","user.attribute":"lastName","id.token.claim":"true","access.token.claim":"true","claim.name":"family_name","jsonType.label":"String"}}
EOF

    echo "  ✓ Protocol mappers cho ${client_name} đã được thêm"
}

# =============================================================
# 4. LDAP User Storage Federation
# =============================================================
echo ""
echo "--- [1/5] Cấu hình LDAP User Storage Federation ---"

existing_ldap=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get components \
    -r school --fields id,name,providerType 2>/dev/null \
    | grep -B3 '"name" : "openldap"' | grep '"id"' | cut -d'"' -f4 | head -1 || echo "")

if [ -n "${existing_ldap}" ]; then
    echo "Xóa LDAP federation cũ (${existing_ldap})..."
    docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh delete \
        "components/${existing_ldap}" -r school
fi

echo "Tạo LDAP User Storage Provider..."
ldap_create_out=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh create components -r school \
    -s name=openldap \
    -s providerId=ldap \
    -s providerType=org.keycloak.storage.UserStorageProvider \
    -s 'config.connectionUrl=["ldap://openldap:389"]' \
    -s "config.usersDn=[\"ou=users,${LDAP_BASE_DN}\"]" \
    -s "config.bindDn=[\"cn=admin,${LDAP_BASE_DN}\"]" \
    -s "config.bindCredential=[\"${LDAP_ADMIN_PASSWORD}\"]" \
    -s 'config.authType=["simple"]' \
    -s 'config.userObjectClasses=["inetOrgPerson"]' \
    -s 'config.usernameLDAPAttribute=["uid"]' \
    -s 'config.rdnLDAPAttribute=["uid"]' \
    -s 'config.uuidLDAPAttribute=["entryUUID"]' \
    -s 'config.editMode=["READ_ONLY"]' \
    -s 'config.importEnabled=["true"]' \
    -s 'config.syncRegistrations=["false"]' \
    -s 'config.vendor=["other"]' \
    -s 'config.searchScope=["1"]' \
    -s 'config.trustEmail=["true"]' \
    -s 'config.cachePolicy=["DEFAULT"]' \
    -s 'config.enabled=["true"]' \
    -s 'config.pagination=["true"]' \
    -s 'config.fullSyncPeriod=["3600"]' \
    -s 'config.changedSyncPeriod=["60"]' \
    -s 'config.batchSizeForSync=["1000"]' \
    -s 'config.validatePasswordPolicy=["false"]' \
    -s 'config.usePasswordModifyExtendedOp=["false"]' \
    -s 'config.allowKerberosAuthentication=["false"]' \
    -s 'config.debug=["false"]' 2>&1)

echo "${ldap_create_out}"
ldap_uuid=$(echo "${ldap_create_out}" | grep -oP "(?<=with id ')([^']+)" | head -1)

# Fallback: query theo name nếu parse output thất bại
if [ -z "${ldap_uuid}" ]; then
    ldap_uuid=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get components \
        -r school --fields id,name 2>/dev/null \
        | grep -B2 '"name" : "openldap"' | grep '"id"' | cut -d'"' -f4 | head -1)
fi

if [ -z "${ldap_uuid}" ]; then
    echo "❌ Lỗi: Không thể lấy LDAP Provider ID sau khi tạo."
    exit 1
fi
echo "LDAP Provider ID: ${ldap_uuid}"

# Sử dụng các mappers mặc định (username, email, first name, last name...) tự động tạo bởi Keycloak
echo "✓ Sử dụng các LDAP mappers mặc định do Keycloak tự động khởi tạo."

echo "✓ LDAP Federation đã được cấu hình (READ_ONLY, uid → username)"

# =============================================================
# 5. gitlab-client
# =============================================================
echo ""
echo "--- [2/5] Cấu hình gitlab-client ---"
gitlab_uuid=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get clients \
    -r school --query clientId=gitlab-client --fields id \
    | grep '"id"' | cut -d'"' -f4 | head -1 || echo "")

if [ -n "${gitlab_uuid}" ]; then
    echo "Xóa gitlab-client cũ..."
    docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh delete "clients/${gitlab_uuid}" -r school
fi

echo "Tạo mới gitlab-client..."
gitlab_create_out=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh create clients -r school \
    -s clientId=gitlab-client \
    -s name="GitLab CE" \
    -s enabled=true \
    -s protocol=openid-connect \
    -s publicClient=false \
    -s "secret=${GITLAB_OIDC_CLIENT_SECRET}" \
    -s "redirectUris=[\"${GITLAB_URL}/users/auth/openid_connect/callback\",\"${GITLAB_URL}/*\"]" \
    -s "webOrigins=[\"${GITLAB_URL}\"]" 2>&1)
echo "${gitlab_create_out}"
gitlab_uuid=$(echo "${gitlab_create_out}" | grep -oP "(?<=with id ')([^']+)" | head -1)
if [ -z "${gitlab_uuid}" ]; then
    gitlab_uuid=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get clients \
        -r school --query clientId=gitlab-client --fields id \
        | grep '"id"' | cut -d'"' -f4 | head -1)
fi

add_protocol_mappers "${gitlab_uuid}" "gitlab-client"
echo "✓ gitlab-client OK"

# =============================================================
# 6. moodle-client
# =============================================================
echo ""
echo "--- [3/5] Cấu hình moodle-client ---"
moodle_uuid=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get clients \
    -r school --query clientId=moodle-client --fields id \
    | grep '"id"' | cut -d'"' -f4 | head -1 || echo "")

if [ -n "${moodle_uuid}" ]; then
    echo "Xóa moodle-client cũ..."
    docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh delete "clients/${moodle_uuid}" -r school
fi

echo "Tạo mới moodle-client..."
moodle_create_out=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh create clients -r school \
    -s clientId=moodle-client \
    -s name="Moodle Platform" \
    -s enabled=true \
    -s protocol=openid-connect \
    -s publicClient=false \
    -s "secret=${MOODLE_OIDC_CLIENT_SECRET}" \
    -s "redirectUris=[\"${MOODLE_WWWROOT}/*\"]" \
    -s "webOrigins=[\"${MOODLE_WWWROOT}\"]" 2>&1)
echo "${moodle_create_out}"
moodle_uuid=$(echo "${moodle_create_out}" | grep -oP "(?<=with id ')([^']+)" | head -1)
if [ -z "${moodle_uuid}" ]; then
    moodle_uuid=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get clients \
        -r school --query clientId=moodle-client --fields id \
        | grep '"id"' | cut -d'"' -f4 | head -1)
fi

add_protocol_mappers "${moodle_uuid}" "moodle-client"
echo "✓ moodle-client OK"

# =============================================================
# 7. jupyterhub-client
# =============================================================
echo ""
echo "--- [4/5] Cấu hình jupyterhub-client ---"
jupyter_uuid=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get clients \
    -r school --query clientId=jupyterhub-client --fields id \
    | grep '"id"' | cut -d'"' -f4 | head -1 || echo "")

if [ -n "${jupyter_uuid}" ]; then
    echo "Xóa jupyterhub-client cũ..."
    docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh delete "clients/${jupyter_uuid}" -r school
fi

echo "Tạo mới jupyterhub-client..."
jupyter_create_out=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh create clients -r school \
    -s clientId=jupyterhub-client \
    -s name="JupyterHub Platform" \
    -s enabled=true \
    -s protocol=openid-connect \
    -s publicClient=false \
    -s "secret=${JUPYTERHUB_OIDC_CLIENT_SECRET}" \
    -s "redirectUris=[\"${JUPYTERHUB_URL}/*\",\"${MOODLE_WWWROOT}/*\"]" \
    -s "webOrigins=[\"${JUPYTERHUB_URL}\",\"${MOODLE_WWWROOT}\"]" 2>&1)
echo "${jupyter_create_out}"
jupyter_uuid=$(echo "${jupyter_create_out}" | grep -oP "(?<=with id ')([^']+)" | head -1)
if [ -z "${jupyter_uuid}" ]; then
    jupyter_uuid=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get clients \
        -r school --query clientId=jupyterhub-client --fields id \
        | grep '"id"' | cut -d'"' -f4 | head -1)
fi

add_protocol_mappers "${jupyter_uuid}" "jupyterhub-client"
echo "✓ jupyterhub-client OK"

# =============================================================
# 7.5. openwebui-client
# =============================================================
echo ""
echo "--- [5/5] Cấu hình openwebui-client ---"
openwebui_uuid=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get clients \
    -r school --query clientId=openwebui-client --fields id \
    | grep '"id"' | cut -d'"' -f4 | head -1 || echo "")

if [ -n "${openwebui_uuid}" ]; then
    echo "Xóa openwebui-client cũ..."
    docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh delete "clients/${openwebui_uuid}" -r school
fi

echo "Tạo mới openwebui-client..."
openwebui_create_out=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh create clients -r school \
    -s clientId=openwebui-client \
    -s name="Open WebUI" \
    -s enabled=true \
    -s protocol=openid-connect \
    -s publicClient=false \
    -s "secret=${OPENWEBUI_OIDC_CLIENT_SECRET}" \
    -s "redirectUris=[\"${OPENWEBUI_URL}/*\",\"http://localhost:3000/*\",\"http://openwebui.school.local:3000/*\"]" \
    -s "webOrigins=[\"${OPENWEBUI_URL}\",\"http://localhost:3000\",\"http://openwebui.school.local:3000\"]" \
    -s 'attributes."post.logout.redirect.uris"="+"' 2>&1)
echo "${openwebui_create_out}"
openwebui_uuid=$(echo "${openwebui_create_out}" | grep -oP "(?<=with id ')([^']+)" | head -1)
if [ -z "${openwebui_uuid}" ]; then
    openwebui_uuid=$(docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get clients \
        -r school --query clientId=openwebui-client --fields id \
        | grep '"id"' | cut -d'"' -f4 | head -1)
fi

add_protocol_mappers "${openwebui_uuid}" "openwebui-client"
echo "✓ openwebui-client OK"

# =============================================================
# 8. GitLab Identity Provider (Keycloak broker)
# =============================================================
echo ""
echo "--- GitLab Identity Provider (kc_idp_hint=gitlab) ---"

GITLAB_OAUTH_APP_ID="${GITLAB_OAUTH_APP_ID:-}"
GITLAB_OAUTH_APP_SECRET="${GITLAB_OAUTH_APP_SECRET:-}"
KC_HOSTNAME_URL="${KC_HOSTNAME_URL:-http://keycloak.school.local:18090}"

if [ -z "${GITLAB_OAUTH_APP_ID}" ] || [ -z "${GITLAB_OAUTH_APP_SECRET}" ]; then
    echo "⚠️ Bỏ qua GitLab IdP — GITLAB_OAUTH_APP_ID hoặc GITLAB_OAUTH_APP_SECRET chưa được đặt."
    echo ""
    echo "   Hướng dẫn tạo GitLab Application:"
    echo "   1. Vào ${GITLAB_URL}/admin/applications (hoặc User Settings → Applications)"
    echo "   2. Tên: Keycloak-SSO"
    echo "   3. Redirect URI: ${KC_HOSTNAME_URL}/realms/school/broker/gitlab/endpoint"
    echo "   4. Scopes: api, read_user, openid, profile, email"
    echo "   5. Lưu Application ID và Secret vào sso-stack/.env:"
    echo "      GITLAB_OAUTH_APP_ID=<id>"
    echo "      GITLAB_OAUTH_APP_SECRET=<secret>"
    echo "   6. Chạy lại script này."
else
    # Xóa GitLab IdP cũ nếu tồn tại
    if docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get \
        identity-provider/instances/gitlab -r school >/dev/null 2>&1; then
        echo "Xóa GitLab IdP cũ..."
        docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh delete \
            identity-provider/instances/gitlab -r school
    fi

    echo "Tạo GitLab OIDC Identity Provider (providerId=oidc)..."
    docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh create \
        identity-provider/instances -r school \
        -s alias=gitlab \
        -s providerId=oidc \
        -s enabled=true \
        -s trustEmail=true \
        -s storeToken=false \
        -s addReadTokenRoleOnCreate=false \
        -s authenticateByDefault=false \
        -s firstBrokerLoginFlowAlias="first broker login" \
        -s "config.clientId=${GITLAB_OAUTH_APP_ID}" \
        -s "config.clientSecret=${GITLAB_OAUTH_APP_SECRET}" \
        -s "config.authorizationUrl=${GITLAB_URL}/oauth/authorize" \
        -s "config.tokenUrl=${GITLAB_URL}/oauth/token" \
        -s "config.userInfoUrl=${GITLAB_URL}/oauth/userinfo" \
        -s "config.jwksUrl=${GITLAB_URL}/oauth/discovery/keys" \
        -s 'config.defaultScope=openid profile email read_user api' \
        -s 'config.syncMode=INHERIT' \
        -s 'config.validateSignature=false' \
        -s 'config.useJwksUrl=true' \
        -s 'config.backchannelSupported=false'

    # Mapper: GitLab nickname (= username) -> Keycloak user attribute 'username'
    echo "Thêm username mapper cho GitLab IdP..."
    docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh create \
        identity-provider/instances/gitlab/mappers -r school \
        -s name="gitlab-username" \
        -s identityProviderMapper=oidc-user-attribute-idp-mapper \
        -s 'config.syncMode=INHERIT' \
        -s 'config.attribute=username' \
        -s 'config.jsonField=nickname'

    echo "✓ GitLab Identity Provider đã được cấu hình"
    echo "  Người dùng vào JupyterHub → nút 'Đăng nhập với GitLab' → Keycloak tự redirect tới GitLab OAuth"
    echo "  trustEmail=true: Keycloak tự ghép tài khoản GitLab với LDAP nếu email trùng"
fi

# =============================================================
# 9. Trigger LDAP Full Sync
# =============================================================
echo ""
echo "--- Trigger LDAP Full Sync ---"
ADMIN_TOKEN=$(get_admin_token)
if [ -n "${ADMIN_TOKEN}" ]; then
    sync_result=$(docker exec sso-keycloak curl -sf -X POST \
        "http://localhost:8080/admin/realms/school/user-storage/${ldap_uuid}/sync?action=triggerFullSync" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" 2>&1 || echo "")
    echo "Kết quả sync: ${sync_result}"
    echo "✓ LDAP sync đã được kích hoạt"
else
    echo "⚠️ Không thể lấy admin token để trigger sync (có thể chạy lại script sau)"
fi

# =============================================================
# 9. Debug output
# =============================================================
echo ""
echo "Ghi debug ra file client_debug.json..."
docker exec sso-keycloak /opt/keycloak/bin/kcadm.sh get clients \
    -r school --fields clientId,id,enabled 2>/dev/null > client_debug.json || true

echo ""
echo "========================================================================="
echo "✓ Keycloak đã được cấu hình đầy đủ!"
echo ""
echo "  LDAP Federation:"
echo "    - Provider: openldap → ldap://openldap:389"
echo "    - User DN:  ou=users,${LDAP_BASE_DN}"
echo "    - Mappers:  uid→username, mail→email, cn→firstName, sn→lastName"
echo ""
echo "  Clients (với protocol mappers preferred_username/email/given_name/family_name):"
echo "    - moodle-client    → ${MOODLE_WWWROOT}"
echo "    - gitlab-client    → ${GITLAB_URL}"
echo "    - jupyterhub-client → ${JUPYTERHUB_URL}"
echo "    - openwebui-client → ${OPENWEBUI_URL}"
echo "========================================================================="
