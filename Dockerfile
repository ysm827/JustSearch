# ---- Stage 1: Install Python dependencies ----
FROM python:3.11-slim AS builder

WORKDIR /build

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: Production image ----
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HEADLESS=true

WORKDIR /app

# Copy pre-installed Python packages
COPY --from=builder /install /usr/local

# Install minimal system deps for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libatspi2.0-0 libxshmfence1 curl \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright Chromium (no full deps, we handled that above)
RUN playwright install chromium

# Copy application code
COPY backend/ /app/backend/
COPY tools/ /app/tools/
COPY run.sh /app/run.sh

# Create directories for persistent data
RUN mkdir -p /app/backend/chats /app/user_data

EXPOSE 8000

# Healthcheck: verify the app responds on port 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
