from contextvars import ContextVar

access_id_var: ContextVar[str | None] = ContextVar("access_id", default=None)
