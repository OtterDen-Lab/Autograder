# Web Grading Interface - Docker Image
# Base Python 3.12 slim image for smaller size
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy LMSInterface dependency (local path dependency)
COPY LMSInterface /app/LMSInterface

# Copy Autograder source
COPY Autograder /app
COPY Autograder/pyproject.toml /app/

# Update pyproject.toml to use local LMSInterface path
RUN sed -i 's|path = "../LMSInterface"|path = "./LMSInterface"|g' /app/pyproject.toml

# Install uv for fast package management
RUN pip install --no-cache-dir uv

# Install dependencies
RUN uv pip install --system -e /app

# Create data directory for database
RUN mkdir -p /data

# Set working directory to web_grading
WORKDIR /app/Autograder/web_grading

# Expose port 8000
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/api/health', timeout=2)" || exit 1

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV GRADING_DB_PATH=/data/grading.db
ENV PYTHONPATH=/app/Autograder/web_grading

# Run the application
CMD ["uvicorn", "web_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
