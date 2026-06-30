FROM ubuntu:22.04

# Tránh các dấu nhắc tương tác khi cài đặt gói
ENV DEBIAN_FRONTEND=noninteractive

# Cài đặt Apache, PHP và các extension cần thiết
RUN apt-get update && apt-get install -y \
    apache2 \
    php \
    php-ldap \
    php-xml \
    curl \
    gettext-base \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Tải bản release chính thức của phpLDAPadmin từ GitHub (phiên bản 1.2.6.6 hỗ trợ PHP 8)
ENV PHPLDAPADMIN_VERSION=1.2.6.6
RUN curl -sSL https://github.com/leenooks/phpLDAPadmin/archive/refs/tags/${PHPLDAPADMIN_VERSION}.tar.gz -o /tmp/phpldapadmin.tar.gz && \
    mkdir -p /usr/share/phpldapadmin && \
    tar -xzf /tmp/phpldapadmin.tar.gz -C /usr/share/phpldapadmin --strip-components=1 && \
    rm /tmp/phpldapadmin.tar.gz

# Đảm bảo quyền sở hữu cho thư mục
RUN chown -R www-data:www-data /usr/share/phpldapadmin

# Cấu hình Apache để phục vụ phpldapadmin ngay tại root URL
RUN echo 'Alias /phpldapadmin /usr/share/phpldapadmin/htdocs' > /etc/apache2/sites-available/phpldapadmin.conf && \
    echo 'Alias / /usr/share/phpldapadmin/htdocs/' >> /etc/apache2/sites-available/phpldapadmin.conf && \
    a2ensite phpldapadmin.conf && \
    echo "ServerName localhost" >> /etc/apache2/apache2.conf

# Copy tệp cấu hình và entrypoint
COPY config.php.template /usr/share/phpldapadmin/config.php.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]
