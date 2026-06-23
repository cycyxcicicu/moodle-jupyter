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
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get update && apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Tạo người dùng jovyan (UID 1000) để chạy singleuser server
RUN useradd -m -s /bin/bash -u 1000 jovyan

RUN pip3 install --default-timeout=1000 --no-cache-dir --upgrade pip==24.0 && \
    pip3 install --default-timeout=1000 --no-cache-dir \
    jupyterhub==5.4.6 \
    jupyterlab==4.2.1 \
    jupyterlab-git==0.50.1 \
    jupyter-app-launcher==0.3.2 \
    pandas==2.2.2 \
    numpy==1.26.4 \
    matplotlib==3.8.4 \
    sqlalchemy==2.0.30 \
    psycopg2-binary==2.9.9

# Copy các file script hỗ trợ auto-clone của Phase 2B và Launcher config
COPY gitlab-auto-clone.py /usr/local/bin/gitlab-auto-clone.py
COPY start-singleuser.sh /usr/local/bin/start-singleuser.sh
RUN chmod +x /usr/local/bin/gitlab-auto-clone.py /usr/local/bin/start-singleuser.sh

RUN mkdir -p /usr/local/share/jupyter/jupyter_app_launcher
COPY jp_app_launcher_sync.yaml /usr/local/share/jupyter/jupyter_app_launcher/jp_app_launcher_sync.yaml

RUN mkdir -p /home/jovyan/work && \
    chown -R jovyan:jovyan /home/jovyan

USER jovyan
WORKDIR /home/jovyan

EXPOSE 8888

CMD ["/usr/local/bin/start-singleuser.sh"]
