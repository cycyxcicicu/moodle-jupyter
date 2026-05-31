FROM ubuntu:22.04

LABEL description="Custom Moodle image using Ubuntu, Apache, PHP 8.3, and PostgreSQL"

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Ho_Chi_Minh

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    unzip \
    gnupg2 \
    software-properties-common \
    apt-transport-https \
    lsb-release \
    && add-apt-repository ppa:ondrej/php -y \
    && apt-get update && apt-get install -y --no-install-recommends \
    apache2 \
    libapache2-mod-php8.3 \
    php8.3 \
    php8.3-cli \
    php8.3-common \
    php8.3-pgsql \
    php8.3-xml \
    php8.3-curl \
    php8.3-gd \
    php8.3-intl \
    php8.3-mbstring \
    php8.3-zip \
    php8.3-soap \
    php8.3-opcache \
    postgresql-client \
    && a2enmod headers rewrite \
    && a2dismod mpm_event \
    && a2enmod mpm_prefork \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL --retry 3 --retry-all-errors --connect-timeout 10 \
        https://download.moodle.org/download.php/direct/stable405/moodle-latest-405.tgz \
        -o /tmp/moodle.tgz \
    && mkdir -p /var/www/html \
    && tar -xzf /tmp/moodle.tgz -C /var/www/html --strip-components=1 \
    && rm -f /var/www/html/index.html \
    && rm /tmp/moodle.tgz

COPY php.ini /etc/php/8.3/apache2/conf.d/99-moodle.ini
COPY php.ini /etc/php/8.3/cli/conf.d/99-moodle.ini
COPY config/config.php.template /var/www/html/config.php
COPY config/setup_jupyter_lti.php /usr/local/share/moodle/setup_jupyter_lti.php
COPY docker-entrypoint.sh /usr/local/bin/moodle-entrypoint.sh

RUN chmod +x /usr/local/bin/moodle-entrypoint.sh

RUN mkdir -p /var/moodledata \
    && chown -R www-data:www-data /var/www/html /var/moodledata \
    && chmod -R 755 /var/www/html \
    && chmod -R 775 /var/moodledata

EXPOSE 80

ENTRYPOINT ["/usr/local/bin/moodle-entrypoint.sh"]
