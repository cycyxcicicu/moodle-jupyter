#!/bin/bash
set -euo pipefail

# Map environment variables
export LDAP_BASE_DN="${LDAP_BASE_DN:-dc=school,dc=local}"
export LDAP_ADMIN_DN="cn=admin,${LDAP_BASE_DN}"
export LDAP_ORGANIZATION="${LDAP_ORGANIZATION:-School Local}"
export LDAP_ADMIN_PASSWORD="${LDAP_ADMIN_PASSWORD:-admin123}"
export SV001_PASSWORD="${SV001_PASSWORD:-student123}"
export TEACHER01_PASSWORD="${TEACHER01_PASSWORD:-teacher123}"

# 1. Băm mật khẩu quản trị LDAP bằng slappasswd
echo "Hashing LDAP admin password..."
export LDAP_ROOTPW_HASH=$(slappasswd -s "$LDAP_ADMIN_PASSWORD")

# 2. Biên dịch file slapd.conf từ template
echo "Generating slapd.conf..."
envsubst < /etc/ldap/slapd.conf.template > /etc/ldap/slapd.conf

# Đảm bảo quyền sở hữu cho thư mục chạy slapd và dữ liệu
mkdir -p /var/run/slapd
mkdir -p /var/lib/ldap
chown -R openldap:openldap /var/run/slapd
chown -R openldap:openldap /var/lib/ldap

# 3. Chỉ thực hiện import LDIF lần đầu tiên khi thư mục dữ liệu trống (chưa có data.mdb)
if [ ! -f /var/lib/ldap/data.mdb ]; then
    echo "First time startup: initializing LDAP database..."
    
    # Tạo các file ldif thực tế thông qua envsubst
    envsubst < /etc/ldap/ldif/00-base.ldif.template > /tmp/00-base.ldif
    envsubst < /etc/ldap/ldif/01-groups.ldif.template > /tmp/01-groups.ldif
    envsubst < /etc/ldap/ldif/02-users.sample.ldif.template > /tmp/02-users.sample.ldif
    
    # Sử dụng slapadd (offline) để import dữ liệu trực tiếp lên disk
    echo "Importing base structure..."
    slapadd -f /etc/ldap/slapd.conf -l /tmp/00-base.ldif
    
    echo "Importing groups..."
    slapadd -f /etc/ldap/slapd.conf -l /tmp/01-groups.ldif
    
    echo "Importing sample users..."
    slapadd -f /etc/ldap/slapd.conf -l /tmp/02-users.sample.ldif
    
    # Phân quyền lại cho user openldap sở hữu các file database mới tạo
    chown -R openldap:openldap /var/lib/ldap
    echo "Database initialization complete."
else
    echo "Database directory is already initialized. Skipping LDIF import."
fi

# 4. Chạy OpenLDAP slapd trong foreground
echo "Starting OpenLDAP slapd..."
exec slapd -f /etc/ldap/slapd.conf -h "ldap://0.0.0.0:389/ ldapi:///" -g openldap -u openldap -d 256
