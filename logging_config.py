import os

# Define the location of the log files (same as in gunicorn_asgi.config.py)
environment = os.getenv("ENV", "production")
if environment == "development":
    accesslog = "./logs/backend_gateway.access.log"
    errorlog = "./logs/backend_gateway.error.log"
else:
    accesslog = "/var/log/inference-service/backend_gateway.access.log"
    errorlog = "/var/log/inference-service/backend_gateway.error.log"


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(asctime)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(asctime)s - %(client_addr)s - "%(request_line)s" %(status_code)s',
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.FileHandler",
            "filename": errorlog,
        },
        "access": {
            "formatter": "access",
            "class": "logging.FileHandler",
            "filename": accesslog,
        },
    },
    "loggers": {
        "uvicorn.error": {"handlers": ["default"], "level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}