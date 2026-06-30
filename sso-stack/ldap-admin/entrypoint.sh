#!/bin/bash
set -euo pipefail

# Map environment variables
export LDAP_HOST="${LDAP_HOST:-openldap}"
export LDAP_PORT="${LDAP_PORT:-389}"
export LDAP_BASE_DN="${LDAP_BASE_DN:-dc=school,dc=local}"
export LDAP_ADMIN_DN="${LDAP_ADMIN_DN:-cn=admin,dc=school,dc=local}"

echo "Generating phpLDAPadmin configuration..."
# Tạo thư mục config nếu chưa tồn tại
mkdir -p /usr/share/phpldapadmin/config
envsubst '$LDAP_HOST,$LDAP_PORT,$LDAP_BASE_DN,$LDAP_ADMIN_DN' < /usr/share/phpldapadmin/config.php.template > /usr/share/phpldapadmin/config/config.php
chown www-data:www-data /usr/share/phpldapadmin/config/config.php

echo "Starting Apache Web Server..."
exec apache2ctl -DFOREGROUND
