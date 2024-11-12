import os

"""gunicorn ASGI server configuration."""

# Determine if we're in production or development
environment = os.getenv("ENV", "production")

# Localhost port to communicate between Nginx and Gunicorn
bind = '0.0.0.0:7000'

# Maximum response time above which Gunicorn sends a timeout error
timeout = 1800

# Number of requests before workers automatically restart
max_requests = 3000

# Randomize worker restarts
max_requests_jitter = 300

# Maximum number of pending connections
backlog = 2048

# Type of workers
worker_class = "resource_server_async.uvicorn_workers.InferenceUvicornWorker"
workers = 1
threads = 1

# Log directory based on environment
if environment == "development":
    # Development log files in the current directory
    accesslog = "./logs/backend_gateway.access.log"
    errorlog = "./logs/backend_gateway.error.log"
    bind = '127.0.0.1:8000'
else:
    # Local development log files in a local directory
    accesslog = "/var/log/inference-service/backend_gateway.access.log"
    errorlog = "/var/log/inference-service/backend_gateway.error.log"

# Whether to send Django output to the error log
capture_output = True

# How verbose the Gunicorn error logs should be
loglevel = "debug"
enable_stdio_inheritance = True