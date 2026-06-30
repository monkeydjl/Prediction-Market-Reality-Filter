# Prediction Market Reality Filter — Production Dockerfile
# Build:  docker build -t pmrf:0.3.0 -f deploy/Dockerfile .
# Run:    docker run -p 8000:8000 --env-file backend/.env pmrf:0.3.0
#
# Multi-stage: builds the Next.js frontend from source so a single
# `docker build` produces a complete image (no manual npm run build).

# ---- Stage 1: Frontend builder ----
FROM node:22-alpine AS frontend-builder

WORKDIR /frontend

# Copy package manifests first for layer caching.
COPY frontend/package.json frontend/package-lock.json* ./

RUN npm ci

# Copy frontend source and build the static export.
COPY frontend/ ./

# Next.js static export writes to ./out (per next.config.ts output: 'export').
RUN npm run build

# ---- Stage 2: Python builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for feedparser + httpx
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies to a target directory
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 3: Runtime ----
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Runtime system deps (libxml2 needed at runtime by feedparser)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/logs /app/data /frontend/out \
    && chown -R app:app /app /frontend

# Copy backend
COPY --chown=app:app backend/ /app/

# Copy frontend static export from the frontend-builder stage (no manual
# pre-build required — the build happened in Stage 1).
COPY --from=frontend-builder --chown=app:app /frontend/out/ /frontend/out/

USER app

# Expose and run
EXPOSE 8000

# Health check (works even without docker-compose)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=5).status < 400 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
