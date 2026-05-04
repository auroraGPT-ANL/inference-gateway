import json
import logging
import logging.config
from typing import Any

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from ninja import Query

from logging_config import LOGGING_CONFIG
from resource_server_async.schemas.batch import (
    BatchLogSummary,
    BatchStatus,
    BatchSubmit,
)
from resource_server_async.streaming import (
    decode_request_body,
    set_streaming_error,
    set_streaming_metadata,
    set_streaming_status,
    store_streaming_data,
    validate_streaming_request_security,
)

from .clusters import BaseCluster
from .errors import (
    AccessDenied,
    BatchFailed,
    BatchNotFound,
    BatchOngoing,
    BatchUnavailable,
    QuotaExceeded,
    UnsupportedFramework,
)

logging.config.dictConfig(LOGGING_CONFIG)

# Local utils
from resource_server_async.endpoints import BaseEndpoint, GlobusComputeEndpoint
from resource_server_async.schemas.batch import BatchListFilter
from resource_server_async.schemas.clusters import JobInfo, JobsByStatus
from resource_server_async.schemas.db_models import (
    BatchLogPydantic,
    UserPydantic,
)
from resource_server_async.schemas.endpoints import (
    SubmitBatchResult,
    SubmitTaskAsyncResponse,
    SubmitTaskResult,
)

from .services import (
    filter_jobs_for_user,
    get_list_endpoints_data,
    prep_globus_staging_area,
    submit_openai_inference_request,
)

log = logging.getLogger(__name__)
log.info("Utils functions loaded.")

# Django database
# from resource_server.models import FederatedEndpoint
# Django Ninja API
from resource_server_async.api import AuthedRequest, api, router
from resource_server_async.models import BatchLog
from resource_server_async.schemas import (
    GlobusStagingAreaPrepared,
    ListEndpointsResponse,
    Sam3Request,
)
from resource_server_async.schemas.openai_chat_completions import (
    OpenAIChatCompletionsPydantic,
)
from resource_server_async.schemas.openai_completions import OpenAICompletionsPydantic
from resource_server_async.schemas.openai_embeddings import OpenAIEmbeddingsPydantic


# Health Check (GET) - No authentication required
# Lightweight endpoint for Kubernetes/load balancer health checks
@router.get("/health", auth=None)
async def health_check() -> dict[str, str]:
    """Lightweight health check endpoint - returns OK if API is responding."""
    return {"status": "ok"}


# Whoami (GET)
@router.get("/whoami", response=UserPydantic)
async def whoami(request: AuthedRequest) -> UserPydantic:
    """GET basic user information from access token, or error message otherwise."""
    return UserPydantic(
        id=request.auth.id,
        name=request.auth.name,
        username=request.auth.username,
        user_group_uuids=request.user_group_uuids,
        idp_id=request.auth.idp_id,
        idp_name=request.auth.idp_name,
        auth_service=request.auth.auth_service,
    )


# List Endpoints (GET)
@router.get("/list-endpoints", response=ListEndpointsResponse)
async def get_list_endpoints(request: AuthedRequest) -> ListEndpointsResponse:
    """GET request to list the available frameworks and models."""
    return await get_list_endpoints_data(request.auth, request.user_group_uuids)


@router.put("/data/staging", response=GlobusStagingAreaPrepared)
def ensure_staging_area(request: AuthedRequest) -> GlobusStagingAreaPrepared:
    """
    Idempotent user request to create a staging area for the inference service.

    A temporary directory named with the user's principal ID is created and
    read/write ACLs are granted to the user to initiate data transfers.
    """
    return prep_globus_staging_area(
        principal_id=request.auth.id,
        collection_id=settings.DATA_STAGING_GLOBUS_COLLECTION_ID,
    )


# List running and queue models (GET)
@router.get("/{cluster_name}/jobs", response=JobsByStatus)
async def get_jobs(request: AuthedRequest, cluster_name: str) -> JobsByStatus:
    """GET request to list the available frameworks and models."""

    cluster = await BaseCluster.load_adapter(cluster_name)

    # Make sure the user is authorized to see this cluster
    cluster.check_permission(request.auth, request.user_group_uuids)

    # If the cluster is under maintenance, report all jobs stopped:
    if cluster.check_maintenance().is_under_maintenance:
        all_endpoints = await get_list_endpoints_data(
            request.auth, request.user_group_uuids
        )
        cluster_info = all_endpoints.clusters.get(cluster.cluster_name)
        frameworks = cluster_info.frameworks if cluster_info else {}

        return JobsByStatus(
            stopped=[
                JobInfo(Models=model, Framework=framework, Cluster=cluster.cluster_name)
                for framework, fw_info in frameworks.items()
                for model in fw_info.models
            ]
        )
    else:
        return await filter_jobs_for_user(
            cluster, request.auth, request.user_group_uuids
        )


# Inference batch (POST)
@router.post("/{cluster_name}/{framework}/v1/batches", response=SubmitBatchResult)
async def post_batch_inference(
    request: AuthedRequest, cluster_name: str, framework: str, batch_data: BatchSubmit
) -> SubmitBatchResult:
    """POST request to send a batch to Globus Compute endpoints."""

    # Get cluster wrapper from database
    cluster = await BaseCluster.load_adapter(cluster_name)

    # Error if the cluster is under maintenance
    cluster.check_maintenance().raise_if_down()

    # Verify that the framework is enabled by the cluster
    if framework not in cluster.frameworks:
        raise UnsupportedFramework(
            f"Framework {framework!r} not available on cluster {cluster.cluster_name!r}."
        )

    endpoint = await BaseEndpoint.load_adapter(
        cluster_name, framework, batch_data.model
    )

    # Error if batch is disabled for this endpoint
    if not endpoint.has_batch_enabled():
        raise BatchUnavailable(
            f"Batch is unavailable for endpoint {endpoint.endpoint_slug}"
        )

    # Block access if the user is not allowed to use the endpoint
    endpoint.check_permission(request.auth, request.user_group_uuids)

    # Reject request if the allowed quota per user would be exceeded
    number_of_active_batches = await BatchLog.objects.filter(
        access_log__user__username=request.auth.username,
        status__in=["pending", "running"],
    ).acount()

    if number_of_active_batches >= settings.MAX_BATCHES_PER_USER:
        raise QuotaExceeded(
            f"Quota of {settings.MAX_BATCHES_PER_USER} active batch(es) per user exceeded."
        )

    # Error if an ongoing batch already exists with the same input_file for the same user
    existing_batch = (
        await BatchLog.objects.filter(
            access_log__user__username=request.auth.username,
            input_file=batch_data.input_file,
        )
        .exclude(
            status__in=[
                BatchStatus.failed.value,
                BatchStatus.completed.value,
            ],
        )
        .afirst()
    )

    if existing_batch is not None:
        raise BatchOngoing(
            f"Input file {batch_data.input_file} "
            f"already used by ongoing batch {existing_batch.id}."
        )

    # Submit batch
    batch_response = await endpoint.submit_batch(batch_data, request.auth.username)

    # Create batch log data
    request.batch_log_data = BatchLogPydantic(
        id=batch_response.batch_id,
        task_ids=batch_response.task_ids,
        cluster=cluster.cluster_name,
        framework=framework,
        model=batch_data.model,
        input_file=batch_data.input_file,
        output_folder_path=batch_data.output_folder_path,
        status=batch_response.status,
        in_progress_at=timezone.now(),
    )

    return batch_response


# List of batches (GET)
@router.get("/v1/batches", response=list[BatchLogSummary])
async def get_batch_list(
    request: AuthedRequest,
    filters: Query[BatchListFilter],
) -> list[BatchLog]:
    """GET request to list all batches linked to the authenticated user."""

    batch_list: list[BatchLog] = []

    # For each batch object owned by the user ...
    async for batch in BatchLog.objects.filter(
        access_log__user__username=request.auth.username
    ).aiterator():
        # If the batch status needs to be revised ...
        if (
            batch.status
            not in [
                BatchStatus.completed.value,
                BatchStatus.failed.value,
            ]
            and batch.task_ids
        ):
            endpoint = await BaseEndpoint.load_adapter(
                batch.cluster, batch.framework, batch.model
            )
            status_result = await endpoint.get_batch_status(batch)
            await batch.update(status_result)

        # If no optional status filter was provided ...
        # or if the status filter matches the current batch status ...
        if filters.status is None or filters.status == batch.status:
            batch_list.append(batch)

    return batch_list


# Inference batch status (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches/{batch_id}", response=str)
async def get_batch_status(request: AuthedRequest, batch_id: str) -> str:
    """GET request to query status of an existing batch job."""
    try:
        batch: BatchLog = await BatchLog.objects.select_related(
            "access_log", "access_log__user"
        ).aget(id=batch_id)
    except BatchLog.DoesNotExist:
        raise BatchNotFound(f"Batch {batch_id} does not exist")

    # Make sure user has permission to access this batch_id
    if not (
        batch.access_log.user
        and request.auth.username == batch.access_log.user.username
    ):
        raise AccessDenied(f"Permission denied to Batch {batch_id}.")

    # Return status directly if batch already completed or failed
    if (
        batch.status not in [BatchStatus.completed, BatchStatus.failed]
        and batch.task_ids
    ):
        endpoint = await BaseEndpoint.load_adapter(
            batch.cluster, batch.framework, batch.model
        )
        status_result = await endpoint.get_batch_status(batch)
        await batch.update(status_result)

    return batch.status


# Inference batch result (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches/{batch_id}/result", response=str)
async def get_batch_result(request: AuthedRequest, batch_id: str) -> str:
    """GET request to recover result from an existing batch job."""

    try:
        batch: BatchLog = await BatchLog.objects.select_related(
            "access_log", "access_log__user"
        ).aget(id=batch_id)
    except BatchLog.DoesNotExist:
        raise BatchNotFound(f"Batch {batch_id} does not exist")

    # Make sure user has permission to access this batch_id
    if not (
        batch.access_log.user
        and request.auth.username == batch.access_log.user.username
    ):
        raise AccessDenied(f"Permission denied to Batch {batch_id}.")

    # Return status directly if batch already completed or failed
    if (
        batch.status not in [BatchStatus.completed, BatchStatus.failed]
        and batch.task_ids
    ):
        endpoint = await BaseEndpoint.load_adapter(
            batch.cluster, batch.framework, batch.model
        )
        status_result = await endpoint.get_batch_status(batch)
        await batch.update(status_result)

    if batch.status == BatchStatus.failed:
        raise BatchFailed(f"Batch failed: {batch.result}", 400, request)
    elif batch.status == BatchStatus.completed:
        return batch.result
    else:
        raise BatchOngoing("Batch not completed yet. Results not ready.")


@router.post("/{cluster_name}/{framework}/v1/chat/completions")
async def create_chat_completion(
    request: AuthedRequest,
    cluster_name: str,
    framework: str,
    payload: OpenAIChatCompletionsPydantic,
) -> Any:
    return await submit_openai_inference_request(
        request, cluster_name, framework, payload
    )


@router.post("/{cluster_name}/{framework}/v1/completions")
async def create_completion(
    request: AuthedRequest,
    cluster_name: str,
    framework: str,
    payload: OpenAICompletionsPydantic,
) -> Any:
    return await submit_openai_inference_request(
        request, cluster_name, framework, payload
    )


@router.post("/{cluster_name}/{framework}/v1/embeddings")
async def create_embedding(
    request: AuthedRequest,
    cluster_name: str,
    framework: str,
    payload: OpenAIEmbeddingsPydantic,
) -> Any:
    return await submit_openai_inference_request(
        request, cluster_name, framework, payload
    )


# Inference (POST)
@router.post("/sophia/sam3service/process", response=SubmitTaskAsyncResponse)
async def sam3_infer(
    request: AuthedRequest, payload: Sam3Request
) -> SubmitTaskAsyncResponse:
    """
    Submit single-image inference request to SAM3 Globus Compute endpoint.
    """
    # Get cluster wrapper from database
    cluster = await BaseCluster.load_adapter("sophia")

    # Error if the cluster is under maintenance
    cluster.check_maintenance().raise_if_down()

    # Endpoint slug (sophia-sam3service-sam3 hardcoded for now)
    endpoint = await BaseEndpoint.load_adapter(
        cluster.cluster_name, "sam3service", "sam3"
    )
    assert isinstance(endpoint, GlobusComputeEndpoint)
    log.info(f"endpoint_slug: {endpoint.endpoint_slug} - user: {request.auth.username}")

    # Block access if the user is not allowed to use the endpoint
    endpoint.check_permission(request.auth, request.user_group_uuids)

    # Submit task
    data = payload.model_dump(exclude={"weights_dir_override"})
    config = (
        {"sam3_weights_dir": str(payload.weights_dir_override)}
        if payload.weights_dir_override
        else None
    )

    task_response = await endpoint.submit_task_async(data, endpoint_config=config)
    return task_response


@router.get("/sophia/sam3service/tasks/{task_id}", response=SubmitTaskResult)
async def sam3_get_task_result(
    request: AuthedRequest, task_id: str
) -> SubmitTaskResult:
    # Get cluster wrapper from database
    cluster = await BaseCluster.load_adapter("sophia")

    # Error if the cluster is under maintenance
    cluster.check_maintenance().raise_if_down()

    endpoint = await BaseEndpoint.load_adapter(
        cluster.cluster_name, "sam3service", "sam3"
    )
    assert isinstance(endpoint, GlobusComputeEndpoint)
    log.info(f"endpoint_slug: {endpoint.endpoint_slug} - user: {request.auth.username}")

    # Block access if the user is not allowed to use the endpoint
    endpoint.check_permission(request.auth, request.user_group_uuids)
    return await endpoint.get_task_result(task_id)


# Streaming server endpoints (integrated into Django)
@router.post("/api/streaming/data/", auth=None, throttle=[])
async def receive_streaming_data(request: HttpRequest) -> JsonResponse:
    """Receive streaming data from vLLM function - INTERNAL ONLY

    Security layers (optimized with caching):
    1. Content-Length validation (DoS prevention)
    2. Global shared secret validation
    3. Per-task token validation (cached)
    4. Data size validation
    """

    # Validate all security requirements
    is_valid, error_response, status_code = validate_streaming_request_security(
        request, max_content_length=150000
    )
    if not is_valid:
        # Try to extract task_id to record auth failure
        try:
            data = json.loads(decode_request_body(request))
            task_id = data.get("task_id")
            if task_id and status_code in [401, 403]:
                set_streaming_metadata(task_id, "auth_failure", "true", ttl=60)
                log.warning(
                    f"Authentication failure recorded for streaming task {task_id}"
                )
        except Exception:
            pass  # Don't fail the error response if we can't record the failure
        return JsonResponse(error_response, status=status_code)

    try:
        data = json.loads(decode_request_body(request))
        task_id = data.get("task_id")
        chunk_data = data.get("data")

        if chunk_data is None:
            return JsonResponse({"error": "Missing data"}, status=400)

        if "\n" in chunk_data:
            # Split batched chunks and store each one
            chunks = chunk_data.split("\n")
            for individual_chunk in chunks:
                if individual_chunk.strip():
                    store_streaming_data(task_id, individual_chunk.strip())
        else:
            store_streaming_data(task_id, chunk_data)

        set_streaming_status(task_id, "streaming")

        return JsonResponse({"status": "received"})

    except Exception as e:
        log.error(f"Error in streaming data endpoint: {e}")
        return JsonResponse({"error": "Internal server error"}, status=500)


@router.post("/api/streaming/error/", auth=None, throttle=[])
async def receive_streaming_error(request: HttpRequest) -> JsonResponse:
    """Receive error from vLLM function - INTERNAL ONLY - P0 OPTIMIZED

    Security layers (optimized with caching):
    1. Content-Length validation (DoS prevention)
    2. Global shared secret validation
    3. Per-task token validation (cached)
    """

    # Validate all security requirements
    is_valid, error_response, status_code = validate_streaming_request_security(
        request, max_content_length=15000
    )
    if not is_valid:
        # Try to extract task_id to record auth failure
        try:
            data = json.loads(decode_request_body(request))
            task_id = data.get("task_id")
            if task_id and status_code in [401, 403]:
                set_streaming_metadata(task_id, "auth_failure", "true", ttl=60)
                log.warning(
                    f"Authentication failure recorded for streaming task {task_id}"
                )
        except Exception:
            pass  # Don't fail the error response if we can't record the failure
        return JsonResponse(error_response, status=status_code)

    try:
        data = json.loads(decode_request_body(request))
        task_id = data.get("task_id")
        error = data.get("error")

        if error is None:
            return JsonResponse({"error": "Missing error"}, status=400)

        # Store error with automatic cleanup
        set_streaming_error(task_id, error)
        set_streaming_status(task_id, "error")

        log.error(f"Received error for task {task_id}: {error}")
        return JsonResponse({"status": "ok", "task_id": task_id})

    except Exception as e:
        log.error(f"Error receiving streaming error: {e}")
        return JsonResponse({"error": "Internal server error"}, status=500)


@router.post("/api/streaming/done/", auth=None, throttle=[])
async def receive_streaming_done(request: HttpRequest) -> JsonResponse:
    """Receive completion signal from vLLM function - INTERNAL ONLY - P0 OPTIMIZED

    Security layers (optimized with caching):
    1. Content-Length validation (DoS prevention)
    2. Global shared secret validation
    3. Per-task token validation (cached)
    """

    # Validate all security requirements
    is_valid, error_response, status_code = validate_streaming_request_security(
        request, max_content_length=15000
    )
    if not is_valid:
        # Try to extract task_id to record auth failure
        try:
            data = json.loads(decode_request_body(request))
            task_id = data.get("task_id")
            if task_id and status_code in [401, 403]:
                set_streaming_metadata(task_id, "auth_failure", "true", ttl=60)
                log.warning(
                    f"Authentication failure recorded for streaming task {task_id}"
                )
        except Exception:
            pass  # Don't fail the error response if we can't record the failure
        return JsonResponse(error_response, status=status_code)

    try:
        data = json.loads(decode_request_body(request))
        task_id = data.get("task_id")

        # Mark as completed with automatic cleanup
        set_streaming_status(task_id, "completed")

        log.info(f"Completed streaming task: {task_id}")
        return JsonResponse({"status": "ok", "task_id": task_id})

    except Exception as e:
        log.error(f"Error receiving streaming done: {e}")
        return JsonResponse({"error": "Internal server error"}, status=500)


# Add URLs to the Ninja API
api.add_router("/", router)
