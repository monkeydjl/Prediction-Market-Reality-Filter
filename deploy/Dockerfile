# Prediction Market Reality Filter — Production Dockerfile
# Build:  docker build -t pmrf:0.3.0 -f deploy/Dockerfile .
# Run:    docker run -p 8000:8000 --env-file .env pmrf:0.3.0

FROM python:3.11-slim

WORKDIR /app

# System deps for feedparser + httpx
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/logs \
    && chown -R app:app /app

# Copy backend
COPY --chown=app:app backend/ /app/

# Copy pre-built frontend (build separately with `npm run build` in frontend/)
COPY --chown=app:app frontend/out/ /frontend/out/

USER app

# Expose and run
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
