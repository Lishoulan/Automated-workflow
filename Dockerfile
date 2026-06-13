# ============================================================================
# Dockerfile — AI Content Factory
# ============================================================================
# NOTE: Docker mode is for Web UI + content generation only.
# RPA / Playwright browser automation is NOT supported in Docker.
# Use local execution for any browser-based publishing tasks.
# ============================================================================

# ---- Build stage ----
FROM python:3.10-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY requirements.txt pyproject.toml ./

# Install Python packages
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---- Runtime stage ----
FROM python:3.10-slim

WORKDIR /app

# Set timezone
ENV TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && dpkg-reconfigure --frontend noninteractive tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/output /app/logs /app/cache /app/config /app/input

# Default environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

# Default command: run once
CMD ["python", "main.py"]
