import logging
from datetime import date, datetime, timezone
from typing import Any

from pythonjsonlogger.json import JsonFormatter

from inference_gateway.request_context import access_id_var

_STRUCTURED_PREFIX = "resource_server_async.structured."

_STREAM_MAP = {
    "uvicorn.access": "uvicorn.access",
    "uvicorn.error": "uvicorn.error",
    "uvicorn": "uvicorn.error",
    "gunicorn.error": "gunicorn.error",
    "gunicorn.access": "gunicorn.access",
}


class GatewayJsonFormatter(JsonFormatter):
    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        log_record["timestamp"] = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["pid"] = record.process
        log_record["lineno"] = record.lineno

        if record.name.startswith(_STRUCTURED_PREFIX):
            log_record["stream"] = record.name[len(_STRUCTURED_PREFIX) :]
        else:
            log_record["stream"] = _STREAM_MAP.get(record.name, "app")

        acc_id = access_id_var.get(None)
        if acc_id is not None:
            log_record["access_id"] = acc_id

    @staticmethod
    def json_default(obj: Any) -> str:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return str(obj)


class UvicornAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 5:
            record.client_addr = record.args[0]
            record.method = record.args[1]
            record.path = record.args[2]
            record.http_version = record.args[3]
            record.status_code = record.args[4]
            record.msg = ""
            record.args = None
        return True


LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "inference_gateway.log_config.GatewayJsonFormatter",
        },
    },
    "filters": {
        "uvicorn_access_fields": {
            "()": "inference_gateway.log_config.UvicornAccessFilter",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
        },
    },
    "loggers": {
        "uvicorn.error": {
            "handlers": ["stdout"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": ["stdout"],
            "level": "INFO",
            "propagate": False,
            "filters": ["uvicorn_access_fields"],
        },
        "gunicorn.error": {
            "handlers": ["stdout"],
            "level": "INFO",
            "propagate": False,
        },
        "gunicorn.access": {
            "handlers": ["stdout"],
            "level": "INFO",
            "propagate": False,
        },
        "resource_server_async": {
            "handlers": ["stdout"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "level": "WARNING",
        "handlers": ["stdout"],
    },
}
