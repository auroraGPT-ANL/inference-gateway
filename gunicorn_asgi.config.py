"""gunicorn WSGI server configuration."""

# Localhost port to communicate between Nginx and Gunicorn
bind = '0.0.0.0:7000'

# Maximum response time above which Gunicorn sends a timeout error
timeout = 1800

# Number of requests before workers automatically restart
# This helps limit damage caused by memory leaks (ensure workers release memory from time to time)
max_requests = 3000

# Random number (between 0 and max_requests_jitter) added to the max_requests number
# This randomize when workers are restarting and limit the risk of all workers restarting at the same time
max_requests_jitter = 300

# Maximum number of pending connections (default 2048)
# If the number of pending requests exceed this number, there will be a ConnectTimeout/MaxEntry error
backlog = 2048

# Process only one request at a time to avoid stealing Globus App SDK client session
# This is temporary, once we use user's credentials and share compute endpoints, we can scale up
worker_class = "sync"
workers = 9
threads = 1

# Access and error logs
accesslog = "/var/log/inference-service/backend_gateway.access.log"
errorlog = "/var/log/inference-service/backend_gateway.error.log"

# Whether to send Django output to the error log
capture_output = True

# How verbose the Gunicorn error logs should be
loglevel = "debug"
enable_stdio_inheritance = True
