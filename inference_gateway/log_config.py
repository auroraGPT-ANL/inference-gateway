import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

_LOG_TO_STDOUT = os.getenv("LOG_TO_STDOUT", "false").lower() in ("true", "1", "t")
_LOG_ENV = os.getenv("ENV", "production")
_LOG_DIR = "./logs" if _LOG_ENV == "development" else "/var/log/inference-service"

if not Path(_LOG_DIR).is_dir():
    _LOG_DIR = "./logs"
    Path(_LOG_DIR).mkdir(exist_ok=True)


def _make_file_handler(
    filename: str, formatter: str = "default"
) -> dict[str, str | int]:
    return {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "filename": os.path.join(_LOG_DIR, filename),
        "when": "midnight",
        "interval": 1,
        "utc": True,
        "backupCount": 0,
        "formatter": formatter,
        "encoding": "utf-8",
    }


def _json_default(obj: Any) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _serializer(obj: Any, **kws: Any) -> str:
    try:
        return json.dumps(obj, **kws)
    except:
        if "default" in kws:
            kws["default"] = _json_default
            return json.dumps(obj, **kws)
        raise


_STRUCTURED_TABLES = [
    "access_log",
    "request_log",
    "batch_log",
    "request_metrics",
    "batch_metrics",
]

if _LOG_TO_STDOUT:
    _log_handlers: dict[str, Any] = {
        "error_stream": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "default",
        },
        "structured_stream": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
        },
    }
    _uvicorn_error_handlers = ["error_stream"]
    _uvicorn_access_handlers = ["error_stream"]
    _app_handlers = ["error_stream"]
    _root_handlers = ["error_stream"]
    _structured_handlers = {t: ["structured_stream"] for t in _STRUCTURED_TABLES}
else:
    _log_handlers = {
        "error_file": _make_file_handler("error.log"),
        "access_file": _make_file_handler("access.log", formatter="access"),
        "app_file": _make_file_handler("app.log"),
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    }
    for t in _STRUCTURED_TABLES:
        _log_handlers[f"{t}_jsonl"] = _make_file_handler(f"{t}.jsonl", formatter="json")
    _uvicorn_error_handlers = ["error_file"]
    _uvicorn_access_handlers = ["access_file"]
    _app_handlers = ["app_file"]
    _root_handlers = ["app_file", "console"]
    _structured_handlers = {t: [f"{t}_jsonl"] for t in _STRUCTURED_TABLES}

_structured_loggers = {
    f"resource_server_async.structured.{t}": {
        "handlers": h,
        "level": "INFO",
        "propagate": False,
    }
    for t, h in _structured_handlers.items()
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(asctime)s.%(msecs)03d | %(levelname)-8s | pid=%(process)d | %(name)s:%(lineno)d | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "use_colors": False,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(asctime)s.%(msecs)03d | %(levelname)-8s | pid=%(process)d | %(client_addr)s | "%(request_line)s" %(status_code)s',
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "use_colors": False,
        },
        "json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processors": [
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(
                    serializer=_serializer,
                ),
            ],
            "foreign_pre_chain": [
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
            ],
        },
    },
    "handlers": _log_handlers,
    "loggers": {
        "uvicorn.error": {
            "handlers": _uvicorn_error_handlers,
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": _uvicorn_access_handlers,
            "level": "INFO",
            "propagate": False,
        },
        "gunicorn.error": {
            "handlers": _uvicorn_error_handlers,
            "level": "INFO",
            "propagate": False,
        },
        "gunicorn.access": {
            "handlers": _uvicorn_access_handlers,
            "level": "INFO",
            "propagate": False,
        },
        "resource_server_async": {
            "handlers": _app_handlers,
            "level": "INFO",
            "propagate": False,
        },
        **_structured_loggers,
    },
    "root": {
        # Third-party logs fall through to root and get logged WARNING level
        # Turn this up if you want debug info from e.g. Globus Compute
        "level": "WARNING",
        "handlers": _root_handlers,
    },
}

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
