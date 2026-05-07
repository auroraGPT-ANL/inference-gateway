from contextvars import ContextVar

from django.http import HttpRequest

access_id_var: ContextVar[str | None] = ContextVar("access_id", default=None)
request_var: ContextVar[HttpRequest | None] = ContextVar("request", default=None)
