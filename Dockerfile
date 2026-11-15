FROM python:3.10-slim

WORKDIR /app

# Install system dependencies including FFmpeg for video rendering
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

EXPOSE 8000

# Use shell form so Render's dynamic $PORT variable is correctly picked up
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
