FROM ubuntu:22.04

# Tránh các dấu nhắc tương tác khi cài đặt gói
ENV DEBIAN_FRONTEND=noninteractive

# Cài đặt slapd, ldap-utils và gettext-base (để có envsubst)
RUN apt-get update && apt-get install -y \
    slapd \
    ldap-utils \
    gettext-base \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Tạo các thư mục cần thiết cho slapd và xóa DB mặc định tự sinh lúc cài đặt package
RUN mkdir -p /var/run/slapd /etc/ldap/ldif /var/lib/ldap && \
    rm -rf /var/lib/ldap/*

# Copy các file cấu hình và entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY slapd.conf.template /etc/ldap/slapd.conf.template
COPY ldif/ /etc/ldap/ldif/

EXPOSE 389

ENTRYPOINT ["/entrypoint.sh"]
