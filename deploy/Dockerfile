# Prediction Market Reality Filter — Production Dockerfile
# Build:  docker build -t pmrf:0.3.0 -f deploy/Dockerfile .
# Run:    docker run -p 8000:8000 --env-file .env pmrf:0.3.0

# ---- Stage 1: Builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for feedparser + httpx
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies to a target directory
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: Runtime ----
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
    && mkdir -p /app/logs /app/data \
    && chown -R app:app /app

# Copy backend
COPY --chown=app:app backend/ /app/

# Copy pre-built frontend (build separately with `npm run build` in frontend/)
COPY --chown=app:app frontend/out/ /frontend/out/

USER app

# Expose and run
EXPOSE 8000

# Health check (works even without docker-compose)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=5).status < 400 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
