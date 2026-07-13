FROM python:3.12-slim

WORKDIR /app

# Optional but helpful for psycopg2/pandas wheels compatibility
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer cache)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Project code will be mounted by docker-compose in dev
ENV PYTHONPATH=/app