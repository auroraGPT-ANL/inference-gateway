import os

from inference_gateway.log_config import LOGGING

"""Inference Gateway Gunicorn ASGI server configuration."""

# Determine if we're in production or development
environment = os.getenv("ENV", "production")

# Localhost port to communicate between Nginx and Gunicorn
bind = "0.0.0.0:7000"

# Maximum response time above which Gunicorn sends a timeout error
timeout = 60

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
workers = 5
threads = 1
worker_connections = 1000  # Maximum number of simultaneous clients per worker

# Worker lifecycle settings
preload_app = False  # Do not preload so that you can keep main process when reloading
daemon = False  # Run in foreground (managed by systemd)

# Django LOGGING config handles file routing
# Do NOT need duplicate file-based logging here!
if environment == "development":
    accesslog = "-"
    errorlog = "-"
    loglevel = "info"
    logconfig_dict = LOGGING
    bind = "127.0.0.1:8000"
else:
    accesslog = "-"
    errorlog = "-"
    loglevel = "info"
    logconfig_dict = LOGGING

# Whether to send Django output to the error log
capture_output = True

# Enable stdio inheritance for proper logging
enable_stdio_inheritance = True

# StatsD metrics (if you have StatsD configured)
# statsd_host = 'localhost:8125'
# statsd_prefix = 'gunicorn'

# Process naming for better monitoring
proc_name = "inference-gateway"

# Error handling
max_retries = 3
