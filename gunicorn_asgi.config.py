import os
import multiprocessing

"""gunicorn ASGI server configuration."""

# Determine if we're in production or development
environment = os.getenv("ENV", "production")

# Localhost port to communicate between Nginx and Gunicorn
bind = '0.0.0.0:7000'

# Maximum response time above which Gunicorn sends a timeout error
timeout = 1800

# Graceful timeout for worker shutdown
graceful_timeout = 30

# Keep-alive setting
keepalive = 5

# Number of requests before workers automatically restart
max_requests = 3000

# Randomize worker restarts
max_requests_jitter = 300

# Maximum number of pending connections
backlog = 2048

# Type of workers
worker_class = "resource_server_async.uvicorn_workers.InferenceUvicornWorker"

# Worker configuration
workers = multiprocessing.cpu_count() * 2 + 1  # Recommended formula: (2 x num_cores) + 1
threads = 4  # Increase threads per worker for better concurrency
worker_connections = 1000  # Maximum number of simultaneous clients per worker

# Worker lifecycle settings
preload_app = True  # Preload application code before forking workers
daemon = False  # Run in foreground (managed by systemd)

# Log directory based on environment
if environment == "development":
    # Development log files in the current directory
    accesslog = "./logs/backend_gateway.access.log"
    errorlog = "./logs/backend_gateway.error.log"
    bind = '127.0.0.1:8000'
    # More verbose logging in development
    loglevel = "debug"
else:
    # Production log files in a local directory
    accesslog = "/var/log/inference-service/backend_gateway.access.log"
    errorlog = "/var/log/inference-service/backend_gateway.error.log"
    # Less verbose logging in production
    loglevel = "debug"

# Whether to send Django output to the error log
capture_output = True

# Enable stdio inheritance for proper logging
enable_stdio_inheritance = True

# StatsD metrics (if you have StatsD configured)
# statsd_host = 'localhost:8125'
# statsd_prefix = 'gunicorn'

# Process naming for better monitoring
proc_name = 'inference-gateway'

# Error handling
max_retries = 3