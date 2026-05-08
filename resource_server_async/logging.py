import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from inspect import iscoroutinefunction
from logging import getLogger

from asgiref.sync import markcoroutinefunction
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse

from resource_server_async.schemas.db_models import (
    AccessLogPydantic,
    RequestLogPydantic,
    UserPydantic,
)

from .cache import should_throttle

logger = getLogger(__name__)


@dataclass
class RequestContext:
    access_log: AccessLogPydantic
    user: UserPydantic | None = None
    request_log: RequestLogPydantic | None = None


__request_context: ContextVar[RequestContext] = ContextVar("__request_context")


def get_request_context() -> RequestContext:
    """
    Return the RequestContext value set for the current http request.

    Raises LookupError if called outside of a request span wrapped by the
    AccessLogMiddleware.
    """
    return __request_context.get()


def initialize_access_log(request: HttpRequest) -> AccessLogPydantic:
    """Return initial state of an AccessLogPydantic entry"""

    # Extract the origin IP address
    origin_ip = request.META.get("HTTP_X_FORWARDED_FOR")
    if origin_ip is None:
        origin_ip = request.META.get("REMOTE_ADDR")

    # Remove duplicate if any
    if origin_ip:
        ip_list = [ip.strip() for ip in origin_ip.split(",")]
        origin_ip = ", ".join(set(ip_list))

    return AccessLogPydantic(
        id=str(uuid.uuid4()),
        timestamp_request=datetime.now(timezone.utc),
        api_route=request.path_info,
        origin_ip=origin_ip,
    )


async def write_logs(
    context: RequestContext, response: HttpResponse | StreamingHttpResponse
) -> None:
    context.access_log.emit(context.user, response)

    if context.request_log:
        body = (
            "streaming_response_in_progress"
            if isinstance(response, StreamingHttpResponse)
            else response.content.decode(errors="ignore")
        )
        context.request_log.emit(body, response.status_code)

        if not isinstance(response, StreamingHttpResponse):
            await context.request_log.emit_metrics()


class AccessLogMiddleware:
    sync_capable = False
    async_capable = True

    def __init__(
        self,
        get_response: Callable[
            [HttpRequest], Awaitable[HttpResponse | StreamingHttpResponse]
        ],
    ):
        self.get_response = get_response
        self._background_tasks: set[asyncio.Task[None]] = set()

        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        if exc := task.exception():
            logger.error("Background log write failed", exc_info=exc)

    async def __call__(
        self, request: HttpRequest
    ) -> HttpResponse | StreamingHttpResponse:

        token = __request_context.set(RequestContext(initialize_access_log(request)))

        try:
            response = await self.get_response(request)
            ctx_data = __request_context.get()
        finally:
            __request_context.reset(token)

        if should_skip_logging(ctx_data, request, response):
            return response

        # Fire-and-forget logging pattern:
        task = asyncio.create_task(write_logs(ctx_data, response))
        self._background_tasks.add(task)
        task.add_done_callback(self._on_done)
        return response


def should_skip_logging(
    ctx: RequestContext,
    request: HttpRequest,
    response: HttpResponse | StreamingHttpResponse,
) -> bool:
    # Don't log internal streaming requests:
    if "api/streaming" in request.path:
        return True

    status_code = response.status_code
    fingerprint = (
        "<streaming>"
        if isinstance(response, StreamingHttpResponse)
        else str(response.content[:128])
    )

    user = ctx.user.username if ctx.user else ctx.access_log.origin_ip

    # Debounce if it's the same user/error repeatedly:
    if status_code >= 400 and should_throttle(user, fingerprint, status_code):
        return True

    # Internal errors de-dup'd at user/status level:
    if status_code >= 500 and should_throttle(user, status_code):
        return True

    return False
