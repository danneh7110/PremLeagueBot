# Use a lightweight Python base image
FROM python:3.11-slim

# Prevent Python from buffering logs
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose Fly.io port
ENV PORT=8080

# Start Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]
