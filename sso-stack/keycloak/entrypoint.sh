#!/bin/bash
set -euo pipefail

# Map environment variables for the realm config
export MOODLE_WWWROOT="${MOODLE_WWWROOT:-http://localhost:18080}"
export GITLAB_URL="${GITLAB_URL:-http://gitlab.local:8929}"
export JUPYTERHUB_URL="${JUPYTERHUB_URL:-http://localhost:18000}"

export MOODLE_OIDC_CLIENT_SECRET="${MOODLE_OIDC_CLIENT_SECRET:-moodle-secret-123}"
export GITLAB_OIDC_CLIENT_SECRET="${GITLAB_OIDC_CLIENT_SECRET:-gitlab-secret-123}"
export JUPYTERHUB_OIDC_CLIENT_SECRET="${JUPYTERHUB_OIDC_CLIENT_SECRET:-jupyterhub-secret-123}"

# LDAP variables for federation config in realm template
export LDAP_BASE_DN="${LDAP_BASE_DN:-dc=school,dc=local}"
export LDAP_ADMIN_PASSWORD="${LDAP_ADMIN_PASSWORD:-adminpwd123}"

echo "Preparing Keycloak realm configuration..."
if [ -f /opt/keycloak/data/import/school-realm.example.json ]; then
    envsubst < /opt/keycloak/data/import/school-realm.example.json > /opt/keycloak/data/import/school-realm.json
fi

# Chạy Keycloak ở chế độ optimized với HTTP được kích hoạt và import realm tự động
echo "Starting Keycloak server..."
exec /opt/keycloak/bin/kc.sh start --optimized \
    --http-port=8080 \
    --http-enabled=true \
    --hostname-strict=false \
    --import-realm
