FROM ubuntu:22.04

# Tránh các dấu nhắc tương tác khi cài đặt gói
ENV DEBIAN_FRONTEND=noninteractive
ENV KC_HOME=/opt/keycloak

# Cài đặt Java 21, curl, tar và gettext-base
RUN apt-get update && apt-get install -y \
    curl \
    gettext-base \
    openjdk-21-jre-headless \
    tar \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Định nghĩa biến môi trường cho phiên bản Keycloak
ARG KEYCLOAK_VERSION=24.0.5
ENV KEYCLOAK_VERSION=${KEYCLOAK_VERSION}

# Tải Keycloak chính thức từ GitHub releases
RUN echo "Downloading Keycloak version ${KEYCLOAK_VERSION}..." && \
    curl -sSL https://github.com/keycloak/keycloak/releases/download/${KEYCLOAK_VERSION}/keycloak-${KEYCLOAK_VERSION}.tar.gz -o /tmp/keycloak.tar.gz && \
    mkdir -p ${KC_HOME} && \
    tar -xzf /tmp/keycloak.tar.gz -C ${KC_HOME} --strip-components=1 && \
    rm /tmp/keycloak.tar.gz

# Tạo group và user keycloak để chạy bảo mật
RUN groupadd -r keycloak && useradd -r -g keycloak -d ${KC_HOME} -s /sbin/nologin keycloak && \
    chown -R keycloak:keycloak ${KC_HOME}

WORKDIR ${KC_HOME}

# Tối ưu hóa Keycloak cho PostgreSQL và bật health check khi build
RUN ./bin/kc.sh build --db=postgres --health-enabled=true

# Copy config, entrypoint và cấp quyền
COPY --chown=keycloak:keycloak entrypoint.sh /opt/keycloak/entrypoint.sh
RUN chmod +x /opt/keycloak/entrypoint.sh

# Tạo thư mục import và sao chép realm template
RUN mkdir -p /opt/keycloak/data/import && \
    chown -R keycloak:keycloak /opt/keycloak/data
COPY --chown=keycloak:keycloak realm/school-realm.example.json /opt/keycloak/data/import/school-realm.example.json

# Đảm bảo phân quyền tối hậu cho toàn bộ thư mục keycloak
RUN chown -R keycloak:keycloak /opt/keycloak

USER keycloak

EXPOSE 8080

ENTRYPOINT ["/opt/keycloak/entrypoint.sh"]
