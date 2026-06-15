#!/bin/bash
set -euo pipefail

# Helper to run commands as root (uses sudo when not root)
run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

echo "=== Đang kiểm tra Docker & Docker Compose ==="

# 1. Kiểm tra Docker
if command -v docker >/dev/null 2>&1; then
    echo "Docker đã được cài đặt."
    docker --version
else
    echo "Không tìm thấy Docker. Tiến hành cài đặt tự động (chỉ hỗ trợ Ubuntu)..."
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [ "${ID:-}" = "ubuntu" ]; then
            run_as_root rm -f /etc/apt/sources.list.d/docker.list /etc/apt/sources.list.d/docker.sources
            run_as_root apt-get update
            run_as_root apt-get install -y ca-certificates curl gnupg lsb-release
            run_as_root install -m 0755 -d /etc/apt/keyrings
            if [ ! -f /etc/apt/keyrings/docker.asc ]; then
                run_as_root curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
                run_as_root chmod a+r /etc/apt/keyrings/docker.asc
            fi
            docker_suite="$(echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
            cat <<EOF | run_as_root tee /etc/apt/sources.list.d/docker.sources > /dev/null
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${docker_suite}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
            run_as_root apt-get update
            run_as_root apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            if command -v systemctl >/dev/null 2>&1; then
                run_as_root systemctl start docker
                run_as_root systemctl enable docker >/dev/null 2>&1 || true
            fi
            echo "Cài đặt Docker thành công."
        else
            echo "LỖI: Hệ điều hành không hỗ trợ cài đặt tự động. Vui lòng tự cài đặt Docker."
            exit 1
        fi
    else
        echo "LỖI: Không phát hiện được hệ điều hành. Vui lòng cài đặt Docker thủ công."
        exit 1
    fi
fi

# 2. Kiểm tra Docker Compose v2 plugin
if docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin đã sẵn sàng."
    docker compose version
elif command -v docker-compose >/dev/null 2>&1; then
    echo "Tìm thấy docker-compose v1."
    docker-compose version
else
    echo "Không tìm thấy Docker Compose. Đang tiến hành cài đặt plugin..."
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [ "${ID:-}" = "ubuntu" ]; then
            run_as_root apt-get update
            run_as_root apt-get install -y docker-compose-plugin
            echo "Cài đặt Docker Compose plugin thành công."
        else
            echo "LỖI: Không hỗ trợ cài đặt tự động Docker Compose. Vui lòng cài đặt thủ công."
            exit 1
        fi
    else
        echo "LỖI: Vui lòng cài đặt Docker Compose thủ công."
        exit 1
    fi
fi

# Kiểm tra Docker daemon có đang chạy không
if ! docker ps >/dev/null 2>&1; then
    echo "Cảnh báo: Docker daemon chưa chạy hoặc user hiện tại không có quyền truy cập docker socket."
    echo "Hãy chắc chắn docker service đang chạy và chạy lệnh dưới quyền sudo nếu cần."
fi
