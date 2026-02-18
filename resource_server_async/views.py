from ninja import Query
from asgiref.sync import sync_to_async
from django.conf import settings
import uuid
import json
from django.utils import timezone
from django.utils.text import slugify
from django.http import JsonResponse, HttpResponse

# Tool to log access requests
import logging

log = logging.getLogger(__name__)

# Force Uvicorn to add timestamps in the Gunicorn access log
import logging.config
from logging_config import LOGGING_CONFIG

logging.config.dictConfig(LOGGING_CONFIG)

# Local utils
from utils.pydantic_models.db_models import (
    RequestLogPydantic,
    BatchLogPydantic,
    UserPydantic,
)
from utils.pydantic_models.batch import BatchStatusEnum, BatchListFilter
from resource_server_async.clusters.cluster import Jobs, JobInfo, BaseCluster
from resource_server_async.utils import (
    extract_prompt,
    validate_request_body,
    validate_batch_body,
    update_batch,
    decode_request_body,
    # Streaming functions
    store_streaming_data,
    set_streaming_status,
    set_streaming_error,
    set_streaming_metadata,
    validate_streaming_request_security,
    # Response functions
    get_response,
    create_access_log,
    create_request_log,
    # Wrapper function
    get_endpoint_wrapper,
    get_cluster_wrapper,
    get_list_endpoints_data,
    GetListEndpointsDataResponse,
)

log.info("Utils functions loaded.")

# Django database
# from resource_server.models import FederatedEndpoint
from resource_server_async.models import BatchLog, Cluster, Endpoint

# Django Ninja API
from resource_server_async.api import api, router

# NOTE: All caching is now centralized in resource_server_async.utils
# Caching uses Django cache (configured for Redis) with automatic fallback to in-memory cache
# - Endpoint caching: get_endpoint_from_cache(), cache_endpoint(), remove_endpoint_from_cache()
# - Streaming caching: All streaming functions use get_redis_client() for Redis-specific operations
# - Permission caching: In-memory TTLCache for performance-critical permission checks


# Health Check (GET) - No authentication required
# Lightweight endpoint for Kubernetes/load balancer health checks
@router.get("/health", auth=None)
async def health_check(request):
    """Lightweight health check endpoint - returns OK if API is responding."""
    return JsonResponse({"status": "ok"}, status=200)


# Whoami (GET)
@router.get("/whoami")
async def whoami(request):
    """GET basic user information from access token, or error message otherwise."""

    # Get user info
    try:
        user = UserPydantic(
            id=request.auth.id,
            name=request.auth.name,
            username=request.auth.username,
            user_group_uuids=request.user_group_uuids,
            idp_id=request.auth.idp_id,
            idp_name=request.auth.idp_name,
            auth_service=request.auth.auth_service,
        )
    except Exception as e:
        return await get_response(
            f"Error: could not create user from request.auth: {e}", 500, request
        )

    # Return user details
    return await get_response(user.model_dump_json(), 200, request)


# List Endpoints (GET)
@router.get("/list-endpoints")
async def get_list_endpoints(request):
    """GET request to list the available frameworks and models."""

    # Extract the list of all endpoints from the database
    response = await get_list_endpoints_data(request)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    all_endpoints = response.all_endpoints

    # Return list of frameworks and models
    return await get_response(json.dumps(all_endpoints), 200, request)


# List running and queue models (GET)
@router.get("/{cluster}/jobs")
async def get_jobs(request, cluster: str):
    """GET request to list the available frameworks and models."""

    # Get cluster wrapper from database
    response = await get_cluster_wrapper(cluster)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    cluster = response.cluster

    # Make sure the user is authorized to see this cluster
    response = cluster.check_permission(request.auth, request.user_group_uuids)
    if (response.is_authorized == False) or response.error_message:
        return await get_response(response.error_message, response.error_code, request)

    # If the cluster is under maintenance ...
    response = cluster.check_maintenance()
    if response.is_under_maintenance:
        # Create initial empty Jobs response
        jobs = Jobs()

        # Extract the list of all endpoints from the database
        response = await get_list_endpoints_data(request)
        if response.error_message:
            return await get_response(
                response.error_message, response.error_code, request
            )
        all_endpoints = response.all_endpoints

        # Try to add each model listed for this cluster in the "stopped" section
        try:
            for framework in all_endpoints["clusters"][cluster.cluster_name][
                "frameworks"
            ]:
                for model in all_endpoints["clusters"][cluster.cluster_name][
                    "frameworks"
                ][framework]["models"]:
                    jobs.stopped.append(
                        JobInfo(
                            Models=model,
                            Framework=framework,
                            Cluster=cluster.cluster_name,
                        )
                    )

        # Error if the model parsing did not work
        except Exception as e:
            return await get_response(
                f"Error: Cluster {cluster.cluster_name} under maintenance. Could not recover list of models: {e}",
                500,
                request,
            )

    # If the cluster is operational and not under maintenance ...
    else:
        # Get jobs from the targetted cluster
        response = await cluster.get_jobs(request.auth)
        if response.error_message:
            return await get_response(
                response.error_message, response.error_code, request
            )
        jobs: Jobs = response.jobs

        # For each job state listed in the jobs response ...
        for jobs_state in [
            jobs.running,
            jobs.queued,
            jobs.stopped,
            jobs.others,
            jobs.private_batch_running,
            jobs.private_batch_queued,
        ]:
            # For each block (set of models) in this state
            # -1, -1, -1 for reversed order to safely remove/edit values jobs_state
            for i_block in range(len(jobs_state) - 1, -1, -1):
                block = jobs_state[i_block]

                # Collect the list of models
                models = [m.strip() for m in block.Models.split(",") if m.strip()]

                # Define list of models that the user is allowed to see within that block
                visible_models = []

                # For each model ...
                for model in models:
                    # Extract the underlying endpoint wrapper for this model
                    endpoint_slug = slugify(
                        " ".join([block.Cluster, block.Framework, model.lower()])
                    )
                    response = await get_endpoint_wrapper(endpoint_slug)

                    # Continue if the endpoint does not exist to ignore test/dev running jobs
                    if response.error_message:
                        if "does not exist" in response.error_message:
                            continue
                        else:
                            return await get_response(
                                response.error_message, response.error_code, request
                            )

                    # Flag the model as "visible" if the user is authorized to see it ...
                    endpoint = response.endpoint
                    if endpoint.check_permission(
                        request.auth, request.user_group_uuids
                    ).is_authorized:
                        visible_models.append(model)

                # Remove block if no model should be visible
                if len(visible_models) == 0:
                    del jobs_state[i_block]

                # Update models if some (or all) of them are still visible
                else:
                    jobs_state[i_block].Models = ",".join(visible_models)

    # Return the cluster's jobs status
    return await get_response(jobs.model_dump(), 200, request)


# Inference batch (POST)
@router.post("/{cluster}/{framework}/v1/batches")
async def post_batch_inference(request, cluster: str, framework: str, *args, **kwargs):
    """POST request to send a batch to Globus Compute endpoints."""

    # Validate and build the inference request data
    batch_data = validate_batch_body(request)
    if "error" in batch_data.keys():
        return await get_response(batch_data["error"], 400, request)

    # Get cluster wrapper from database
    response = await get_cluster_wrapper(cluster)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    cluster: BaseCluster = response.cluster

    # Error if the cluster is under maintenance
    response = cluster.check_maintenance()
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)

    # Verify that the framework is enabled by the cluster
    if framework not in cluster.frameworks:
        return await get_response(
            f"Error: framework {framework} not available on cluster {cluster.cluster_name}.",
            400,
            request,
        )

    # Build the requested endpoint slug
    endpoint_slug = slugify(
        " ".join([cluster.cluster_name, framework, batch_data["model"].lower()])
    )

    # Get endpoint wrapper from database
    response = await get_endpoint_wrapper(endpoint_slug)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    endpoint = response.endpoint

    # Error if batch is disabled for this endpoint
    if not endpoint.has_batch_enabled():
        return await get_response(
            f"Error: Batch is unavailable for endpoint {endpoint_slug}", 501, request
        )

    # Block access if the user is not allowed to use the endpoint
    response = endpoint.check_permission(request.auth, request.user_group_uuids)
    if (response.is_authorized == False) or response.error_message:
        return await get_response(response.error_message, response.error_code, request)

    # Reject request if the allowed quota per user would be exceeded
    try:
        number_of_active_batches = 0
        async for batch in (
            BatchLog.objects.filter(
                access_log__user__username=request.auth.username,
                status__in=["pending", "running"],
            )
            .select_related("access_log", "access_log__user")
            .aiterator()
        ):
            number_of_active_batches += 1
        if number_of_active_batches >= settings.MAX_BATCHES_PER_USER:
            error_message = f"Error: Quota of {settings.MAX_BATCHES_PER_USER} active batch(es) per user exceeded."
            return await get_response(error_message, 400, request)
    except Exception as e:
        return await get_response(
            f"Error: Could not query active batches owned by user: {e}", 400, request
        )

    # Error if an ongoing batch already exists with the same input_file for the same user
    try:
        async for batch in (
            BatchLog.objects.filter(
                access_log__user__username=request.auth.username,
                input_file=batch_data["input_file"],
            )
            .select_related("access_log", "access_log__user")
            .aiterator()
        ):
            if not batch.status in [
                BatchStatusEnum.failed.value,
                BatchStatusEnum.completed.value,
            ]:
                error_message = f"Error: Input file {batch_data['input_file']} already used by ongoing batch {batch.batch_id}."
                return await get_response(error_message, 400, request)
    except BatchLog.DoesNotExist:
        pass  # Batch can be submitted if the input_file is not used by any other batches
    except Exception as e:
        return await get_response(
            f"Error: Could not filter Batch database entries: {e}", 400, request
        )

    # Submit batch
    batch_response = await endpoint.submit_batch(batch_data, request.auth.username)

    # Create batch log data
    request.batch_log_data = BatchLogPydantic(
        id=batch_response.batch_id,
        task_ids=batch_response.task_ids,
        cluster=cluster.cluster_name,
        framework=framework,
        model=batch_data["model"],
        input_file=batch_data["input_file"],
        output_folder_path=batch_data.get("output_folder_path", ""),
        status=batch_response.status,
        in_progress_at=timezone.now(),
    )

    # Error if something went wrong during the batch submission
    if batch_response.error_message:
        return await get_response(
            batch_response.error_message, batch_response.error_code, request
        )

    # Prepare response and return it to the user
    response = {
        "batch_id": request.batch_log_data.id,
        "input_file": request.batch_log_data.input_file,
        "status": request.batch_log_data.status,
    }
    return await get_response(json.dumps(response), 200, request)


# List of batches (GET)
@router.get("/v1/batches")
async def get_batch_list(
    request, filters: BatchListFilter = Query(...), *args, **kwargs
):
    """GET request to list all batches linked to the authenticated user."""

    # Declare the list of batches to be returned to the user
    batch_list = []

    # For each batch object owned by the user ...
    try:
        async for batch in (
            BatchLog.objects.filter(access_log__user__username=request.auth.username)
            .select_related("access_log", "access_log__user")
            .aiterator()
        ):
            # If the batch status needs to be revised ...
            if batch.status not in [
                BatchStatusEnum.completed.value,
                BatchStatusEnum.failed.value,
            ]:
                # Get the latest batch status and result (and update database if needed)
                response = await update_batch(batch)
                if response.error_message:
                    return await get_response(
                        response.error_message, response.error_code, request
                    )
                batch = response.batch

            # If no optional status filter was provided ...
            # or if the status filter matches the current batch status ...
            if isinstance(filters.status, type(None)) or (
                isinstance(filters.status, str) and filters.status == batch.status
            ):
                # Add the batch details to the list
                batch_list.append(
                    {
                        "batch_id": str(batch.id),
                        "cluster": batch.cluster,
                        "framework": batch.framework,
                        "input_file": batch.input_file,
                        "in_progress_at": str(batch.in_progress_at),
                        "completed_at": str(batch.completed_at),
                        "failed_at": str(batch.failed_at),
                        "status": batch.status,
                    }
                )

    # Will return empty list if no batch object was found
    except BatchLog.DoesNotExist:
        pass

    # Error message if something went wrong
    except Exception as e:
        return await get_response(
            f"Error: Could not filter Batch database entries: {e}", 400, request
        )

    # Return list of batches
    return await get_response(json.dumps(batch_list), 200, request)


# Inference batch status (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches/{batch_id}")
async def get_batch_status(request, batch_id: str, *args, **kwargs):
    """GET request to query status of an existing batch job."""

    # Recover batch object in the database
    try:
        batch: BatchLog = await sync_to_async(
            lambda: BatchLog.objects.select_related(
                "access_log", "access_log__user"
            ).get(id=batch_id),
            thread_sensitive=True,
        )()
    except BatchLog.DoesNotExist:
        return await get_response(
            f"Error: Batch {batch_id} does not exist.", 400, request
        )
    except Exception as e:
        return await get_response(
            f"Error: Could not access Batch {batch_id} from database: {e}", 500, request
        )

    # Make sure user has permission to access this batch_id
    try:
        if not request.auth.username == batch.access_log.user.username:
            return await get_response(
                f"Error: Permission denied to Batch {batch_id}.", 403, request
            )
    except Exception as e:
        return await get_response(
            f"Error: Something went wrong while parsing Batch {batch_id}: {e}",
            500,
            request,
        )

    # Return status directly if batch already completed or failed
    if batch.status in [BatchStatusEnum.completed.value, BatchStatusEnum.failed.value]:
        return await get_response(json.dumps(batch.status), 200, request)

    # Get the latest batch status and result (and update database if needed)
    response = await update_batch(batch)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    batch = response.batch

    # Return status of the batch job
    return await get_response(json.dumps(batch.status), 200, request)


# Inference batch result (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches/{batch_id}/result")
async def get_batch_result(request, batch_id: str, *args, **kwargs):
    """GET request to recover result from an existing batch job."""

    # Recover batch object in the database
    try:
        batch = await sync_to_async(
            lambda: BatchLog.objects.select_related(
                "access_log", "access_log__user"
            ).get(id=batch_id),
            thread_sensitive=True,
        )()
    except BatchLog.DoesNotExist:
        return await get_response(
            f"Error: Batch {batch_id} does not exist.", 400, request
        )
    except Exception as e:
        return await get_response(
            f"Error: Could not access Batch {batch_id} from database: {e}", 400, request
        )

    # Make sure user has permission to access this batch_id
    try:
        if not request.auth.username == batch.access_log.user.username:
            error_message = f"Error: Permission denied to Batch {batch_id}.."
            return await get_response(error_message, 403, request)
    except Exception as e:
        return await get_response(
            f"Error: Something went wrong while parsing Batch {batch_id}: {e}",
            400,
            request,
        )

    # Return error if batch failed
    if batch.status == BatchStatusEnum.failed.value:
        return await get_response(f"Error: Batch failed: {batch.result}", 400, request)

    # Return result if batch already finished
    if batch.status == BatchStatusEnum.completed.value:
        return await get_response(json.dumps(batch.result), 200, request)

    # Get the latest batch status and result (and update database if needed)
    response = await update_batch(batch)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    batch = response.batch

    # Return error if results are not ready yet
    if batch.status != BatchStatusEnum.completed.value:
        return await get_response(
            "Error: Batch not completed yet. Results not ready.", 400, request
        )

    # Return status of the batch job
    return await get_response(json.dumps(batch.result), 200, request)


# Inference (POST)
@router.post("/{cluster}/{framework}/v1/{path:openai_endpoint}")
async def post_inference(
    request, cluster: str, framework: str, openai_endpoint: str, *args, **kwargs
):
    """POST request to reach Globus Compute endpoints."""

    # Validate and build the inference request data, and clear openai_endpoint string
    data = validate_request_body(request, openai_endpoint)
    if "error" in data.keys():
        return await get_response(data["error"], 400, request)
    openai_endpoint = data["model_params"].get("openai_endpoint", openai_endpoint)

    # Get cluster wrapper from database
    response = await get_cluster_wrapper(cluster)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    cluster: BaseCluster = response.cluster

    # Error if the cluster is under maintenance
    response = cluster.check_maintenance()
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)

    # Verify that the framework is available by the cluster
    if framework not in cluster.frameworks:
        return await get_response(
            f"Error: framework {framework} not available on cluster {cluster.cluster_name}.",
            400,
            request,
        )

    # Verify that the openAI endpoint is available by the cluster
    if openai_endpoint not in cluster.openai_endpoints:
        return await get_response(
            f"Error: OpenAI endpoint {openai_endpoint} not available on cluster {cluster.cluster_name}.",
            400,
            request,
        )

    # Check if streaming is requested
    stream = data["model_params"].get("stream", False)

    # Build the requested endpoint slug
    endpoint_slug = slugify(
        " ".join(
            [cluster.cluster_name, framework, data["model_params"]["model"].lower()]
        )
    )
    log.info(f"endpoint_slug: {endpoint_slug} - user: {request.auth.username}")

    # Get endpoint wrapper from database
    response = await get_endpoint_wrapper(endpoint_slug)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    endpoint = response.endpoint

    # Block access if the user is not allowed to use the endpoint
    response = endpoint.check_permission(request.auth, request.user_group_uuids)
    if (response.is_authorized == False) or response.error_message:
        return await get_response(response.error_message, response.error_code, request)

    # Initialize the request log data for the database entry
    request.request_log_data = RequestLogPydantic(
        id=str(uuid.uuid4()),
        cluster=cluster.cluster_name,
        framework=framework,
        openai_endpoint=data["model_params"]["openai_endpoint"],
        prompt=json.dumps(extract_prompt(data["model_params"])),
        model=data["model_params"]["model"],
        timestamp_compute_request=timezone.now(),
    )

    # Submit task
    if stream:
        task_response = await endpoint.submit_streaming_task(
            data, request.request_log_data.id
        )
    else:
        task_response = await endpoint.submit_task(data)

    # Update request log data
    request.request_log_data.task_uuid = task_response.task_id
    request.request_log_data.timestamp_compute_response = timezone.now()

    # Display error message if any
    if task_response.error_message:
        return await get_response(
            task_response.error_message, task_response.error_code, request
        )

    # If streaming, meaning that the StreamingHttpResponse object will be returned directly ...
    if stream:
        # Manually create access and request logs to database
        try:
            access_log = await create_access_log(request.access_log_data, None, 200)
            request.request_log_data.access_log = access_log
            _ = await create_request_log(
                request.request_log_data, "streaming_response_in_progress", 200
            )
        except Exception as e:
            return HttpResponse(
                json.dumps(f"Error: Could not save access and request logs: {e}"),
                status=500,
            )

        # Return StreamingHttpResponse object directly
        return task_response.response

    # If not streaming, return the complete response and automate database operations
    else:
        return await get_response(task_response.result, 200, request)


# Streaming server endpoints (integrated into Django)
@router.post("/api/streaming/data/", auth=None, throttle=[])
async def receive_streaming_data(request):
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
async def receive_streaming_error(request):
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
async def receive_streaming_done(request):
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


# Federated Inference (POST) - Chooses cluster/framework automatically
# TEMPORARY: deactivating federated inference. Needs to be re-written with endpoint/cluster wrappers
'''
@router.post("/v1/{path:openai_endpoint}")
async def post_federated_inference(request, openai_endpoint: str, *args, **kwargs):
    """
    POST request to automatically select an appropriate Globus Compute endpoint
    based on model availability and cluster status, abstracting cluster/framework.
    """

    # Strip the last forward slash if needed
    if openai_endpoint[-1] == "/":
        openai_endpoint = openai_endpoint[:-1]

    # Validate and build the inference request data - crucial for getting the model name
    data = validate_request_body(request, openai_endpoint)
    if "error" in data.keys():
        return await get_response(data['error'], 400, request) # Use get_response to log failure

    # Update the database with the input text from user and specific OpenAI endpoint
    requested_model = data["model_params"]["model"] # Model name is needed for filtering

    log.info(f"Federated request for model: {requested_model} - user: {request.auth.username}")

    # --- Endpoint Selection Logic ---
    selected_endpoint = None
    error_message = "No suitable endpoint found for the requested model."
    error_code = 503 # Service Unavailable by default

    try:
        # 1. Find the FederatedEndpoint definition for the requested model
        try:
            get_fed_endpoint_async = sync_to_async(FederatedEndpoint.objects.get)
            federated_definition = await get_fed_endpoint_async(target_model_name=requested_model)
            log.info(f"Found FederatedEndpoint '{federated_definition.slug}' definition for model {requested_model}.")
        except FederatedEndpoint.DoesNotExist:
            error_message = f"Error: No federated endpoint definition found for model '{requested_model}'."
            error_code = 404 # Not Found
            raise ValueError(error_message)
        except Exception as e:
            error_message = f"Error retrieving federated definition for model '{requested_model}': {e}"
            error_code = 500
            raise ValueError(error_message)

        # Parse the list of targets from the FederatedEndpoint
        targets = federated_definition.targets
        if not targets:
            error_message = f"Error: Federated definition '{federated_definition.slug}' has no associated targets."
            error_code = 500 # Configuration error
            raise ValueError(error_message)

        # 2. Filter targets accessible by the user
        accessible_targets = []
        for target in targets:
            allowed_groups, msg = extract_group_uuids(target.get("allowed_globus_groups", ""))
            if len(msg) > 0:
                log.warning(f"Skipping target {target['cluster']} due to group parsing error: {msg}")
                continue
            if len(allowed_groups) == 0 or len(set(request.user_group_uuids).intersection(allowed_groups)) > 0:
                accessible_targets.append(target)

        if not accessible_targets:
            error_message = f"Error: User not authorized to access any target for model '{requested_model}'."
            error_code = 401
            raise ValueError(error_message)
        
        log.info(f"Found {len(accessible_targets)} accessible targets for federated model {requested_model}.")

        # Get Globus Compute client (needed for status checks)
        try:
            gcc = globus_utils.get_compute_client_from_globus_app()
            gce = globus_utils.get_compute_executor(client=gcc) # Needed for qstat
        except Exception as e:
            error_message = f"Error: Could not get Globus Compute client/executor for status checks: {e}"
            error_code = 500
            raise ConnectionError(error_message)

        # 2. Prioritize targets based on status (Running/Queued > Online > Fallback)
        targets_with_status = []
        qstat_cache = {} # Cache qstat results per cluster

        for target in accessible_targets:
            cluster = target["cluster"]
            endpoint_slug = target["endpoint_slug"]

            # Check Globus endpoint status first
            gc_status, gc_error = globus_utils.get_endpoint_status(
                endpoint_uuid=target["endpoint_uuid"], client=gcc, endpoint_slug=endpoint_slug
            )
            if len(gc_error) > 0:
                log.warning(f"Could not get Globus status for {endpoint_slug}: {gc_error}. Skipping.")
                continue
            
            is_online = gc_status["status"] == "online"
            model_job_status = "unknown" # e.g., running, queued, stopped, unknown
            free_nodes = -1 # Default to unknown

            # Check qstat if endpoint is online and cluster supports it
            if is_online and cluster in ALLOWED_QSTAT_ENDPOINTS:
                if cluster not in qstat_cache:
                    # Fetch qstat details only once per cluster per request
                    qstat_result_str, _, q_err, q_code = await get_qstat_details(
                        cluster, gcc=gcc, gce=gce, timeout=30 # Shorter timeout for selection
                    )
                    if len(q_err) > 0 or q_code != 200:
                        log.warning(f"Could not get qstat for cluster {cluster}: {q_err} (Code: {q_code}). Status checks degraded.")
                        qstat_cache[cluster] = {"error": True, "data": {}}
                    else:
                        try:
                             qstat_data = json.loads(qstat_result_str)
                             qstat_cache[cluster] = {
                                 "error": False, 
                                 "data": qstat_data,
                                 "free_nodes": qstat_data.get('cluster_status', {}).get('free_nodes', -1)
                             }
                        except json.JSONDecodeError:
                            log.warning(f"Could not parse qstat JSON for cluster {cluster}. Status checks degraded.")
                            qstat_cache[cluster] = {"error": True, "data": {}, "free_nodes": -1}
                
                # Parse cached qstat data for this specific model/endpoint
                if not qstat_cache[cluster]["error"]:
                    qstat_data = qstat_cache[cluster]["data"]
                    free_nodes = qstat_cache[cluster]["free_nodes"] # Get free nodes count from cache
                    found_in_qstat = False
                    for state in ["running", "queued"]:
                        if state in qstat_data:
                            for job in qstat_data[state]:
                                # Check if the job matches cluster, framework, and serves the model
                                if (job.get("Cluster") == cluster and
                                    job.get("Framework") == target["framework"] and
                                    requested_model in job.get("Models Served", "").split(",")):
                                    model_job_status = "queued" if state == "queued" else job.get("Model Status", "running")
                                    found_in_qstat = True
                                    break # Found in this state
                        if found_in_qstat: break # Found in qstat overall
                    if not found_in_qstat:
                         model_job_status = "stopped" # qstat ran, but model not listed
            
            elif not is_online:
                 model_job_status = "offline" # Globus endpoint itself is offline

            targets_with_status.append({
                "target": target,
                "is_online": is_online,
                "job_status": model_job_status, # running, queued, stopped, offline, unknown
                "free_nodes": free_nodes # -1 if unknown
            })

        # Selection Algorithm:
        priority1_running = [t for t in targets_with_status if t["job_status"] == "running"]
        priority1_queued = [t for t in targets_with_status if t["job_status"] == "queued"]
        priority2_online_free = [t for t in targets_with_status if t["is_online"] and t["free_nodes"] > 0]
        priority3_online_other = [t for t in targets_with_status if t["is_online"] and t["free_nodes"] <= 0]
        
        # TODO: Add smarter selection within priorities (e.g., load balancing, lowest queue)
        # For now, just take the first available in priority order.

        if priority1_running:
            selected_endpoint = priority1_running[0]["target"]
            log.info(f"Selected running endpoint: {selected_endpoint['endpoint_slug']}")
        elif priority1_queued:
            selected_endpoint = priority1_queued[0]["target"]
            log.info(f"Selected queued endpoint: {selected_endpoint['endpoint_slug']}")
        elif priority2_online_free:
             selected_endpoint = priority2_online_free[0]["target"]
             log.info(f"Selected online endpoint on cluster with free nodes: {selected_endpoint['endpoint_slug']}")
        elif priority3_online_other: # Online, but couldn't determine job status via qstat or no free nodes
            selected_endpoint = priority3_online_other[0]["target"]
            log.info(f"Selected online endpoint (no free nodes or unknown status): {selected_endpoint['endpoint_slug']}")
        else:
            # Fallback: First accessible endpoint overall (even if offline/unknown, submit will handle it)
            # This case should be rare if accessible_endpoints is not empty
            if accessible_targets:
                selected_endpoint = accessible_targets[0]
                log.warning(f"No ideal endpoint found. Falling back to first accessible concrete endpoint: {selected_endpoint['endpoint_slug']}")
            else:
                # This should not happen based on earlier checks, but safeguard anyway.
                 error_message = f"Federated Error: No *accessible* concrete endpoints remained after status checks for model '{requested_model}'."
                 error_code = 500
                 raise RuntimeError(error_message)


    except (ValueError, ConnectionError, RuntimeError) as e:
        # Errors raised during selection logic (already contain message/code)
        log.error(f"Federated selection failed: {e}")
        # error_message and error_code are set before raising
        return await get_response(error_message, error_code, request)
    except Exception as e:
        # Catch-all for unexpected errors during selection
        error_message = f"Unexpected error during endpoint selection: {e}"
        error_code = 500
        log.exception(error_message) # Log traceback
        return await get_response(error_message, error_code, request)

    # --- Execution with Selected Endpoint ---
    if not selected_endpoint:
        # Should be caught above, but final safety check
        return await get_response("Internal Server Error: Endpoint selection failed unexpectedly.", 500, request)

    # Update db_data with the *actual* endpoint chosen
    #db_data["endpoint_slug"] = selected_endpoint["endpoint_slug"]

    # Prepare data for the specific chosen endpoint
    try:
        data["model_params"]["api_port"] = selected_endpoint["api_port"]
        # Ensure the model name in the request matches the endpoint's model (case might differ)
        data["model_params"]["model"] = selected_endpoint["model"]
    except Exception as e:
        return await get_response(f"Error processing selected endpoint data for {selected_endpoint['endpoint_slug']}: {e}", 500, request)

    # Check Globus status *again* right before submission (could have changed)
    # Use the same gcc client from before
    final_status, final_error = globus_utils.get_endpoint_status(
        endpoint_uuid=selected_endpoint["endpoint_uuid"], client=gcc, endpoint_slug=selected_endpoint["endpoint_slug"]
    )
    if len(final_error) > 0:
        return await get_response(f"Error confirming status for selected endpoint {selected_endpoint['endpoint_slug']}: {final_error}", 500, request)
    if not final_status["status"] == "online":
        return await get_response(f"Error: Selected endpoint {selected_endpoint['endpoint_slug']} went offline before submission.", 503, request)
    
    resources_ready = int(final_status["details"].get("managers", 0)) > 0

    # Initialize the request log data for the database entry
    request.request_log_data = RequestLogPydantic(
        id=str(uuid.uuid4()),
        cluster=selected_endpoint["cluster"],
        framework=selected_endpoint["framework"],
        model=data["model_params"]["model"],
        openai_endpoint=data["model_params"]["openai_endpoint"],
        prompt=json.dumps(extract_prompt(data["model_params"])),
        timestamp_compute_request=timezone.now()
    )

    # Submit task to the chosen endpoint and wait for result
    result, task_uuid, submit_error_message, submit_error_code = await globus_utils.submit_and_get_result(
        gce, selected_endpoint["endpoint_uuid"], selected_endpoint["function_uuid"], data=data
    )
    request.request_log_data.timestamp_compute_response = timezone.now()
    if len(submit_error_message) > 0:
        # Submission failed, log with the chosen endpoint slug
        return await get_response(submit_error_message, submit_error_code, request)
    request.request_log_data.task_uuid = task_uuid

    # Return Globus Compute results
    return await get_response(result, 200, request)
'''

# Add URLs to the Ninja API
api.add_router("/", router)
