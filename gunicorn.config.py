"""gunicorn WSGI server configuration."""

bind = '0.0.0.0:7000'
max_requests = 100
workers = 4
timeout = 1800

# Access log - records incoming requests
accesslog = "/var/log/inference-service/backend_gateway.access.log"

# Error log - records Gunicorn server goings-on
errorlog = "/var/log/inference-service/backend_gateway.error.log"

# Whether to send Django output to the error log
capture_output = True

# How verbose the Gunicorn error logs should be
loglevel = "debug"
enable_stdio_inheritance = True
