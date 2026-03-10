# ============================================================
# cli-bridge Docker Image
# Python 3.12 + Node.js 22 (for iflow CLI)
# ============================================================

FROM python:3.12-slim

# Install system dependencies + Node.js 22
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    tini \
    nodejs \
    npm \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Verify Node.js
RUN node --version && npm --version

# Install iflow CLI globally
RUN npm install -g @iflow-ai/iflow-cli@latest

# Install uv (fast Python package manager)
RUN pip install --no-cache-dir uv

# Set working directory
WORKDIR /app

# Copy project metadata and source
COPY pyproject.toml README.md ./
COPY cli_bridge/ ./cli_bridge/

# Install Python package and dependencies into system environment
RUN uv pip install --system --no-cache .

# Copy Python entrypoint
COPY docker_entrypoint.py /docker_entrypoint.py

# Create necessary directories
RUN mkdir -p /root/.cli-bridge/workspace \
             /root/.cli-bridge/data/cron \
             /root/.cli-bridge/media \
             /root/.iflow

# Persistent data volumes
VOLUME ["/root/.cli-bridge", "/root/.iflow"]

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NODE_ENV=production

# Health check
HEALTHCHECK --interval=60s --timeout=15s --start-period=30s --retries=3 \
    CMD cli-bridge status || exit 1

# Use tini as PID 1 for proper signal handling
ENTRYPOINT ["tini", "--", "python", "/docker_entrypoint.py"]
CMD ["gateway", "run"]
