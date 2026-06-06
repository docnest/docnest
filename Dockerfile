# DocNest — FastAPI seat/membership app
FROM python:3.12-slim

# Keep Python lean and predictable in containers.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DOCNEST_DB=/data/docnest.db

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app ./app
COPY scripts ./scripts

# The SQLite database lives on a mounted volume so data survives rebuilds.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# The app seeds the DB idempotently on startup (init_db + seed).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
