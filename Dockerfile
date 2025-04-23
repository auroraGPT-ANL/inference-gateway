# Use Python 3.11 as base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV POETRY_VERSION=1.7.1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==${POETRY_VERSION}

# Install uvicorn and gunicorn
RUN pip install --no-cache-dir uvicorn gunicorn

# Copy project files
COPY . .

# Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Set DJANGO_SETTINGS_MODULE env var specifically for this RUN command
# Collect static files
RUN export DJANGO_SETTINGS_MODULE=inference_gateway.settings

# Create necessary directories
RUN mkdir -p /var/log/inference-service

# Run the application with standard Uvicorn worker and explicit parameters (Workaround)
CMD ["gunicorn", \
     "inference_gateway.asgi:application", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-b", "0.0.0.0:7000", \
     "--workers", "5", \
     "--threads", "4", \
     "--timeout", "1800", \
     "--log-level", "debug", \
     "--access-logfile", "/var/log/inference-service/backend_gateway.access.log", \
     "--error-logfile", "/var/log/inference-service/backend_gateway.error.log", \
     "--capture-output"] 
