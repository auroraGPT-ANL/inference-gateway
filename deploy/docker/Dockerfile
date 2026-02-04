# Use Python 3.12 as base image
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

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

# Copy dependency file to avoid "Permission denied" issues
COPY pyproject.toml uv.lock ./

# Install Python dependencies (without installing the project yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-extras --dev --no-install-project

# Copy project files
COPY . .

# Sync project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-extras --dev

# Create the log directory
RUN mkdir /var/log/inference-service/

# Expose port
EXPOSE 7000

# Setup and start the application
RUN chmod +x /app/deploy/docker/entrypoint.sh
ENTRYPOINT ["/app/deploy/docker/entrypoint.sh"]
