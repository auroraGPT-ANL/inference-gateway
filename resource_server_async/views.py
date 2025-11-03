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
import utils.globus_utils as globus_utils
import utils.metis_utils as metis_utils
from utils.pydantic_models.db_models import RequestLogPydantic, BatchLogPydantic, UserPydantic
from resource_server_async.utils import (
    validate_url_inputs, 
    validate_cluster_framework,
    extract_prompt, 
    validate_request_body,
    validate_batch_body,
    get_qstat_details,
    update_batch_status_result,
    ALLOWED_QSTAT_ENDPOINTS,
    BatchListFilter,
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
    # Metis utilities
    get_endpoint_wrapper,
    get_cluster_wrapper
)
log.info("Utils functions loaded.")

# Django database
from resource_server.models import (
    Batch, 
    FederatedEndpoint
)
from resource_server.models import Endpoint as EndpointOld
from resource_server_async.models import RequestLog, BatchLog, Endpoint, Cluster

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
    return JsonResponse({'status': 'ok'}, status=200)


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
            auth_service=request.auth.auth_service
        )
    except Exception as e:
        return await get_response(f"Error: could not create user from request.auth: {e}", 500, request)
    
    # Return user details
    return await get_response(user.model_dump_json(), 200, request)


# List Endpoints (GET)
@router.get("/list-endpoints")
async def get_list_endpoints(request):
    """GET request to list the available frameworks and models."""

    # Get list of all clusters
    try:
        db_clusters = await sync_to_async(list)(Cluster.objects.all())
    except Exception as e:
        return await get_response(f"Error: Could not access Cluster database entries: {e}", 500, request)
    
    # For each cluster ...
    all_endpoints = {"clusters": {}}
    for db_cluster in db_clusters:
        try:

            # Get cluster wrapper from database
            response = await get_cluster_wrapper(db_cluster.cluster_name)
            if response.error_message:
                return await get_response(response.error_message, response.error_code, request)
            cluster = response.cluster

            # If the user is allowed to see the cluster ...
            response = cluster.check_permission(request.auth, request.user_group_uuids)
            if response.is_authorized:

                # Collect the list of endpoints that the user is authorized to see
                all_endpoints["clusters"][cluster.cluster_name] = await cluster.get_endpoint_list(request.auth, request.user_group_uuids)

        # Error message if something went wrong while building the endpoint list
        except Exception as e:
            return await get_response(f"Error: Could not generate list of frameworks and models from database: {e}", 500, request)

    # Return list of frameworks and models
    return await get_response(json.dumps(all_endpoints), 200, request)


# List Endpoints Detailed (GET)
@router.get("/list-endpoints-detailed")
async def get_list_endpoints_detailed(request):
    """GET request to list the available frameworks and models with live status."""

    # Collect endpoints objects from the database
    try:
        endpoint_list = await sync_to_async(list)(Endpoint.objects.all())
    except Exception as e:
        return await get_response(f"Error: Could not access Endpoint database entries: {e}", 400, request)
    
    # Get Globus Compute client and executor
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests
    # NOTE: Do not include endpoint_id argument, otherwise it will cache multiple executors
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
        gce = globus_utils.get_compute_executor(client=gcc)
    except Exception as e:
        return await get_response(f"Error: Could not get the Globus Compute client or executor: {e}", 500, request)

    # Prepare the list of available frameworks and models
    all_endpoints = {"clusters": {}}
    qstat_model_status = {}
    qstat_cluster_available = []
    try:

        # For each database endpoint entry ...
        for endpoint in endpoint_list:

            # Extract the list of allowed group UUIDs tied to the endpoint
            allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
            if len(error_message) > 0:
                return await get_response(json.dumps(error_message), 400, request)
    
            # If the user is allowed to see the endpoint ...
            # i.e. if (there is no restriction) or (if the user is at least part of one allowed groups) ...
            if len(allowed_globus_groups) == 0 or len(set(request.user_group_uuids).intersection(allowed_globus_groups)) > 0:

                # If this is a new cluster for the dictionary ...
                if not endpoint.cluster in all_endpoints["clusters"]:

                    # Add new entry to the dictionary
                    all_endpoints["clusters"][endpoint.cluster] = {
                        "base_url": f"/resource_server/{endpoint.cluster}",
                        "frameworks": {}
                    }

                    # If it is possible to collect qstat details on the jobs running/queued on the cluster ...
                    if endpoint.cluster in ALLOWED_QSTAT_ENDPOINTS:

                        # Collect qstat details on the jobs running/queued on the cluster
                        qstat_result, task_uuid, error_message, error_code = await get_qstat_details(
                            endpoint.cluster, gcc=gcc, gce=gce, timeout=60
                        )
                        qstat_result = json.loads(qstat_result)

                        # Re-organize the qstat result into a dictionary with endpoint_slugs (as keys) and status (as values)
                        # NOTE: If the qstat job fails, keep going, the response will simply contain less detailed info
                        try:

                            # For all running and queued jobs ...
                            for state in ["running", "queued"]:
                                for entry in qstat_result[state]:

                                    # Extract the job status
                                    if state == "queued":
                                        model_status = "queued"
                                    else:
                                        model_status = entry["Model Status"]
                                
                                    # For each model served ...
                                    for model in entry["Models Served"].split(","):
                                        if len(model) > 0:

                                            # Build endpoint slug and add status to the qstat dictionary
                                            endpoint_slug = slugify(" ".join(
                                                [entry["Cluster"], entry["Framework"], model]
                                            ))
                                            qstat_model_status[endpoint_slug] = model_status

                            # Add cluster to the list of clusters that have successful qstat query
                            qstat_cluster_available.append(endpoint.cluster)

                        except:
                            pass

                # Add a new framework dictionary entry if needed
                # TODO: Make sure this dynamically get populated based on the cluster using ALLOWED_OPENAI_ENDPOINTS
                if not endpoint.framework in all_endpoints["clusters"][endpoint.cluster]["frameworks"]:
                    all_endpoints["clusters"][endpoint.cluster]["frameworks"][endpoint.framework] = {
                        "models": [],
                        "endpoints": {
                            "chat": f"/{endpoint.framework}/v1/chat/completions/",
                            "completion": f"/{endpoint.framework}/v1/completions/",
                            "embedding": f"/{endpoint.framework}/v1/embeddings/"
                        }
                    }

                # Check status of the current Globus Compute endpoint
                endpoint_status, error_message = globus_utils.get_endpoint_status(
                    endpoint_uuid=endpoint.endpoint_uuid, client=gcc, endpoint_slug=endpoint.endpoint_slug
                )
                if len(error_message) > 0:
                    return await get_response(error_message, 500, request)
                
                # Assign the status of the HPC job assigned to the model
                # NOTE: "offline" status should always take priority over the qstat result
                if endpoint_status["status"] == "online":
                    if endpoint.endpoint_slug in qstat_model_status:
                        model_status = qstat_model_status[endpoint.endpoint_slug]
                    elif endpoint.cluster in qstat_cluster_available:
                        model_status = "stopped"
                    else:
                        model_status = "status not available"
                else:
                    model_status = "status not available"

                # Add model to the dictionary
                all_endpoints["clusters"][endpoint.cluster]["frameworks"][endpoint.framework]["models"].append(
                    {
                        "name": endpoint.model,
                        "endpoint_status": endpoint_status["status"],
                        "model_status": model_status
                    }    
                )

        # Sort models alphabetically (case insensitive)
        for cluster in all_endpoints["clusters"]:
            for framework in all_endpoints["clusters"][cluster]["frameworks"]:
                all_endpoints["clusters"][cluster]["frameworks"][framework]["models"] = \
                    sorted(all_endpoints["clusters"][cluster]["frameworks"][framework]["models"], key=lambda x: x["name"].lower())

    # Error message if something went wrong while building the endpoint list
    except Exception as e:
        return await get_response(f"Error: Could not generate list of frameworks and models from database: {e}", 400, request)

    # Return list of frameworks and models
    return await get_response(json.dumps(all_endpoints), 200, request)


# Endpoint Status (GET)
@router.get("/{cluster}/{framework}/{path:model}/status")
async def get_endpoint_status(request, cluster: str, framework: str, model: str, *args, **kwargs):
    """GET request to get a detailed status of a specific Globus Compute endpoint."""

    # Get the requested endpoint from the database
    endpoint_slug = slugify(" ".join([cluster, framework, model]))
    try:
        endpoint = await sync_to_async(Endpoint.objects.get)(endpoint_slug=endpoint_slug)
    except Endpoint.DoesNotExist:
        return await get_response(f"Error: The requested endpoint {endpoint_slug} does not exist.", 400, request)
    except Exception as e:
        return await get_response(f"Error: Could not extract endpoint and function UUIDs: {e}", 400, request)
    
    # Error message if user is not allowed to see the endpoint
    allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
    if len(error_message) > 0:
        return await get_response(json.dumps(error_message), 400, request)
    if len(allowed_globus_groups) > 0 and len(set(request.user_group_uuids).intersection(allowed_globus_groups)) == 0:
        return await get_response(f"Error: User not authorized to access endpoint {endpoint_slug}", 401, request)

    # Get Globus Compute client and executor
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests
    # NOTE: Do not include endpoint_id argument, otherwise it will cache multiple executors
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
        gce = globus_utils.get_compute_executor(client=gcc)
    except Exception as e:
        return await get_response(f"Error: Could not get the Globus Compute client or executor: {e}", 500, request)
    
    # Extract the status of the current Globus Compute endpoint
    endpoint_status, error_message = globus_utils.get_endpoint_status(
        endpoint_uuid=endpoint.endpoint_uuid, client=gcc, endpoint_slug=endpoint.endpoint_slug
    )
    if len(error_message) > 0:
        return await get_response(error_message, 500, request)

    # If it is possible to collect qstat details on the jobs running/queued on the cluster ...
    model_status = {}
    if endpoint_status["status"] == "online":
        if cluster in ALLOWED_QSTAT_ENDPOINTS:

            # Collect qstat details on the jobs running/queued on the cluster
            qstat_result, task_uuid, error_message, error_code = await get_qstat_details(
                cluster, gcc=gcc, gce=gce, timeout=60
            )
            qstat_result = json.loads(qstat_result)

            # Extract the targetted model if within the qstat result ...
            try:
                for state in qstat_result:
                    for entry in qstat_result[state]:
                        if entry["Framework"] == framework and model in entry["Models Served"]:
                            model_status = entry
                            break
            except:
                pass

    # Build and return detailed status
    status = {
        "cluster": cluster,
        "model": model_status,
        "endpoint": endpoint_status
    }
    return await get_response(status, 200, request)


# List running and queue models (GET)
@router.get("/{cluster}/jobs")
async def get_jobs(request, cluster:str):
    """GET request to list the available frameworks and models."""

    # ===== GLOBUS COMPUTE CLUSTER HANDLING =====
    # Make sure the URL inputs point to an available endpoint 
    framework = "api" if cluster == "metis" else "vllm"
    error_message = validate_url_inputs(cluster, framework=framework, openai_endpoint="chat/completions")
    if len(error_message):
        return await get_response(error_message, 400, request)

        # ===== METIS CLUSTER HANDLING =====
    if cluster == "metis":
        # Metis uses a status API instead of qstat
        metis_status, error_msg = await metis_utils.fetch_metis_status(use_cache=True)
        if error_msg:
            return await get_response(f"Error fetching Metis status: {error_msg}", 503, request)
        
        # Format Metis status to match the jobs endpoint format
        formatted_result = metis_utils.format_metis_status_for_jobs(metis_status)
        return await get_response(json.dumps(formatted_result), 200, request)


    # Collect (qstat) details on the jobs running/queued on the cluster
    result, task_uuid, error_message, error_code = await get_qstat_details(cluster, timeout=60)
    if len(error_message) > 0:
        return await get_response(error_message, error_code, request)
    
    # Return Globus Compute results
    return await get_response(result, 200, request)


# Inference batch (POST)
# TODO: Use primary identity username to claim ownership on files and batches
@router.post("/{cluster}/{framework}/v1/batches")
async def post_batch_inference(request, cluster: str, framework: str, *args, **kwargs):
    """POST request to send a batch to Globus Compute endpoints."""
    
    # Reject request if the allowed quota per user would be exceeded
    try:
        number_of_active_batches = 0
        async for batch in BatchLog.objects.filter(
            access_log__user__username=request.auth.username,
            status__in=["pending", "running"]
        ).select_related("access_log", "access_log__user").aiterator():
            number_of_active_batches += 1
        if number_of_active_batches >= settings.MAX_BATCHES_PER_USER:
            error_message = f"Error: Quota of {settings.MAX_BATCHES_PER_USER} active batch(es) per user exceeded."
            return await get_response(error_message, 400, request)
    except Exception as e:
        return await get_response(f"Error: Could not query active batches owned by user: {e}", 400, request)
    
    # Validate and build the inference request data
    batch_data = validate_batch_body(request)
    if "error" in batch_data.keys():
        return await get_response(batch_data['error'], 400, request)

    # Make sure the URL inputs point to an available endpoint 
    error_message = validate_cluster_framework(cluster, framework)
    if len(error_message):
        return await get_response(error_message, 400, request)

    # Get endpoint wrapper from database
    response = await get_endpoint_wrapper(endpoint_slug)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    endpoint = response.endpoint

    # Block access if the user is not allowed to use the endpoint
    response = endpoint.check_permission(request.auth, request.user_group_uuids)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
        
    # Make sure the endpoint has batch UUIDs
    if len(endpoint.batch_endpoint_uuid) == 0 or len(endpoint.batch_function_uuid) == 0:
        return await get_response(f"Endpoint {endpoint_slug} does not have batch enabled.", 501, request)

    # Error if an ongoing batch already exists with the same input_file
    # TODO: More checks here to make sure we don't duplicate batches?
    #       Do we allow multiple batches on the same file on different clusters?
    try:
        async for batch in BatchLog.objects.filter(input_file=batch_data["input_file"]):
            if not batch.status in ["failed", "completed"]:
                error_message = f"Error: Input file {batch_data['input_file']} already used by ongoing batch {batch.batch_id}."
                return await get_response(error_message, 400, request)
    except BatchLog.DoesNotExist:
        pass # Batch can be submitted if the input_file is not used by any other batches
    except Exception as e:
        return await get_response(f"Error: Could not filter Batch database entries: {e}", 400, request)

    # Get Globus Compute client (using the endpoint identity)
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
    except Exception as e:
        return await get_response(f"Error: Could not get the Globus Compute client: {e}", 500, request)

    # Query the status of the Globus Compute batch endpoint
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests,
    # otherwise, coroutines can set off the too-many-requests Globus error before the "first" requests can cache the status
    endpoint_slug = f"{endpoint_slug}/batch"
    endpoint_uuid = endpoint.batch_endpoint_uuid
    function_uuid = endpoint.batch_function_uuid
    endpoint_status, error_message = globus_utils.get_endpoint_status(
        endpoint_uuid=endpoint_uuid, client=gcc, endpoint_slug=endpoint_slug
    )
    if len(error_message) > 0:
        return await get_response(error_message, 500, request)

    # Error if the endpoint is not online
    if not endpoint_status["status"] == "online":
        return await get_response(f"Error: Endpoint {endpoint_slug} is offline.", 503, request)
    
    # Prepare input parameter for the compute tasks
    # NOTE: This is already in list format in case we submit multiple tasks per batch
    batch_id = str(uuid.uuid4())
    params_list = [
        {
            "model_params": {
                "input_file": batch_data["input_file"],
                "model": batch_data["model"]
            },
            "batch_id": batch_id,
            "username": request.auth.username
        }
    ]
    if "output_folder_path" in batch_data:
        params_list[0]["model_params"]["output_folder_path"] = batch_data["output_folder_path"]

    # Prepare the batch job
    try:
        batch = gcc.create_batch()
        for params in params_list:
            batch.add(function_id=function_uuid, args=[params])
    except Exception as e:
        return await get_response(f"Error: Could not create Globus Compute batch: {e}", 500, request)
    
    # Create batch log data
    request.batch_log_data = BatchLogPydantic(
        id=batch_id,
        cluster=cluster,
        framework=framework,
        model=batch_data["model"],
        input_file=batch_data["input_file"],
        output_folder_path=batch_data.get("output_folder_path", ""),
        status="failed",
        in_progress_at=timezone.now()
    )

    # Submit batch to Globus Compute and update batch status if submission is successful
    try:
        batch_response = gcc.batch_run(endpoint_id=endpoint_uuid, batch=batch)
        request.batch_log_data.status = "pending"
    except Exception as e:
        return await get_response(f"Error: Could not submit the Globus Compute batch: {e}", 500, request)
    
    # Extract the Globus batch UUID from submission
    try:
        request.batch_log_data.globus_batch_uuid = batch_response["request_id"]
    except Exception as e:
        return await get_response(f"Error: Batch submitted but no batch UUID recovered: {e}", 400, request)

    # Extract the batch and task UUIDs from submission
    try:
        request.batch_log_data.globus_task_uuids = ""
        for _, task_uuids in batch_response["tasks"].items():
            request.batch_log_data.globus_task_uuids += ",".join(task_uuids) + ","
        request.batch_log_data.globus_task_uuids = request.batch_log_data.globus_task_uuids[:-1]
    except Exception as e:
        return await get_response(f"Error: Batch submitted but no task UUID recovered: {e}", 400, request)

    # Create batch entry in the database and return response to the user
    response = {
        "batch_id": request.batch_log_data.id,
        "input_file": request.batch_log_data.input_file,
        "status": request.batch_log_data.status
    }
    return await get_response(json.dumps(response), 200, request)


# List of batches (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches")
async def get_batch_list(request, filters: BatchListFilter = Query(...), *args, **kwargs):
    """GET request to list all batches linked to the authenticated user."""

    # Declare the list of batches to be returned to the user
    batch_list = []
    try:

        # For each batch object owned by the user ...
        async for batch in BatchLog.objects.filter(
            access_log__user__username=request.auth.username
        ).select_related("access_log", "access_log__user").aiterator():

            # Get a status update for the batch (this will update the database if needed)
            batch_status, batch_result, error_message, code = await update_batch_status_result(batch)
            if len(error_message) > 0:
                return await get_response(error_message, code, request)
            
            # If no optional status filter was provided ...
            # or if the status filter matches the current batch status ...
            if isinstance(filters.status, type(None)) or \
                (isinstance(filters.status, str) and filters.status == batch_status):

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
                    "status": batch_status
                }
            )

    # Will return empty list if no batch object was found
    except BatchLog.DoesNotExist:
        pass

    # Error message if something went wrong
    except Exception as e:
        return await get_response(f"Error: Could not filter Batch database entries: {e}", 400, request)

    # Return list of batches
    return await get_response(json.dumps(batch_list), 200, request)


# Inference batch status (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches/{batch_id}")
async def get_batch_status(request, batch_id: str, *args, **kwargs):
    """GET request to query status of an existing batch job."""

    # Recover batch object in the database
    try:
        batch = await sync_to_async(
            lambda: BatchLog.objects.select_related(
                "access_log",
                "access_log__user"
            ).get(id=batch_id),
            thread_sensitive=True,
        )()
    except BatchLog.DoesNotExist:
        return await get_response(f"Error: Batch {batch_id} does not exist.", 400, request)
    except Exception as e:
        return await get_response(f"Error: Could not access Batch {batch_id} from database: {e}", 400, request)

    # Make sure user has permission to access this batch_id
    try:
        if not request.auth.username == batch.access_log.user.username:
            error_message = f"Error: Permission denied to Batch {batch_id}."
            return await get_response(error_message, 403, request)
    except Exception as e:
        return await get_response(f"Error: Something went wrong while parsing Batch {batch_id}: {e}", 400, request)
    
    # Get a status update for the batch (this will update the database if needed)
    batch_status, batch_result, error_message, code = await update_batch_status_result(batch)
    if len(error_message) > 0:
        return await get_response(error_message, code, request)

    # Return status of the batch job
    return await get_response(json.dumps(batch_status), 200, request)


# Inference batch result (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches/{batch_id}/result")
async def get_batch_result(request, batch_id: str, *args, **kwargs):
    """GET request to recover result from an existing batch job."""

    # Recover batch object in the database
    try:
        batch = await sync_to_async(
            lambda: BatchLog.objects.select_related(
                "access_log",
                "access_log__user"
            ).get(id=batch_id),
            thread_sensitive=True,
        )()
    except BatchLog.DoesNotExist:
        return await get_response(f"Error: Batch {batch_id} does not exist.", 400, request)
    except Exception as e:
        return await get_response(f"Error: Could not access Batch {batch_id} from database: {e}", 400, request)

    # Make sure user has permission to access this batch_id
    try:
        if not request.auth.username == batch.access_log.user.username:
            error_message = f"Error: Permission denied to Batch {batch_id}.."
            return await get_response(error_message, 403, request)
    except Exception as e:
        return await get_response(f"Error: Something went wrong while parsing Batch {batch_id}: {e}", 400, request)

    # Get a status update for the batch (this will update the database if needed)
    batch_status, batch_result, error_message, code = await update_batch_status_result(batch)
    if len(error_message) > 0:
        return await get_response(error_message, code, request)

    # Return error if batch failed
    if batch_status == "failed":
        return await get_response(f"Error: Batch failed: {batch.access_log.error}", 400, request)

    # Return error if results are not ready yet
    if not batch_status == "completed":
        return await get_response("Error: Batch not completed yet. Results not ready.", 400, request)

    # Return status of the batch job
    return await get_response(json.dumps(batch_result), 200, request)


# Inference (POST)
@router.post("/{cluster}/{framework}/v1/{path:openai_endpoint}")
async def post_inference(request, cluster: str, framework: str, openai_endpoint: str, *args, **kwargs):
    """POST request to reach Globus Compute endpoints."""
    
    # Strip the last forward slash if needed
    if openai_endpoint[-1] == "/":
        openai_endpoint = openai_endpoint[:-1]

    # Make sure the URL inputs point to an available endpoint 
    error_message = validate_url_inputs(cluster, framework, openai_endpoint)
    if len(error_message):
        return await get_response(error_message, 400, request)

    # Validate and build the inference request data
    data = validate_request_body(request, openai_endpoint)
    if "error" in data.keys():
        return await get_response(data['error'], 400, request)
    
    # Check if streaming is requested
    stream = data["model_params"].get('stream', False)
    
    # Build the requested endpoint slug
    endpoint_slug = slugify(" ".join([cluster, framework, data["model_params"]["model"].lower()]))
    log.info(f"endpoint_slug: {endpoint_slug} - user: {request.auth.username}")

    # Get endpoint wrapper from database
    response = await get_endpoint_wrapper(endpoint_slug)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    endpoint = response.endpoint

    # Block access if the user is not allowed to use the endpoint
    response = endpoint.check_permission(request.auth, request.user_group_uuids)
    if response.error_message:
        return await get_response(response.error_message, response.error_code, request)
    
    # Initialize the request log data for the database entry
    request.request_log_data = RequestLogPydantic(
        id=str(uuid.uuid4()),
        cluster=cluster,
        framework=framework,
        openai_endpoint=data["model_params"]["openai_endpoint"],
        prompt=json.dumps(extract_prompt(data["model_params"])),
        model=data["model_params"]["model"],
        timestamp_compute_request=timezone.now()
    )
    
    # Submit task
    if stream:
        task_response = await endpoint.submit_streaming_task(data, request.request_log_data.id)
    else:
        task_response = await endpoint.submit_task(data)

    # Update request log data
    request.request_log_data.task_uuid = task_response.task_id
    request.request_log_data.timestamp_compute_response = timezone.now()

    # Display error message if any
    if task_response.error_message:
        return await get_response(task_response.error_message, task_response.error_code, request)
    
    # If streaming, meaning that the StreamingHttpResponse object will be returned directly ...
    if stream:

        # Manually create access and request logs to database
        try:
            access_log = await create_access_log(request.access_log_data, None, 200)
            request.request_log_data.access_log = access_log
            _ = await create_request_log(request.request_log_data, "streaming_response_in_progress", 200)
        except Exception as e:
            return HttpResponse(json.dumps(f"Error: Could not save access and request logs: {e}"), status=500)

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
    is_valid, error_response, status_code = validate_streaming_request_security(request, max_content_length=150000)
    if not is_valid:
        # Try to extract task_id to record auth failure
        try:
            data = json.loads(decode_request_body(request))
            task_id = data.get('task_id')
            if task_id and status_code in [401, 403]:
                set_streaming_metadata(task_id, "auth_failure", "true", ttl=60)
                log.warning(f"Authentication failure recorded for streaming task {task_id}")
        except Exception:
            pass  # Don't fail the error response if we can't record the failure
        return JsonResponse(error_response, status=status_code)
    
    try:
        data = json.loads(decode_request_body(request))
        task_id = data.get('task_id')
        chunk_data = data.get('data')
        
        if chunk_data is None:
            return JsonResponse({"error": "Missing data"}, status=400)
        
        if '\n' in chunk_data:
            # Split batched chunks and store each one
            chunks = chunk_data.split('\n')
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
    is_valid, error_response, status_code = validate_streaming_request_security(request, max_content_length=15000)
    if not is_valid:
        # Try to extract task_id to record auth failure
        try:
            data = json.loads(decode_request_body(request))
            task_id = data.get('task_id')
            if task_id and status_code in [401, 403]:
                set_streaming_metadata(task_id, "auth_failure", "true", ttl=60)
                log.warning(f"Authentication failure recorded for streaming task {task_id}")
        except Exception:
            pass  # Don't fail the error response if we can't record the failure
        return JsonResponse(error_response, status=status_code)
    
    try:
        data = json.loads(decode_request_body(request))
        task_id = data.get('task_id')
        error = data.get('error')
        
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
    is_valid, error_response, status_code = validate_streaming_request_security(request, max_content_length=15000)
    if not is_valid:
        # Try to extract task_id to record auth failure
        try:
            data = json.loads(decode_request_body(request))
            task_id = data.get('task_id')
            if task_id and status_code in [401, 403]:
                set_streaming_metadata(task_id, "auth_failure", "true", ttl=60)
                log.warning(f"Authentication failure recorded for streaming task {task_id}")
        except Exception:
            pass  # Don't fail the error response if we can't record the failure
        return JsonResponse(error_response, status=status_code)
    
    try:
        data = json.loads(decode_request_body(request))
        task_id = data.get('task_id')
        
        # Mark as completed with automatic cleanup
        set_streaming_status(task_id, "completed")
        
        log.info(f"Completed streaming task: {task_id}")
        return JsonResponse({"status": "ok", "task_id": task_id})
        
    except Exception as e:
        log.error(f"Error receiving streaming done: {e}")
        return JsonResponse({"error": "Internal server error"}, status=500)


# Federated Inference (POST) - Chooses cluster/framework automatically
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

# Add URLs to the Ninja API
api.add_router("/", router)
