"""gunicorn WSGI server configuration."""

# Localhost port to communicate between Nginx and Gunicorn
bind = '0.0.0.0:7000'

# Maximum response time above which Gunicorn sends a timeout error
timeout = 1800

# Number of requests before workers automatically restart
# This helps limit damage caused by memory leaks
max_requests = 100

# Process only one request at a time to avoid stealing Globus App SDK client session
# This is temporary, once we use user's credentials and share compute endpoints, we can scale up
worker_class = "sync"
workers = 4
threads = 1

# Access and error logs
accesslog = "/var/log/inference-service/backend_gateway.access.log"
errorlog = "/var/log/inference-service/backend_gateway.error.log"

# Whether to send Django output to the error log
capture_output = True

# How verbose the Gunicorn error logs should be
loglevel = "debug"
enable_stdio_inheritance = True
