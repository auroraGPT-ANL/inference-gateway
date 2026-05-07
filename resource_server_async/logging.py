import ast
import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import getLogger
from typing import Any

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse

from inference_gateway.request_context import access_id_var, request_var
from resource_server_async.schemas.db_models import (
    AccessLogPydantic,
    RequestLogPydantic,
)

from .cache import should_throttle
from .endpoints import BaseEndpoint
from .models import BatchLog, RequestLog

logger = getLogger(__name__)
_access_slog = getLogger("resource_server_async.structured.access_log")
_request_slog = getLogger("resource_server_async.structured.request_log")
_request_metrics_slog = getLogger("resource_server_async.structured.request_metrics")


@dataclass(frozen=True, slots=True)
class UsageTokens:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    response_time_sec: float | None = None


def _parse_dict(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return ast.literal_eval(raw)


def extract_usage(request: RequestLogPydantic) -> UsageTokens:
    def _get_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
        value: dict[str, Any] | None = data.get(key)
        return value if isinstance(value, dict) else {}

    def _get_int(data: dict[str, Any], key: str) -> int | None:
        value = data.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    if (
        not request.access_log
        or request.access_log.status_code >= 300
        or not request.result
    ):
        return UsageTokens()

    try:
        data = json.loads(request.result)
        if isinstance(data, str):
            data = _parse_dict(data)
        assert isinstance(data, dict)
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


def _write_access_log(
    request: HttpRequest, response: HttpResponse | StreamingHttpResponse
) -> AccessLogPydantic | None:
    access_log: AccessLogPydantic | None = getattr(request, "access_log_data", None)

    if not access_log:
        logger.debug("Missing request.access_log_data")
        return None

    access_log.timestamp_response = datetime.now(timezone.utc)
    access_log.status_code = response.status_code

    if response.status_code >= 400:
        if isinstance(response, StreamingHttpResponse):
            access_log.error = "<streaming response error>"
        else:
            access_log.error = response.content.decode(errors="ignore")

    _access_slog.info(
        "created",
        extra={
            **access_log.model_dump(mode="json", exclude={"user"}),
            "user_id": access_log.user.id,
        },
    )
    return access_log


def _write_request_log(
    request: HttpRequest,
    response: HttpResponse | StreamingHttpResponse,
    access_log: AccessLogPydantic,
) -> RequestLogPydantic | None:
    request_log: RequestLogPydantic | None = getattr(request, "request_log_data", None)

    if not request_log:
        return None

    request_log.access_log = access_log
    if response.status_code < 300:
        if isinstance(response, StreamingHttpResponse):
            request_log.result = "streaming_response_in_progress"
        else:
            request_log.result = response.content.decode(errors="ignore")

    if request_log.timestamp_compute_response is None:
        request_log.timestamp_compute_response = datetime.now(timezone.utc)

    _request_slog.info(
        "created",
        extra={
            **request_log.model_dump(mode="json", exclude={"access_log"}),
            "access_log_id": request_log.access_log.id,
        },
    )
    return request_log


def update_request_log(
    request_id: str, result: str | None, timestamp_compute_response: datetime
) -> None:
    _request_slog.info(
        "updated",
        extra={
            "id": request_id,
            "result": result,
            "timestamp_compute_response": timestamp_compute_response,
        },
    )


def write_request_metrics(
    request: RequestLogPydantic | RequestLog, usage: UsageTokens
) -> None:
    if (
        isinstance(usage.total_tokens, (int, float))
        and isinstance(usage.response_time_sec, (int, float))
        and usage.response_time_sec > 1e-9
    ):
        throughput_tokens_per_sec = usage.total_tokens / usage.response_time_sec
    else:
        throughput_tokens_per_sec = None

    status_code = request.access_log.status_code if request.access_log else None

    defaults = {
        "cluster": request.cluster,
        "framework": request.framework,
        "model": request.model,
        "status_code": status_code,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "response_time_sec": usage.response_time_sec,
        "throughput_tokens_per_sec": throughput_tokens_per_sec,
        "timestamp_compute_request": request.timestamp_compute_request,
        "timestamp_compute_response": request.timestamp_compute_response,
    }
    _request_metrics_slog.info("upserted", extra={"request_id": request.id, **defaults})


async def write_logs(
    request: HttpRequest, response: HttpResponse | StreamingHttpResponse
) -> None:
    access_log = _write_access_log(request, response)

    if not access_log:
        logger.debug("Missing request.access_log_data")
        return

    request_log = _write_request_log(request, response, access_log)

    if request_log is not None and not isinstance(response, StreamingHttpResponse):
        usage = extract_usage(request_log)
        write_request_metrics(request_log, usage)
        endpoint = await BaseEndpoint.load_adapter(
            request_log.cluster, request_log.framework, request_log.model
        )
        endpoint.record_token_usage(access_log.user, int(usage.total_tokens or 0))

    batch_log = await BatchLog.create_from_response(request, response)
    if batch_log is not None:
        # Create BatchMetrics skeleton; later updates can fill tokens/throughput
        await batch_log.create_or_update_metrics(None, None, None, None)


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

        _aid_token = access_id_var.set(str(uuid.uuid4()))
        _r_token = request_var.set(request)
        try:
            response = await self.get_response(request)
        finally:
            access_id_var.reset(_aid_token)
            request_var.reset(_r_token)

        if "api/streaming" in request.path:
            # Don't log internal streaming requests; this is machine-to-machine
            return response

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
