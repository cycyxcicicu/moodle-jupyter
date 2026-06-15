FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Ho_Chi_Minh

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    git \
    unzip \
    build-essential \
    python3.10 \
    python3-pip \
    python3-dev \
    openjdk-17-jdk \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get update && apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Tạo người dùng jovyan (UID 1000) để chạy singleuser server
RUN useradd -m -s /bin/bash -u 1000 jovyan

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --upgrade pip==24.0 && \
    pip3 install --no-cache-dir -r /tmp/requirements.txt

# Cài đặt IJava Kernel
RUN wget https://github.com/SpencerPark/IJava/releases/download/v1.3.0/ijava-1.3.0.zip && \
    unzip ijava-1.3.0.zip -d ijava-kernel && \
    cd ijava-kernel && \
    python3 install.py --sys-prefix && \
    cd .. && \
    rm -rf ijava-kernel ijava-1.3.0.zip

# Tạo thư mục làm việc mặc định, các thư mục cho nbgrader và phân quyền sở hữu cho jovyan
RUN mkdir -p /etc/jupyter && \
    mkdir -p /home/jovyan/work && \
    mkdir -p /srv/nbgrader/exchange && \
    chmod 777 /srv/nbgrader/exchange && \
    chown -R jovyan:jovyan /home/jovyan /srv/nbgrader

COPY jupyter_server_config.py /etc/jupyter/jupyter_server_config.py
COPY nbgrader_config.py /etc/jupyter/nbgrader_config.py
COPY create_nbgrader_course.py /usr/local/bin/create_nbgrader_course.py
RUN chmod +x /usr/local/bin/create_nbgrader_course.py

# Kích hoạt nbgrader extension cho JupyterLab (mặc định đã được tự kích hoạt, chạy lệnh này để đảm bảo)
RUN jupyter labextension disable @jupyter/assignment-list-extension || true

USER jovyan
WORKDIR /home/jovyan

EXPOSE 8888

CMD ["sh", "-c", "mkdir -p /home/jovyan/work; if mount | grep -q /srv/nbgrader/courses; then for dir in courses templates; do if [ -d /srv/nbgrader/$dir ] && [ ! -e /home/jovyan/work/$dir ]; then ln -s /srv/nbgrader/$dir /home/jovyan/work/$dir; fi; done; fi; exec jupyterhub-singleuser"]

