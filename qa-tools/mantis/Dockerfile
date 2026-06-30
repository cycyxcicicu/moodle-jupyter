FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Ho_Chi_Minh

# Install system packages and PHP extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    apache2 \
    php \
    libapache2-mod-php \
    php-pgsql \
    php-mbstring \
    php-gd \
    php-xml \
    php-curl \
    php-zip \
    curl \
    ca-certificates \
    tar \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Enable Apache modules
RUN a2enmod rewrite

# Download and install MantisBT 2.28.3
WORKDIR /tmp
RUN curl -L -o mantisbt.tar.gz https://downloads.sourceforge.net/project/mantisbt/mantis-stable/2.28.3/mantisbt-2.28.3.tar.gz \
    && tar -xzf mantisbt.tar.gz \
    && rm -rf /var/www/html/* \
    && mv mantisbt-2.28.3/* /var/www/html/ \
    && rm -rf mantisbt.tar.gz mantisbt-2.28.3

# Copy configurations
COPY apache.conf /etc/apache2/sites-available/000-default.conf
COPY php.ini /tmp/php.ini

# Determine PHP version dynamically and copy php.ini to the correct path
RUN PHP_VER=$(php -r "echo PHP_MAJOR_VERSION.'.'.PHP_MINOR_VERSION;") && \
    cp /tmp/php.ini /etc/php/$PHP_VER/apache2/php.ini && \
    rm /tmp/php.ini

# Set permissions
RUN chown -R www-data:www-data /var/www/html && \
    chmod -R 775 /var/www/html

WORKDIR /var/www/html

EXPOSE 80

CMD ["apache2ctl", "-D", "FOREGROUND"]
