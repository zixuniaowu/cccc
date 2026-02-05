# Stage 1: Build WebUI
FROM node:20-slim AS web-builder
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: Final image
FROM python:3.11-slim

# Install dependencies + Node.js
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git bash \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI (curl + bash, npm package deprecated)
RUN curl -fsSL https://claude.ai/install.sh | bash

# Install other CLI tools via npm
RUN npm install -g \
    @google/gemini-cli \
    @openai/codex \
    @factory/cli

# Install CCCC
WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# Copy built WebUI (vite outputs to ../src/cccc/ports/web/dist)
COPY --from=web-builder /src/cccc/ports/web/dist ./src/cccc/ports/web/dist

# Workspace for project files (mount volumes here)
RUN mkdir -p /workspace
WORKDIR /workspace

# Configuration
ENV CCCC_HOME=/data
ENV PATH="/root/.local/bin:$PATH"

# Daemon IPC - TCP mode for SDK access
# K8s sidecar: 127.0.0.1 (same Pod shares network namespace)
# Local dev:   override with -e CCCC_DAEMON_HOST=0.0.0.0 -e CCCC_DAEMON_ALLOW_REMOTE=1
ENV CCCC_DAEMON_TRANSPORT=tcp
ENV CCCC_DAEMON_HOST=127.0.0.1
ENV CCCC_DAEMON_PORT=9765

# Web server - 0.0.0.0 for external access
ENV CCCC_WEB_HOST=0.0.0.0
ENV CCCC_WEB_PORT=8765

EXPOSE 8765 9765
VOLUME ["/data", "/workspace"]

# cccc (without args) starts both daemon and web server
CMD ["cccc"]
