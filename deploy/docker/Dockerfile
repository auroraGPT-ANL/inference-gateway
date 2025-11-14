# Use Python 3.12 as base image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DJANGO_SETTINGS_MODULE=inference_gateway.settings

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create necessary directories
RUN mkdir -p /app/staticfiles

# Expose port
EXPOSE 7000

# Run the application with Uvicorn worker
CMD ["gunicorn", \
     "inference_gateway.asgi:application", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-b", "0.0.0.0:7000", \
     "--workers", "5", \
     "--threads", "4", \
     "--timeout", "1800", \
     "--log-level", "debug", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--capture-output"] 
