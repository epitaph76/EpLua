FROM python:3.12-slim

ARG STYLUA_VERSION=2.4.1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/apps/api

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        lua5.4 \
        liblua5.4-dev \
        luarocks \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN luarocks --lua-version=5.4 install luacheck

RUN curl -fsSL -o /tmp/stylua.zip "https://github.com/JohnnyMorganz/StyLua/releases/download/v${STYLUA_VERSION}/stylua-linux-x86_64.zip" \
    && unzip /tmp/stylua.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/stylua \
    && rm -f /tmp/stylua.zip

WORKDIR /app

COPY apps/api/pyproject.toml /tmp/api-pyproject.toml
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "fastapi>=0.115,<1.0" \
        "httpx>=0.28,<1.0" \
        "uvicorn[standard]>=0.34,<1.0"

COPY . /app

RUN pip install --no-cache-dir -e /app/apps/api

WORKDIR /app/apps/api
