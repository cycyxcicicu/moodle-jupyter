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
    npm install -g configurable-http-proxy typescript@5.3.3 ts-node@10.9.2 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --upgrade pip==24.0 && \
    pip3 install --no-cache-dir -r /tmp/requirements.txt

RUN wget https://github.com/SpencerPark/IJava/releases/download/v1.3.0/ijava-1.3.0.zip && \
    unzip ijava-1.3.0.zip -d ijava-kernel && \
    cd ijava-kernel && \
    python3 install.py --sys-prefix && \
    cd .. && \
    rm -rf ijava-kernel ijava-1.3.0.zip

RUN mkdir -p /etc/jupyterhub
COPY jupyterhub_config.py /etc/jupyterhub/jupyterhub_config.py

WORKDIR /etc/jupyterhub

EXPOSE 8000

CMD ["jupyterhub", "-f", "/etc/jupyterhub/jupyterhub_config.py"]
