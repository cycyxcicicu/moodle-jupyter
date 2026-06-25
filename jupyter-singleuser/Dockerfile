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
    tini \
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
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER jovyan
WORKDIR /home/jovyan

EXPOSE 8888

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["jupyterhub-singleuser"]

