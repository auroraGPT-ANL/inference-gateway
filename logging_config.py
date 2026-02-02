import os

# Determine logging behaviour for different environments
environment = os.getenv("ENV", "production")
log_to_stdout = os.getenv("LOG_TO_STDOUT", "false").lower() in ("true", "1", "t")

handlers = {}
logger_handlers = {
    "uvicorn.error": None,
    "uvicorn.access": None,
    "resource_server_async": None,
    "utils": None,
}

if log_to_stdout:
    handlers["default"] = {
        "formatter": "default",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stderr",
    }
    handlers["access"] = {
        "formatter": "access",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stdout",
    }

    logger_handlers["uvicorn.error"] = ["default"]
    logger_handlers["uvicorn.access"] = ["access"]
    logger_handlers["resource_server_async"] = ["default"]
    logger_handlers["utils"] = ["default"]
    root_handlers = ["default"]
else:
    if environment == "development":
        accesslog = "./logs/backend_gateway.access.log"
        errorlog = "./logs/backend_gateway.error.log"
    else:
        accesslog = "/var/log/inference-service/backend_gateway.access.log"
        errorlog = "/var/log/inference-service/backend_gateway.error.log"

    handlers["default"] = {
        "formatter": "default",
        "class": "logging.FileHandler",
        "filename": errorlog,
    }
    handlers["access"] = {
        "formatter": "access",
        "class": "logging.FileHandler",
        "filename": accesslog,
    }
    handlers["console"] = {
        "formatter": "default",
        "class": "logging.StreamHandler",
    }

    logger_handlers["uvicorn.error"] = ["default"]
    logger_handlers["uvicorn.access"] = ["access"]
    logger_handlers["resource_server_async"] = ["default", "console"]
    logger_handlers["utils"] = ["default", "console"]
    root_handlers = ["default", "console"]


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
    "handlers": handlers,
    "loggers": {
        "uvicorn.error": {
            "handlers": logger_handlers["uvicorn.error"],
            "level": "INFO",
        },
        "uvicorn.access": {
            "handlers": logger_handlers["uvicorn.access"],
            "level": "INFO",
            "propagate": False,
        },
        "resource_server_async": {
            "handlers": logger_handlers["resource_server_async"],
            "level": "INFO",
            "propagate": False,
        },
        "utils": {
            "handlers": logger_handlers["utils"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "level": "INFO",
        "handlers": root_handlers,
    },
}
