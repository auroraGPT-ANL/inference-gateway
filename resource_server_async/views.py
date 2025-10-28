from ninja import Query
from ninja.errors import HttpError
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
import uuid
import json
import asyncio
import time
from django.utils import timezone
from django.utils.text import slugify
from django.http import JsonResponse, StreamingHttpResponse

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
from utils.auth_utils import validate_access_token
from utils.pydantic_models.db_models import RequestLogPydantic, BatchLogPydantic, UserPydantic
from resource_server_async.utils import (
    validate_url_inputs, 
    validate_cluster_framework,
    extract_prompt, 
    validate_request_body,
    validate_batch_body,
    #validate_file_body,
    extract_group_uuids,
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
    get_streaming_metadata,
    validate_streaming_request_security,
    get_streaming_data_and_status_batch,
    process_streaming_completion_async,
    # Cache functions
    get_endpoint_from_cache,
    cache_endpoint,
    is_cached,
    remove_endpoint_from_cache,
    # Response functions
    get_response,
    # Streaming utilities
    prepare_streaming_task_data,
    create_streaming_response_headers,
    format_streaming_error_for_openai,
    create_access_log,
    # Metis utilities
    handle_metis_streaming_inference,
    update_metis_streaming_log
)
log.info("Utils functions loaded.")

# Django database
from resource_server.models import (
    Endpoint, 
    Log, 
    Batch, 
    FederatedEndpoint
)
from resource_server_async.models import RequestLog, BatchLog

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

    # Collect endpoints objects from the database
    try:
        endpoint_list = await sync_to_async(list)(Endpoint.objects.all())
    except Exception as e:
        return await get_response(f"Error: Could not access Endpoint database entries: {e}", 400, request)

    # Prepare the list of available frameworks and models
    all_endpoints = {"clusters": {}}
    try:

        # ===== ADD METIS CLUSTER MODELS =====
        # Fetch Metis status and add Metis models to the list
        metis_status, metis_error = await metis_utils.fetch_metis_status(use_cache=True)
        if not metis_error and metis_status:
            # Add Metis cluster entry
            all_endpoints["clusters"]["metis"] = {
                "base_url": "/resource_server/metis",
                "frameworks": {
                    "api": {
                        "models": [],
                        "endpoints": {
                            "chat": "/api/v1/chat/completions/"
                        }
                    }
                }
            }
            
            # Extract expert models from Metis status
            for model_key, model_info in metis_status.items():
                if model_info.get("status") == "Live":
                    experts = model_info.get("experts", [])
                    for expert in experts:
                        if isinstance(expert, str) and len(expert) > 0:
                            all_endpoints["clusters"]["metis"]["frameworks"]["api"]["models"].append(expert)
            
            # Sort Metis models alphabetically
            all_endpoints["clusters"]["metis"]["frameworks"]["api"]["models"] = \
                sorted(all_endpoints["clusters"]["metis"]["frameworks"]["api"]["models"])
        
        # ===== ADD GLOBUS COMPUTE CLUSTER MODELS =====
        # For each database endpoint entry ...
        for endpoint in endpoint_list:

            # Extract the list of allowed group UUIDs tied to the endpoint
            allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
            if len(error_message) > 0:
                log.error(error_message)
                return await get_response(error_message, 400, request)
    
            # If the user is allowed to see the endpoint ...
            # i.e. if (there is no restriction) or (if the user is at least part of one allowed groups) ...
            if len(allowed_globus_groups) == 0 or len(set(request.user_group_uuids).intersection(allowed_globus_groups)) > 0:

                # Add a new cluster dictionary entry if needed
                if not endpoint.cluster in all_endpoints["clusters"]:
                    all_endpoints["clusters"][endpoint.cluster] = {
                        "base_url": f"/resource_server/{endpoint.cluster}",
                        "frameworks": {}
                    }
                
                # Add a new framework dictionary entry if needed
                if not endpoint.framework in all_endpoints["clusters"][endpoint.cluster]["frameworks"]:
                    all_endpoints["clusters"][endpoint.cluster]["frameworks"][endpoint.framework] = {
                        "models": [],
                        "endpoints": {
                            "chat": f"/{endpoint.framework}/v1/chat/completions/",
                            "completion": f"/{endpoint.framework}/v1/completions/",
                            "embedding": f"/{endpoint.framework}/v1/embeddings/"
                        }
                    }

                # Add model
                all_endpoints["clusters"][endpoint.cluster]["frameworks"][endpoint.framework]["models"].append(endpoint.model)

        # Sort models alphabetically
        for cluster in all_endpoints["clusters"]:
            for framework in all_endpoints["clusters"][cluster]["frameworks"]:
                all_endpoints["clusters"][cluster]["frameworks"][framework]["models"] = \
                    sorted(all_endpoints["clusters"][cluster]["frameworks"][framework]["models"])

    # Error message if something went wrong while building the endpoint list
    except Exception as e:
        return await get_response(f"Error: Could not generate list of frameworks and models from database: {e}", 400, request)

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
        async for batch in Batch.objects.filter(username=request.auth.username, status__in=["pending", "running"]):
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

    # Build the requested endpoint slug
    endpoint_slug = slugify(" ".join([cluster, framework, batch_data["model"].lower()]))
    
    # Pull the targetted endpoint from database to check if user is permitted to run this model
    try:
        endpoint = await sync_to_async(Endpoint.objects.get)(endpoint_slug=endpoint_slug)
    except Endpoint.DoesNotExist:
        return await get_response(f"Error: Endpoint {endpoint_slug} does not exist.", 400, request)
    except Exception as e:
        return await get_response(f"Error: Could not extract endpoint: {e}", 400, request)
    
    # Extract the list of allowed group UUIDs tied to the targetted endpoint
    allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
    if len(error_message) > 0:
        return await get_response(error_message, 401, request)
    
    # Block access if the user is not a member of at least one of the required groups
    if len(allowed_globus_groups) > 0: # This is important to check if there is a restriction
        if len(set(request.user_group_uuids).intersection(allowed_globus_groups)) == 0:
            return await get_response(f"Permission denied to endpoint {endpoint_slug}.", 401, request)
        
    # Make sure the endpoint has batch UUIDs
    if len(endpoint.batch_endpoint_uuid) == 0 or len(endpoint.batch_function_uuid) == 0:
        return await get_response(f"Endpoint {endpoint_slug} does not have batch enabled.", 501, request)

    # Error if an ongoing batch already exists with the same input_file
    # TODO: More checks here to make sure we don't duplicate batches?
    #       Do we allow multiple batches on the same file on different clusters?
    try:
        async for batch in Batch.objects.filter(input_file=batch_data["input_file"]):
            if not batch.status in ["failed", "completed"]:
                error_message = f"Error: Input file {batch_data['input_file']} already used by ongoing batch {batch.batch_id}."
                return await get_response(error_message, 400, request)
    except Batch.DoesNotExist:
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
        async for batch in BatchLog.objects.filter(username=request.auth.username):

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
                    "batch_id": str(batch.batch_id),
                    "cluster": batch.cluster,
                    "framework": batch.framework,
                    "input_file": batch.input_file,
                    "created_at": str(batch.created_at),
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
        batch = await sync_to_async(BatchLog.objects.get)(batch_id=batch_id)
    except BatchLog.DoesNotExist:
        return await get_response(f"Error: Batch {batch_id} does not exist.", 400, request)
    except Exception as e:
        return await get_response(f"Error: Could not access Batch {batch_id} from database: {e}", 400, request)

    # Make sure user has permission to access this batch_id
    try:
        if not request.auth.username == batch.username:
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
        batch = await sync_to_async(BatchLog.objects.get)(batch_id=batch_id)
    except BatchLog.DoesNotExist:
        return await get_response(f"Error: Batch {batch_id} does not exist.", 400, request)
    except Exception as e:
        return await get_response(f"Error: Could not access Batch {batch_id} from database: {e}", 400, request)

    # Make sure user has permission to access this batch_id
    try:
        if not request.auth.username == batch.username:
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
    
    # Strip the last forward slash is needed
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

    # ===== METIS CLUSTER HANDLING =====
    # Metis uses direct API calls instead of Globus Compute
    if cluster == "metis":
        return await handle_metis_inference(request, data, stream, endpoint_slug, cluster, framework)
    
    # ===== GLOBUS COMPUTE CLUSTER HANDLING =====
    # Try to get endpoint from Redis cache first
    endpoint = get_endpoint_from_cache(endpoint_slug)
    if endpoint is None:
        # If not in cache, fetch from DB asynchronously
        try:
            get_endpoint_async = sync_to_async(Endpoint.objects.get, thread_sensitive=True)
            endpoint = await get_endpoint_async(endpoint_slug=endpoint_slug)

            # Store the fetched endpoint in Redis cache
            cache_endpoint(endpoint_slug, endpoint)

        except Endpoint.DoesNotExist:
            return await get_response(f"Error: The requested endpoint {endpoint_slug} does not exist.", 400, request)
        except Exception as e:
            return await get_response(f"Error: Could not extract endpoint {endpoint_slug}: {e}", 400, request)

    # Use the endpoint data (either from cache or freshly fetched)
    try:
        data["model_params"]["api_port"] = endpoint.api_port
    except Exception as e:
         # If there was an error processing the data (e.g., attribute missing),
         # it might be safer to remove it from the cache to force a refresh on next request.
        remove_endpoint_from_cache(endpoint_slug)
        return await get_response(f"Error processing endpoint data for {endpoint_slug}: {e}", 400, request)

    # Extract the list of allowed group UUIDs tied to the targetted endpoint
    allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
    if len(error_message) > 0:
        # Remove from cache if group extraction fails, as the cached data might be problematic
        remove_endpoint_from_cache(endpoint_slug)
        return await get_response(error_message, 401, request)

    # Block access if the user is not a member of at least one of the required groups
    if len(allowed_globus_groups) > 0: # This is important to check if there is a restriction
        if len(set(request.user_group_uuids).intersection(allowed_globus_groups)) == 0:
            return await get_response(f"Permission denied to endpoint {endpoint_slug}.", 401, request)

    # Get Globus Compute client and executor
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests
    # NOTE: Do not include endpoint_id argument, otherwise it will cache multiple executors
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
        gce = globus_utils.get_compute_executor(client=gcc)
    except Exception as e:
        return await get_response(f"Error: Could not get the Globus Compute client or executor: {e}", 500, request)
    
    # Query the status of the targetted Globus Compute endpoint
    # NOTE: Do not await here, cache the "first" request to avoid too-many-requests Globus error
    endpoint_status, error_message = globus_utils.get_endpoint_status(
        endpoint_uuid=endpoint.endpoint_uuid, client=gcc, endpoint_slug=endpoint_slug
    )
    if len(error_message) > 0:
        return await get_response(error_message, 500, request)
        
    # Check if the endpoint is running and whether the compute resources are ready (worker_init completed)
    if not endpoint_status["status"] == "online":
        return await get_response(f"Error: Endpoint {endpoint_slug} is offline.", 503, request)
    resources_ready = int(endpoint_status["details"]["managers"]) > 0
    
    # If the compute resource is not ready (if node not acquired or if worker_init phase not completed)
    if not resources_ready:

        # If a user already triggered the model (model currently loading) ...
        cache_key = f"endpoint_triggered:{endpoint_slug}"
        if is_cached(cache_key, create_empty=False):

            # Get model name
            try:
                model = data["model_params"]["model"]
            except:
                model = "Unknown"
            
            # Send an error to avoid overloading the Globus Compute endpoint
            # This also reduces memory footprint on the API application
            error_message = f"Error: Endpoint {endpoint_slug} online but not ready to receive tasks. "
            error_message += "Please try again later."
            return await get_response(error_message, 503, request)

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
    
    if stream:
        # Handle streaming request
        return await handle_streaming_inference(gce, endpoint, data, resources_ready, request, endpoint_slug=endpoint_slug)
    else:
        # Handle non-streaming request (original logic)
        # Submit task and wait for result
        result, task_uuid, error_message, error_code = await globus_utils.submit_and_get_result(
            gce, endpoint.endpoint_uuid, endpoint.function_uuid, resources_ready, data=data, endpoint_slug=endpoint_slug
        )
        request.request_log_data.timestamp_compute_response = timezone.now()
        if len(error_message) > 0:
            return await get_response(error_message, error_code, request)
        
        # Assign task UUID if the execution did not fail
        request.request_log_data.task_uuid = task_uuid

        # Return Globus Compute results
        return await get_response(result, 200, request)
    

async def handle_streaming_inference(gce, endpoint, data, resources_ready, request, endpoint_slug=None):
    """Handle streaming inference using integrated Django streaming endpoints with comprehensive metrics"""
    
    # Generate unique task ID for streaming
    stream_task_id = str(uuid.uuid4())
    streaming_start_time = time.time()
    
    # Prepare streaming data payload using utility function
    data = prepare_streaming_task_data(data, stream_task_id)
    
    # Submit task to Globus Compute (same logic as non-streaming)
    try:
        # Assign endpoint UUID to the executor (same as submit_and_get_result)
        gce.endpoint_id = endpoint.endpoint_uuid
        
        # Submit Globus Compute task and collect the future object (same as submit_and_get_result)
        future = gce.submit_to_registered_function(endpoint.function_uuid, args=[data])
        
        # Wait briefly for task to be registered with Globus (like submit_and_get_result does)
        # This allows the task_uuid to be populated without waiting for full completion
        try:
            asyncio_future = asyncio.wrap_future(future)
            # Wait just long enough for task registration (not full completion)
            await asyncio.wait_for(asyncio.shield(asyncio_future), timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # Timeout/cancellation is expected - we just want task registration, not completion
            pass
        except Exception:
            # Other exceptions don't prevent us from getting task_uuid
            pass
        
        # Get task_id from the future (should be available after brief wait)
        task_uuid = globus_utils.get_task_uuid(future)
        
    except Exception as e:
        return await get_response(f"Error: Could not submit streaming task: {e}", 500, request)
    
    # Cache the endpoint slug to tell the application that a user already submitted a request to this endpoint
    if endpoint_slug:
        cache_key = f"endpoint_triggered:{endpoint_slug}"
        ttl = 600 # 10 minutes
        try:
            cache.set(cache_key, True, ttl)
        except Exception as e:
            log.warning(f"Failed to cache endpoint_triggered:{endpoint_slug}: {e}")
    
    # Create initial log entry and get the ID for later updating
    request.request_log_data.result = "streaming_response_in_progress"
    request.request_log_data.timestamp_compute_response = timezone.now()
    
    # Set task_uuid in database data
    if task_uuid:
        request.request_log_data.task_uuid = str(task_uuid)
        log.info(f"Streaming request task UUID: {task_uuid}")
    else:
        log.warning("No task UUID captured for streaming request")
        request.request_log_data.task_uuid = None

    # Create AccessLog database entry
    request.access_log_data.status_code = 200
    try:
        access_log = await create_access_log(request.access_log_data, "", 200)
    except Exception as e:
        return await get_response(f"Error: Could not create AccessLog entry: {e}", 500, request)
    
    # Create initial log entry
    try:
        request.request_log_data.access_log = access_log
        db_log = RequestLog(**request.request_log_data.model_dump())
        await sync_to_async(db_log.save, thread_sensitive=True)()
        log_id = db_log.id
        log.info(f"Created initial streaming log entry {log_id} for task {task_uuid}")
    except Exception as e:
        log.error(f"Error creating initial streaming log entry: {e}")
        log_id = None
    
    # Start background processing for metrics collection (fire and forget)
    if log_id:
        asyncio.create_task(process_streaming_completion_async(
            task_uuid, stream_task_id, log_id, future, streaming_start_time,
            extract_prompt(data["model_params"]) if data.get("model_params") else None
        ))
    
    # Create simple SSE streaming response  
    async def sse_generator():
        """Simple SSE generator with fast Redis polling - P0 OPTIMIZED with pipeline batching"""
        try:
            max_wait_time = 300  # 5 minutes total timeout
            start_time = time.time()
            last_chunk_index = 0
            first_data_timeout = 30  # 30 seconds to receive first chunk or status
            first_data_received = False
            last_chunk_time = None  # Track when we last received a chunk
            no_new_data_timeout = 5  # 5 seconds with no new chunks = assume completion (fallback if /done not called)
            
            while time.time() - start_time < max_wait_time:
                # P0 OPTIMIZATION: Get status, chunks, and error in a single Redis round-trip
                chunks, status, error_message = get_streaming_data_and_status_batch(stream_task_id)
                
                # Check if we've received any data (chunks or status)
                if (chunks and len(chunks) > 0) or status:
                    first_data_received = True
                
                # PRIORITY 1: Fast auth failure check (immediate break)
                auth_failure = get_streaming_metadata(stream_task_id, "auth_failure")
                if auth_failure:
                    error_msg = {
                        "object": "error",
                        "message": "Streaming authentication failed: Remote compute endpoint could not authenticate with streaming API. Check INTERNAL_STREAMING_SECRET configuration.",
                        "type": "AuthenticationError",
                        "param": None,
                        "code": 401
                    }
                    log.error(f"Streaming task {stream_task_id} - authentication failure detected")
                    set_streaming_status(stream_task_id, "error")
                    set_streaming_error(stream_task_id, error_msg.get("message"))
                    yield f"data: {json.dumps(error_msg)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
                
                # PRIORITY 2: Early timeout check (no data after 30s)
                elapsed_time = time.time() - start_time
                if not first_data_received and elapsed_time > first_data_timeout:
                    error_msg = {
                        "object": "error",
                        "message": f"Streaming task timed out: No data received from compute endpoint after {first_data_timeout} seconds. This may indicate network or endpoint configuration issues.",
                        "type": "StreamingTimeoutError",
                        "param": None,
                        "code": 504
                    }
                    log.error(f"Streaming task {stream_task_id} timed out - no data received after {first_data_timeout}s")
                    set_streaming_status(stream_task_id, "error")
                    set_streaming_error(stream_task_id, error_msg.get("message"))
                    yield f"data: {json.dumps(error_msg)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
                
                # PRIORITY 3: Handle error status (send error then break)
                if status == "error":
                    if error_message:
                        # Format and send the error in OpenAI streaming format
                        formatted_error = format_streaming_error_for_openai(error_message)
                        yield formatted_error
                    # Send [DONE] after error to properly terminate the stream
                    yield "data: [DONE]\n\n"
                    break
                
                # PRIORITY 4: Process ALL pending chunks FIRST (drain the queue)
                # This ensures we don't miss chunks that arrived just before /done
                if chunks and len(chunks) > last_chunk_index:
                    # Send all new chunks at once
                    for i in range(last_chunk_index, len(chunks)):
                        chunk = chunks[i]
                        # Only send actual vLLM content chunks (skip our custom control messages)
                        if chunk.startswith('data: '):
                            # Send the vLLM chunk as-is
                            yield f"{chunk}\n\n"
                        
                        last_chunk_index = i + 1
                    
                    # Update last chunk time
                    last_chunk_time = time.time()
                
                # PRIORITY 5: Check completion status AFTER processing chunks
                # This prevents race condition where /done arrives before final chunks
                if status == "completed":
                    # One final check for any remaining chunks that arrived during processing
                    final_chunks, _, _ = get_streaming_data_and_status_batch(stream_task_id)
                    if final_chunks and len(final_chunks) > last_chunk_index:
                        for i in range(last_chunk_index, len(final_chunks)):
                            chunk = final_chunks[i]
                            if chunk.startswith('data: '):
                                yield f"{chunk}\n\n"
                    
                    log.info(f"Streaming task {stream_task_id} - status is completed, sending [DONE]")
                    yield "data: [DONE]\n\n"
                    break
                
                # PRIORITY 6 (FALLBACK): No new data timeout
                # This handles cases where remote function sent all data but didn't call /done endpoint
                # Only check this if we haven't seen a "completed" status
                if last_chunk_time is not None and (time.time() - last_chunk_time) > no_new_data_timeout:
                    log.warning(f"Streaming task {stream_task_id} - no new chunks for {no_new_data_timeout}s, assuming completion (done signal was not received)")
                    yield "data: [DONE]\n\n"
                    # Set completed status for cleanup
                    set_streaming_status(stream_task_id, "completed")
                    break
                # Fast polling - 25ms
                await asyncio.sleep(0.025)
                
        except Exception as e:
            # For exceptions, just end without error message to maintain OpenAI compatibility
            log.error(f"Exception in SSE generator for task {stream_task_id}: {e}")
    
    # Create streaming response
    response = StreamingHttpResponse(
        streaming_content=sse_generator(),
        content_type='text/event-stream'
    )
    
    # Set headers for SSE using utility function
    headers = create_streaming_response_headers()
    for key, value in headers.items():
        response[key] = value
    
    return response


async def handle_metis_inference(request, data, stream, endpoint_slug, cluster, framework):
    """Handle inference requests to Metis cluster using direct API calls.
    
    Metis models are already deployed behind an API, so we:
    1. Fetch model status from Metis status endpoint
    2. Find matching model in 'experts' list
    3. Make direct API call (no Globus Compute involved)
    """
    
    # Get requested model name
    requested_model = data["model_params"]["model"]
    log.info(f"Metis inference request for model: {requested_model}")
    
    # Initialize the request log data for the database entry
    # Set both timestamps initially to avoid null constraint violations
    # timestamp_compute_response will be updated when we get actual response
    current_time = timezone.now()
    request.request_log_data = RequestLogPydantic(
        id=str(uuid.uuid4()),
        cluster=cluster,
        framework=framework,
        openai_endpoint=data["model_params"]["openai_endpoint"],
        prompt=json.dumps(extract_prompt(data["model_params"])),
        model=requested_model,
        timestamp_compute_request=current_time,
        timestamp_compute_response=current_time  # Initialize to avoid null errors
    )
    
    # Fetch Metis status
    metis_status, error_msg = await metis_utils.fetch_metis_status(use_cache=True)
    if error_msg:
        # Update response timestamp for error case
        request.request_log_data.timestamp_compute_response = timezone.now()
        return await get_response(f"Error fetching Metis status: {error_msg}", 503, request)
    
    # Find matching model in Metis status (returns endpoint_id for API token lookup)
    model_info, endpoint_id, error_msg = metis_utils.find_metis_model(metis_status, requested_model)
    if error_msg:
        # Update response timestamp for error case
        request.request_log_data.timestamp_compute_response = timezone.now()
        return await get_response(error_msg, 404, request)
    
    # Check if the model is Live
    if model_info.get("status") != "Live":
        # Update response timestamp for error case
        request.request_log_data.timestamp_compute_response = timezone.now()
        return await get_response(
            f"Error: Model '{requested_model}' is not currently live on Metis. Status: {model_info.get('status')}",
            503,
            request
        )
    
    # Use validated request data as-is (already in OpenAI format)
    # Only update the stream parameter to match the request
    api_request_data = {**data["model_params"]}
    api_request_data["stream"] = stream
    # Remove internal field that shouldn't be sent to Metis
    api_request_data.pop("openai_endpoint", None)
    api_request_data.pop("api_port", None)
    
    log.info(f"Making Metis API call for model {requested_model} (stream={stream}, endpoint={endpoint_id})")
    
    # Make the API call to Metis
    if stream:
        # Handle streaming request - create AccessLog first
        request.access_log_data.status_code = 200
        try:
            access_log = await create_access_log(request.access_log_data, "", 200)
        except Exception as e:
            request.request_log_data.timestamp_compute_response = timezone.now()
            return await get_response(f"Error: Could not create AccessLog entry: {e}", 500, request)
        
        return await handle_metis_streaming_inference(
            request, model_info, endpoint_id, api_request_data, requested_model, access_log
        )
    else:
        # Non-streaming request
        result, status_code, error_msg = await metis_utils.call_metis_api(
            model_info,
            endpoint_id,
            api_request_data,
            stream=False
        )
        
        request.request_log_data.timestamp_compute_response = timezone.now()
        
        if error_msg:
            return await get_response(error_msg, status_code, request)
        
        # Metis API returns results directly (no task UUID in this paradigm)
        request.request_log_data.task_uuid = None
        
        # Return Metis API results
        return await get_response(result, status_code, request)


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
        gce, selected_endpoint["endpoint_uuid"], selected_endpoint["function_uuid"], resources_ready, data=data
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
