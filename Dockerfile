# ---- Stage 1: Install Python dependencies ----
FROM python:3.11-slim AS builder

WORKDIR /build

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: Production image ----
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy pre-installed Python packages
COPY --from=builder /install /usr/local

# curl 用于健康检查。
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY backend/ /app/backend/
COPY extension/ /app/extension/
COPY run.sh /app/run.sh

# Create directories for persistent data
RUN mkdir -p /app/data

EXPOSE 8000

# Healthcheck: verify the app responds on port 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

LABEL org.opencontainers.image.title="JustSearch"
LABEL org.opencontainers.image.description="AI-powered deep search assistant (browser-bridge edition)"
LABEL org.opencontainers.image.source="https://github.com/yeahhe365/JustSearch"
LABEL org.opencontainers.image.version="2.3.0"
