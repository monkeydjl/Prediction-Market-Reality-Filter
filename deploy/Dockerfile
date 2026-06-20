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

# Copy backend
COPY backend/ /app/

# Copy pre-built frontend (build separately with `npm run build` in frontend/)
COPY frontend/out/ /frontend/out/

# Expose and run
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
