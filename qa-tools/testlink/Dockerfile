
FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Ho_Chi_Minh

# Install Apache + PHP 7.4 and required extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        apache2 \
        php7.4 \
        libapache2-mod-php7.4 \
        php7.4-pgsql \
        php7.4-mbstring \
        php7.4-gd \
        php7.4-xml \
        php7.4-curl \
        php7.4-zip \
        php7.4-json \
        curl \
        ca-certificates \
        tar \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Enable Apache modules
RUN a2enmod rewrite

# Download and install TestLink 1.9.20
WORKDIR /tmp
RUN curl -L -o testlink.tar.gz https://downloads.sourceforge.net/project/testlink/TestLink%201.9/TestLink%201.9.20/testlink-1.9.20.tar.gz \
    && tar -xzf testlink.tar.gz \
    && rm -rf /var/www/html/* \
    && mv testlink-1.9.20/* /var/www/html/ \
    && rm -rf testlink.tar.gz testlink-1.9.20

# Patch PostgreSQL schema bug (duplicate index name udx1 & unsigned keyword) and ADODB driver compatibility
RUN php -r ' \
    $files = [ \
        "/var/www/html/install/sql/postgres/testlink_create_tables.sql", \
        "/var/www/html/install/sql/alter_tables/1.9.20/postgres/DB.1.9.20/step1/db_schema_update.sql" \
    ]; \
    foreach ($files as $f) { \
        if (file_exists($f)) { \
            $c = file_get_contents($f); \
            $c = preg_replace("/udx1(\\s+ON\\s+\\/\\*prefix\\*\\/baseline_l1l2_details)/", "udx2$1", $c); \
            $c = str_replace("INT unsigned", "INT", $c); \
            file_put_contents($f, $c); \
        } \
    } \
    $adodb_files = [ \
        "/var/www/html/vendor/adodb/adodb-php/drivers/adodb-postgres64.inc.php", \
        "/var/www/html/vendor/adodb/adodb-php/drivers/adodb-pdo_pgsql.inc.php" \
    ]; \
    foreach ($adodb_files as $f) { \
        if (file_exists($f)) { \
            $c = file_get_contents($f); \
            $c = str_replace("d.adsrc as def", "pg_get_expr(d.adbin, d.adrelid) as def", $c); \
            file_put_contents($f, $c); \
        } \
    } \
    $install_new_db = "/var/www/html/install/installNewDB.php"; \
    if (file_exists($install_new_db)) { \
        $c = file_get_contents($install_new_db); \
        $c = str_replace( \
            "\$sql=\"DROP VIEW \$target\";", \
            "\$sql = (\$dbType == '\''postgres'\'') ? \"DROP VIEW IF EXISTS \$target\" : \"DROP VIEW \$target\";", \
            $c \
        ); \
        $c = str_replace( \
            "\$sql=\"DROP TABLE \$targetTable\";", \
            "\$sql = (\$dbType == '\''postgres'\'') ? \"DROP TABLE IF EXISTS \$targetTable\" : \"DROP TABLE \$targetTable\";", \
            $c \
        ); \
        file_put_contents($install_new_db, $c); \
    } \
'


# Create required directory structure for TestLink
RUN mkdir -p /var/www/html/gui/templates_c \
    && mkdir -p /var/testlink/logs \
    && mkdir -p /var/testlink/upload_area

# Copy configurations
COPY apache.conf /etc/apache2/sites-available/000-default.conf
COPY php.ini /etc/php/7.4/apache2/php.ini

# Set permissions
RUN chown -R www-data:www-data /var/www/html /var/testlink \
    && chmod -R 775 /var/www/html /var/testlink \
    && chmod -R 775 /var/www/html/gui/templates_c /var/testlink/upload_area /var/testlink/logs

WORKDIR /var/www/html

EXPOSE 80

CMD ["apache2ctl", "-D", "FOREGROUND"]

