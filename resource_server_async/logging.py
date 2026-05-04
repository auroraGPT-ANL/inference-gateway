import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from logging import getLogger
from typing import Any

from django.http import HttpRequest, HttpResponse, StreamingHttpResponse

from resource_server_async.schemas.db_models import AccessLogPydantic

from .cache import should_throttle
from .endpoints import BaseEndpoint
from .models import AccessLog, BatchLog, RequestLog

logger = getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsageTokens:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    response_time_sec: float | None = None


def extract_usage(request: RequestLog) -> UsageTokens:
    def _get_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
        value: dict[str, Any] | None = data.get(key)
        return value if isinstance(value, dict) else {}

    def _get_int(data: dict[str, Any], key: str) -> int | None:
        value = data.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    if request.access_log.status_code >= 300 or not request.result:
        return UsageTokens()

    try:
        data = json.loads(request.result)
    except Exception:
        return UsageTokens()

    usage = _get_dict(data, "usage")
    metrics = _get_dict(data, "metrics")

    if (start := request.timestamp_compute_request) and (
        end := request.timestamp_compute_response
    ):
        response_time_sec = (end - start).total_seconds()
    else:
        response_time_sec = None

    return UsageTokens(
        prompt_tokens=_get_int(usage, "prompt_tokens"),
        completion_tokens=_get_int(usage, "completion_tokens"),
        total_tokens=_get_int(usage, "total_tokens")
        or _get_int(metrics, "total_tokens"),
        response_time_sec=response_time_sec,
    )


async def write_logs(
    request: HttpRequest, response: HttpResponse | StreamingHttpResponse
) -> None:
    access_log = await AccessLog.create_from_response(request, response)

    if not access_log:
        logger.error("Missing request.access_log_data")
        return

    request_log = await RequestLog.create_from_response(request, response, access_log)

    if request_log is not None and not isinstance(response, StreamingHttpResponse):
        usage = extract_usage(request_log)
        await request_log.create_or_update_metrics(
            usage.response_time_sec,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
        )
        endpoint = await BaseEndpoint.load_adapter(
            request_log.cluster, request_log.framework, request_log.model
        )
        endpoint.record_token_usage(access_log.user, int(usage.total_tokens or 0))

    batch_log = await BatchLog.create_from_response(request, response, access_log)
    if batch_log is not None:
        # Create BatchMetrics skeleton; later updates can fill tokens/throughput
        await batch_log.create_or_update_metrics(None, None, None, None)


class AccessLogMiddleware:
    def __init__(
        self,
        get_response: Callable[
            [HttpRequest], Awaitable[HttpResponse | StreamingHttpResponse]
        ],
    ):
        self.get_response = get_response
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        if exc := task.exception():
            logger.error("Background log write failed", exc_info=exc)

    async def __call__(
        self, request: HttpRequest
    ) -> HttpResponse | StreamingHttpResponse:
        response = await self.get_response(request)

        status_code = response.status_code
        fingerprint = (
            "<streaming>"
            if isinstance(response, StreamingHttpResponse)
            else str(response.content[:128])
        )

        try:
            user: str | None = str(request.auth.username)  # type: ignore[attr-defined]
        except AttributeError:
            log: AccessLogPydantic | None = getattr(request, "access_log_data", None)
            user = log.origin_ip if log else None

        if response.status_code >= 400 and should_throttle(
            user, fingerprint, status_code
        ):
            return response

        # Internal errors will be de-dup'd at user/status level but always
        # sent to the error log:
        if response.status_code >= 500 and should_throttle(user, status_code):
            return response

        # Fire-and-forget logging pattern:
        task = asyncio.create_task(write_logs(request, response))
        self._background_tasks.add(task)
        task.add_done_callback(self._on_done)
        return response
