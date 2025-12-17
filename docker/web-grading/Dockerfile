# Web Grading Interface - Docker Image
# Base Python 3.12 slim image for smaller size
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libzbar0 \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy Autograder package (includes vendorized lms_interface)
COPY Autograder /app/Autograder

# Copy WebUI source
COPY WebUI /app/web_grading

# Copy pyproject.toml, LICENSE, and README
COPY pyproject.toml /app/
COPY LICENSE /app/
COPY README.md /app/

# Install uv for fast package management
RUN pip install --no-cache-dir uv

# Install dependencies (includes QuizGenerator>=0.4.0 from PyPI)
RUN uv pip install --system -e /app

# Create data directory for database
RUN mkdir -p /data

# Set working directory to web_grading
WORKDIR /app/web_grading

# Expose port 8765
EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8765/api/health', timeout=2)" || exit 1

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV GRADING_DB_PATH=/data/grading.db
ENV PYTHONPATH=/app/web_grading

# Run the application
CMD ["uvicorn", "web_api.main:app", "--host", "0.0.0.0", "--port", "8765"]
