import uuid
import json
import logging
import redis
import time
import asyncio
import re
import secrets
import hmac
from enum import Enum
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from ninja import FilterSchema
from utils.pydantic_models.openai_chat_completions import OpenAIChatCompletionsPydantic
from utils.pydantic_models.openai_completions import OpenAICompletionsPydantic
from utils.pydantic_models.openai_embeddings import OpenAIEmbeddingsPydantic
from utils.pydantic_models.batch import BatchPydantic
from utils.pydantic_models.db_models import AccessLogPydantic, RequestLogPydantic, BatchLogPydantic
from rest_framework.exceptions import ValidationError
from asgiref.sync import sync_to_async
from asyncache import cached as asynccached
from cachetools import TTLCache
from utils.globus_utils import (
    submit_and_get_result,
    get_endpoint_status,
    get_batch_status,
    get_compute_client_from_globus_app,
    get_compute_executor,
    get_endpoint_status
)
from resource_server.models import ModelStatus, Endpoint
from resource_server_async.models import (AccessLog, RequestLog, BatchLog, RequestMetrics, BatchMetrics)

log = logging.getLogger(__name__) # Add logger

# --- Removed Configuration Loading ---

# Constants are now loaded from settings.py
ALLOWED_FRAMEWORKS = settings.ALLOWED_FRAMEWORKS
ALLOWED_OPENAI_ENDPOINTS = settings.ALLOWED_OPENAI_ENDPOINTS
ALLOWED_CLUSTERS = settings.ALLOWED_CLUSTERS
ALLOWED_QSTAT_ENDPOINTS = settings.ALLOWED_QSTAT_ENDPOINTS

# Batch list filter
class BatchStatusEnum(str, Enum):
    pending = 'pending'
    running = 'running'
    failed = 'failed'
    completed = 'completed'
class BatchListFilter(FilterSchema):
    status: BatchStatusEnum = None


# Exception to raise in case of errors
class ResourceServerError(Exception):
    pass


# Is cached
def is_cached(key: str, create_empty: bool = False, ttl: int = 30) -> bool:
    """
        Returns True if key exists in the cache, False otherwise.
        If create_empty=True, a non-existing key will be created with a "" value (function will still returns False).
    """
     # If the key does not exist ...
    if cache.get(key) is None:

        # Create an empty entry if needed
        if create_empty:
            cache.set(key, "", ttl)

        # Return False since the key did not exist at first
        return False

    # Return True if the key already exists
    else:
        return True


# Validate cluster and framework
def validate_cluster_framework(cluster: str, framework: str):

    # Error message if cluster not available
    if not cluster in ALLOWED_CLUSTERS:
        return f"Error: {cluster} cluster not supported. Currently supporting {ALLOWED_CLUSTERS}."
    
    # Error message if framework not available
    if not framework in ALLOWED_FRAMEWORKS.get(cluster, []): # Use .get for safety
        return f"Error: {framework} framework not supported for cluster {cluster}. Currently supporting {ALLOWED_FRAMEWORKS.get(cluster, [])}."

    # No error message if the inputs are valid
    return ""


# Validate URL inputs
# TODO: Incorporate re-usable validate_cluster_framework function
def validate_url_inputs(cluster: str, framework: str, openai_endpoint: str):
    """Validate user inputs from POST requests."""

    # Error message if cluster not available
    if not cluster in ALLOWED_CLUSTERS:
        return f"Error: {cluster} cluster not supported. Currently supporting {ALLOWED_CLUSTERS}."
    
    # Error message if framework not available
    if not framework in ALLOWED_FRAMEWORKS.get(cluster, []): # Use .get for safety
        return f"Error: {framework} framework not supported for cluster {cluster}. Currently supporting {ALLOWED_FRAMEWORKS.get(cluster, [])}."
    
    # Error message if openai endpoint not available
    if not openai_endpoint in ALLOWED_OPENAI_ENDPOINTS.get(cluster, []): # Use .get for safety
        return f"Error: {openai_endpoint} openai endpoint not supported for cluster {cluster}. Currently supporting {ALLOWED_OPENAI_ENDPOINTS.get(cluster, [])}."

    # No error message if the inputs are valid
    return ""


# Extract user prompt
def extract_prompt(model_params):
    """Extract the user input text from the requested model parameters."""

    # Completions
    if "prompt" in model_params:
        return model_params["prompt"]
        
    # Chat completions
    elif "messages" in model_params:
        return model_params["messages"]
        
    # Embeddings
    elif "input" in model_params:
        return model_params["input"]
        
    # Undefined
    return "default"


# Validate request body
# TODO: Use validate_body to reduce code duplication
def validate_request_body(request, openai_endpoint):
    """Build data dictionary for inference request if user inputs are valid."""
        
    # Select the appropriate pydantic model for data validation
    if "chat/completions" in openai_endpoint:
        pydantic_class = OpenAIChatCompletionsPydantic
    elif "completion" in openai_endpoint:
        pydantic_class = OpenAICompletionsPydantic
    elif "embeddings" in openai_endpoint:
        pydantic_class = OpenAIEmbeddingsPydantic
    else:
        return {"error": f"Error: {openai_endpoint} endpoint not supported."}
    
    # Decode and validate request body
    model_params = validate_body(request, pydantic_class)
    if "error" in model_params.keys():
        return model_params

    # Add the 'url' parameter to model_params
    model_params['openai_endpoint'] = openai_endpoint

    # Build request data if nothing wrong was caught
    return {"model_params": model_params}


# Validate batch body
def validate_batch_body(request):
    """Build data dictionary for inference batch request if user inputs are valid."""
    return validate_body(request, BatchPydantic)


# Helper function to safely decode request body
def decode_request_body(request):
    """
    Safely decode request.body to string, handling both bytes and str.
    
    Django Ninja can return either bytes or str depending on context.
    
    Args:
        request: Django request object
    
    Returns:
        str: Decoded body as string
    """
    body = request.body
    if isinstance(body, bytes):
        return body.decode("utf-8")
    return body


# Validate body
def validate_body(request, pydantic_class):
    """Validate body data from incoming user requests against a given pydantic model."""
                
    # Decode request body into a dictionary
    try:
        params = json.loads(decode_request_body(request))
    except Exception as e:
        return {"error": f"Error: Request body cannot be decoded: {e}"}

    # Send an error if the input data is not valid
    try:
        _ = pydantic_class(**params)
    except ValidationError as e:
        return {"error": f"Error: Could not validate data: {e}"}
    except Exception as e:
        return {"error": f"Error: Data validation went wrong with pydantic model: {e}"}

    # Return decoded request body data if nothing wrong was caught
    return params


# Extract group UUIDs from an allowed_globus_groups model field
def extract_group_uuids(globus_groups):
    """Extract group UUIDs from an allowed_globus_groups model field."""

    # Make sure the globus_groups argument is a string
    if not isinstance(globus_groups, str):
        return [], "Error: globus_groups must be a string like 'group1-name:group1-uuid; group2-name:group2-uuid; ...' "

    # Return empty list with no error message if no group restriction was provided
    if len(globus_groups) == 0:
        return [], ""

    # Declare the list of group UUIDs
    group_uuids = []

    # Append each UUID to the list
    try:
        for group_name_uuid in globus_groups.split(";"):
            group_uuids.append(group_name_uuid.split(":")[-1])
    except Exception as e:
        return [], f"Error: Exception while extracting Globus Group UUIDs. {e}"
    
    # Make sure that all UUID strings have the UUID format
    for uuid_to_test in group_uuids:
        try:
            uuid_obj = uuid.UUID(uuid_to_test).version
        except Exception as e:
            return [], f"Error: Could not extract UUID format from the database. {e}"
    
    # Return the list of group UUIDs
    return group_uuids, ""


# Get qstat details
async def get_qstat_details(cluster, gcc=None, gce=None, timeout=60):
    """
    Collect details on all jobs running/submitted on a given cluster.
    Here return the error message instead of raising exceptions to 
    make sure the outcome gets cached.
    Returns result, task_uuid, error_message, error_code
    """

    # Redis cache key
    cache_key = f"qstat_details:{cluster}"
    
    # Try to get qstat details from Redis
    try:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result
    except Exception as e:
        log.warning(f"Redis cache error for endpoint status: {e}")

    # Get Globus Compute client and executor
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests
    if gcc is None:
        try:
            gcc = get_compute_client_from_globus_app()
        except Exception as e:
            return None, None, f"Error: Could not get the Globus Compute client: {e}", 500
    if gce is None:
        try:
            # NOTE: Do not include endpoint_id argument, otherwise it will cache multiple executors
            gce = get_compute_executor(client=gcc)
        except Exception as e:
            return None, None, f"Error: Could not get the Globus Compute executor: {e}", 500
    
    # Gather the qstat endpoint info using the loaded config
    qstat_config = ALLOWED_QSTAT_ENDPOINTS.get(cluster)
    if not qstat_config:
        return None, None, f"Error: no qstat endpoint configuration exists for cluster {cluster}.", 500

    endpoint_slug = f"{cluster}/jobs"
    endpoint_uuid = qstat_config["endpoint_uuid"]
    function_uuid = qstat_config["function_uuid"]

    # Get the status of the qstat endpoint
    # NOTE: Do not await here, cache the "first" request to avoid too-many-requests Globus error
    endpoint_status, error_message = get_endpoint_status(
        endpoint_uuid=endpoint_uuid, client=gcc, endpoint_slug=endpoint_slug
    )
    if len(error_message) > 0:
        return None, None, error_message, 500
        
    # Return error message if endpoint is not online
    if not endpoint_status["status"] == "online":
        return None, None, f"Error: Endpoint {endpoint_slug} is offline.", 500
    
    # Submit task and wait for result
    result, task_uuid, error_message, error_code = await submit_and_get_result(
        gce, endpoint_uuid, function_uuid, True, timeout=timeout
    )
    if len(error_message) > 0:
        return None, task_uuid, error_message, error_code
    
    # Try to refine the status of each endpoint (in case Globus Compute managers are lost)
    try:

        # For each running endpoint ...
        result = json.loads(result)
        for i, running in enumerate(result["running"]):

            # If the model is in a "running" state (not "starting")
            if running["Model Status"] == "running":

                # Get compute endpoint ID from database
                running_framework = running["Framework"]
                running_model = running["Models"].split(",")[0]
                running_cluster = running["Cluster"]
                endpoint_slug = slugify(" ".join([running_cluster, running_framework, running_model]))
                endpoint = await sync_to_async(Endpoint.objects.get)(endpoint_slug=endpoint_slug)
                endpoint_uuid = endpoint.endpoint_uuid

                # Turn the model to "disconnected" if managers are lost
                endpoint_status, error_message = get_endpoint_status(
                    endpoint_uuid=endpoint_uuid, client=gcc, endpoint_slug=endpoint_slug
                )
                if int(endpoint_status["details"].get("managers", 0)) == 0:
                    result["running"][i]["Model Status"] = "disconnected"

        # Turn the result back to a string
        result = json.dumps(result)

    except Exception as e:
        log.warning(f"Failed to refine qstat model status: {e}")

    # Cache the result for 60 seconds
    try:
        cache.set(cache_key, [result, task_uuid, "", 200], 60)
    except Exception as e:
        log.warning(f"Failed to cache endpoint status: {e}")

    # Return qstat result without error_message
    return result, task_uuid, "", 200


# Update batch status result
async def update_batch_status_result(batch, cross_check=False):
    """
    From a database Batch object, query batch status from Globus
    if necessary, update the "status" and "result" fields in the
    database, and return the batch details.

    Arguments
    ---------
        batch: batch object from the Batch database model
        cross_check: If True, will cross check Globus status with qstat function

    Returns
    -------
        status, result, "", 200 OR "", "", error_message, error_code
    """

    # Skip all of the Globus task status check if the batch already completed or failed
    if batch.status in ["completed", "failed"]:
        return batch.status, batch.result, "", 200

    # Get the Globus batch status response
    status_response, error_message, code = get_batch_status(batch.globus_task_uuids)

    # If there is an error when recovering Globus tasks status/results ...
    if len(error_message) > 0:

        # Mark the batch as failed if the function execution failed
        if "TaskExecutionFailed" in error_message:
            try:
                batch.status = "failed"
                batch.access_log.error = error_message
                await update_database(db_object=batch)
                await update_database(db_object=batch.access_log)
                return batch.status, batch.result, "", 200
            except Exception as e:
                return "", "", f"Error: Could not update batch status in database: {e}", 400
            
        # Return error message
        return "", "", error_message, code
    
    # Parse Globus batch status response
    try:
        status_response_values = list(status_response.values())
        pending_list = [status["pending"] for status in status_response_values]
        status_list = [status["status"] for status in status_response_values]
    except Exception as e:
        return "", "", f"Error: Could not parse get_batch_status response for status: {e}", 400
    
    # Collect latest batch status
    try:

        # If Globus server claims that the tasks are still pending ...
        # TODO: We currently need to do extra checks since the AMQP server does not 
        #       communicate node failure and endpoint restarts to the Globus server.
        #       This means tasks can be lost and will always be flagged as "pending".
        #       Globus has an open ticket for this issue so the below measure is temporary
        if pending_list.count(True) > 0:
            if cross_check:
                batch_status = await cross_check_status(batch)
                if batch_status == "pending" or batch_status == "running":
                    batch.in_progress_at = timezone.now()
                elif batch_status == "failed":
                    batch.failed_at = timezone.now()
                    batch.access_log.error = "Error: Globus task lost. Likely due to node failure or endpoint restart."
            else:
                batch_status = batch.status

        # Completed
        elif status_list.count("success") == len(status_list):
            batch_status = "completed"
            batch.completed_at = timezone.now()

        # Failed
        else:
            batch_status = "failed"
            batch.failed_at = timezone.now()

    # Error if something went wrong while parsing the batch status response
    except Exception as e:
        return "", "", f"Error: Could not define batch status: {e}", 400
    
    # Update batch status in the database
    try:
        batch.status = batch_status
        await update_database(db_object=batch)
        await update_database(db_object=batch.access_log)
    except Exception as e:
        return "", "", f"Error: Could not update batch {batch.batch_id} status in database: {e}", 400
    
    # If batch result is available ...
    if batch_status == "completed":

        # Parse Globus batch status response to extract result
        try:
            result_list = [status["result"] for status in status_response_values]
            batch_result = ",".join(result_list) + ","
            batch_result = batch_result[:-1]

        # Error if something went wrong while parsing the batch status response
        except Exception as e:
            return "", "", f"Error: Could not parse get_batch_status response for result: {e}", 400

        # Update batch result in the database and upsert BatchMetrics
        try:
            batch.result = batch_result
            await update_database(db_object=batch)
            await update_database(db_object=batch.access_log)

            # Try to parse metrics summary from result if available
            total_tokens = None
            num_responses = None
            response_time_sec = None
            throughput = None
            try:
                result_data = json.loads(batch_result)
                if isinstance(result_data, dict) and 'metrics' in result_data:
                    metrics = result_data.get('metrics') or {}
                    total_tokens = metrics.get('total_tokens')
                    num_responses = metrics.get('num_responses')
                    response_time_sec = metrics.get('response_time_sec')
                    throughput = metrics.get('throughput_tokens_per_sec')
            except Exception:
                pass
            try:
                await _upsert_batch_metrics(
                    batch_obj=batch,
                    total_tokens=total_tokens,
                    num_responses=num_responses,
                    response_time_sec=response_time_sec,
                    throughput_tokens_per_sec=throughput,
                )
            except Exception as e:
                log.error(f"Error upserting BatchMetrics: {e}")
        except Exception as e:
            return "", "", f"Error: Could not update batch {batch.batch_id} result in database: {e}", 400

    # Return the new status if nothing went wrong
    return batch.status, batch.result, "", 200


# Cross check status
# TODO: Remove this function once Globus status includes "task lost"
async def cross_check_status(batch):
    """
    This verifies whether a Globus task is pending or lost due to an endpoint
    restart or a compute node failure. This is not 100% accurate, but it serves
    as a temporary improvement while Globus addresses the open ticket on improving
    the communication between Globus and AMQP when tasks are lost.
    Returns: status
    """

    # Get Globus Compute client and executor
    try:
        gcc = get_compute_client_from_globus_app()
        gce = get_compute_executor(client=gcc)
    except Exception as e:
        return batch.status
    
    # Collect (qstat) details on the jobs running/queued on the cluster
    qstat_result, _, error_message, _ = await get_qstat_details(batch.cluster, gcc, gce, timeout=60)

    # Preserve current status if no further investigation can be done
    if len(error_message) > 0:
        return batch.status
    try:
        qstat_result = json.loads(qstat_result)
    except Exception as e:
        return batch.status
    
    # Attempt to parse qstat_result
    try:

        # Collect batch ids that are running
        running_batch_ids = []
        for running in qstat_result["private-batch-running"]:
            if "Batch ID" in running:
                running_batch_ids.append(running["Batch ID"])
        nb_running_batches = len(qstat_result["private-batch-running"])

        # Collect the number of queued batches
        nb_queued_batches = len(qstat_result["private-batch-queued"])
        
        # Set status to "running" if an HPC job is running for the targetted batch
        if str(batch.batch_id) in running_batch_ids:
            return "running"
            
        # Set status to "failed" if previous status was "running", but no HPC job exists for it anymore
        if not batch.batch_id in running_batch_ids and batch.status == "running":
            return "failed"
        
        # Set status to "failed" if batch is pending, but nothing is queued or running
        # Do not fail just because nothing is in the HPC queue. If a batch is running,
        # it is possible that the targetted batch is in the Globus queue in the cloud.
        if batch.status == "pending" and nb_running_batches == 0 and nb_queued_batches == 0:
            if (timezone.now() - batch.created_at).seconds > 10:
                return "failed"

    # Preserve current status if no further investigation can be done    
    except Exception as e:
        return batch.status
    
    # Return same status if no special case was catched
    return batch.status


# Update database
async def update_database(db_Model=None, db_data=None, db_object=None, return_obj=False):
    """
    Create new entry in the database or save the modification of existing entry.
    It returns the database object.
    
    Arguments
    ---------
        db_Model: Django database model from models.py (e.g. AccessLog, RequestLog)
        db_data (dict): Data that will be ingested into the database model
        db_object: Database model object
        return_obj (bool): Whether the database object should be returned

    Notes
    -----
        If db_object is None, it will be created from db_data
        If db_data is provided, it will already create db_object from it
    """

    # Create new database object if needed
    try:
        if isinstance(db_object, type(None)):
            db_object = db_Model(**db_data)
    except Exception as e:
        raise Exception(f"Could not create database model from db_data: {e}")

    # Save database entry
    try:
        await sync_to_async(db_object.save, thread_sensitive=True)()
    except Exception as e:
        error_message = f"Could not save {type(db_Model)} database entry: {e}"
        log.error(error_message)
        raise Exception(error_message)
    
    # Return the database object if needed
    if return_obj:
        return db_object


_redis_client = None
_redis_available = None

def get_redis_client():
    """Get Redis client for LIST and pipeline operations. Cached singleton."""
    global _redis_client, _redis_available
    
    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client
    
    try:
        if hasattr(settings, 'CACHES') and 'redis' in str(settings.CACHES.get('default', {}).get('BACKEND', '')):
            cache_location = settings.CACHES['default'].get('LOCATION')
            if cache_location:
                _redis_client = redis.Redis.from_url(cache_location)
                _redis_client.ping()
                _redis_available = True
                log.info("Redis client initialized successfully")
                return _redis_client
    except Exception as e:
        log.warning(f"Redis not available, falling back to Django cache: {e}")
    
    _redis_available = False
    _redis_client = None
    return None

# ========================================
# Generic Streaming Helpers
# ========================================

def _get_cache_key(key_type: str, task_id: str) -> str:
    """Get cache key for streaming data (Django cache uses Redis in production)"""
    return f"stream:{key_type}:{task_id}"

def _cache_set(task_id: str, key_type: str, value: str, ttl: int = 3600):
    """Generic cache set - uses Django cache (which is Redis in production)"""
    try:
        key = _get_cache_key(key_type, task_id)
        cache.set(key, value, ttl)
    except Exception as e:
        log.error(f"Error setting streaming {key_type} for task {task_id}: {e}")

def _cache_get(task_id: str, key_type: str):
    """Generic cache get - uses Django cache (which is Redis in production)"""
    try:
        key = _get_cache_key(key_type, task_id)
        return cache.get(key)
    except Exception as e:
        log.error(f"Error getting streaming {key_type} for task {task_id}: {e}")
        return None

# ========================================
# Streaming Data (Redis LIST operations)
# ========================================

def store_streaming_data(task_id: str, chunk_data: str, ttl: int = 600):
    """Store streaming chunk using Redis LIST (lpush for ordering)"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = _get_cache_key("data", task_id)
            redis_client.lpush(key, chunk_data)
            redis_client.expire(key, ttl)
        else:
            # Fallback: store as regular list in cache (less efficient)
            key = _get_cache_key("data", task_id)
            existing = cache.get(key, [])
            existing.append(chunk_data)
            cache.set(key, existing, ttl)
    except Exception as e:
        log.error(f"Error storing streaming data for task {task_id}: {e}")

def get_streaming_data(task_id: str):
    """Get all streaming chunks using Redis LIST (lrange)"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = _get_cache_key("data", task_id)
            chunks = redis_client.lrange(key, 0, -1)
            return [chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk for chunk in reversed(chunks)]
        else:
            # Fallback: retrieve from cache as regular list
            key = _get_cache_key("data", task_id)
            return cache.get(key, [])
    except Exception as e:
        log.error(f"Error getting streaming data for task {task_id}: {e}")
        return []

# ========================================
# Streaming Metadata (Django cache)
# ========================================

def set_streaming_metadata(task_id: str, metadata_type: str, value: str, ttl: int = 3600):
    """Set streaming metadata - use direct Redis for consistency with batch operations"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = _get_cache_key(metadata_type, task_id)
            redis_client.setex(key, ttl, value)
        else:
            # Fallback to Django cache
            _cache_set(task_id, metadata_type, value, ttl)
    except Exception as e:
        log.error(f"Error setting streaming {metadata_type} for task {task_id}: {e}")

def get_streaming_metadata(task_id: str, metadata_type: str):
    """Get streaming metadata - use direct Redis for consistency with batch operations"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = _get_cache_key(metadata_type, task_id)
            value = redis_client.get(key)
            return value.decode('utf-8') if isinstance(value, bytes) else value
        else:
            # Fallback to Django cache
            return _cache_get(task_id, metadata_type)
    except Exception as e:
        log.error(f"Error getting streaming {metadata_type} for task {task_id}: {e}")
        return None
def set_streaming_status(task_id: str, status: str, ttl: int = 3600):
    """Set streaming status"""
    set_streaming_metadata(task_id, "status", status, ttl)

def get_streaming_status(task_id: str):
    """Get streaming status"""
    return get_streaming_metadata(task_id, "status")

def set_streaming_error(task_id: str, error: str, ttl: int = 3600):
    """Set streaming error"""
    set_streaming_metadata(task_id, "error", error, ttl)

def get_streaming_error(task_id: str):
    """Get streaming error"""
    return get_streaming_metadata(task_id, "error")


# ========================================
# Token Security
# ========================================

def generate_and_store_streaming_token(task_id: str, ttl: int = 600) -> str:
    """Generate and store authentication token (256 bits entropy) - use direct Redis"""
    token = secrets.token_urlsafe(32)  # 32 bytes = 256 bits
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = _get_cache_key("token", task_id)
            redis_client.setex(key, ttl, token)
        else:
            _cache_set(task_id, "token", token, ttl)
    except Exception as e:
        log.error(f"Error storing token for task {task_id}: {e}")
    log.info(f"Generated and stored streaming token for task {task_id}")
    return token

def validate_streaming_task_token(task_id: str, provided_token: str) -> bool:
    """Validate task token (constant-time comparison) - use direct Redis"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = _get_cache_key("token", task_id)
            stored_token = redis_client.get(key)
            stored_token = stored_token.decode('utf-8') if isinstance(stored_token, bytes) else stored_token
        else:
            stored_token = _cache_get(task_id, "token")
            
        if stored_token:
            is_valid = hmac.compare_digest(stored_token, provided_token)
            if not is_valid:
                log.warning(f"Invalid token provided for task {task_id}")
            return is_valid
        
        log.warning(f"No stored token found for task {task_id}")
        return False
    except Exception as e:
        log.error(f"Error validating streaming token for task {task_id}: {e}")
        return False


# ========================================
# Validation (with in-memory cache)
# ========================================

_validation_cache = TTLCache(maxsize=10000, ttl=300)

def validate_streaming_request_optimized(task_id: str, provided_token: str) -> tuple:
    """Validate streaming request with caching. Returns (is_valid, error_message)"""
    # Check in-memory cache first
    cache_key = f"{task_id}:{provided_token[:16]}"
    try:
        if cache_key in _validation_cache:
            is_valid = _validation_cache[cache_key]
            return (True, None) if is_valid else (False, "Invalid or expired task authentication")
    except Exception:
        pass
    
    # Validate task_id format (UUID)
    try:
        uuid.UUID(task_id)
    except ValueError:
        return False, "Invalid task_id format"
    
    # Validate token (also checks if task exists)
    try:
        is_valid = validate_streaming_task_token(task_id, provided_token)
        
        # Cache the result
        try:
            _validation_cache[cache_key] = is_valid
        except Exception as e:
            log.warning(f"Failed to cache validation result: {e}")
        
        if is_valid:
            return True, None
        return False, "Invalid task authentication token"
            
    except Exception as e:
        log.error(f"Error in validation: {e}")
        return False, f"Validation error: {str(e)}"


def validate_streaming_request_security(request, max_content_length: int = 150000) -> tuple:
    """
    Validate security requirements for streaming API endpoints.
    Checks Content-Length, X-Internal-Secret, and X-Stream-Task-Token.
    
    Args:
        request: Django request object
        max_content_length: Maximum allowed content length in bytes
        
    Returns:
        (is_valid, error_response_dict, status_code) tuple
        - is_valid: True if all checks pass, False otherwise
        - error_response_dict: Dict with error details if validation fails, None if valid
        - status_code: HTTP status code for error response, None if valid
    """
    
    # SECURITY LAYER 1 - Validate Content-Length BEFORE parsing
    content_length = request.headers.get('Content-Length')
    if content_length:
        try:
            if int(content_length) > max_content_length:
                log.warning(f"Streaming request exceeded size limit: {content_length} bytes (max: {max_content_length})")
                return False, {"error": "Request too large"}, 413
        except ValueError:
            pass  # Invalid Content-Length, let parsing catch it
    
    # SECURITY LAYER 2: Validate global internal secret
    internal_secret = request.headers.get('X-Internal-Secret', '')
    expected_secret = getattr(settings, 'INTERNAL_STREAMING_SECRET', 'default-secret-change-me')
    if internal_secret != expected_secret:
        log.warning("Streaming request with invalid internal secret")
        return False, {"error": "Unauthorized: Invalid internal secret"}, 401
    
    # SECURITY LAYER 3: Validate per-task token
    task_token = request.headers.get('X-Stream-Task-Token', '')
    if not task_token:
        log.warning("Streaming request missing task token")
        return False, {"error": "Unauthorized: Missing task token"}, 401
    
    # Parse request body to get task_id for token validation
    try:
        data = json.loads(decode_request_body(request))
        task_id = data.get('task_id')
        
        if not task_id:
            return False, {"error": "Missing task_id"}, 400
        
        # Validate the task token using optimized validation
        is_valid, error_msg = validate_streaming_request_optimized(task_id, task_token)
        if not is_valid:
            log.warning(f"Streaming validation failed for task {task_id}: {error_msg}")
            return False, {"error": error_msg}, 403
        
        # All validation passed
        return True, None, None
        
    except json.JSONDecodeError as e:
        log.error(f"Invalid JSON in streaming request: {e}")
        return False, {"error": "Invalid JSON"}, 400
    except Exception as e:
        log.error(f"Error validating streaming request: {e}")
        return False, {"error": "Internal server error"}, 500


# ========================================
# Batched Operations (Redis pipelines)
# ========================================

def get_streaming_data_and_status_batch(task_id: str):
    """Get data, status, and error in single Redis pipeline. Returns (chunks, status, error)"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            # Use Redis pipeline for optimal performance
            pipe = redis_client.pipeline()
            pipe.lrange(_get_cache_key("data", task_id), 0, -1)
            pipe.get(_get_cache_key("status", task_id))
            pipe.get(_get_cache_key("error", task_id))
            
            results = pipe.execute()
            
            # Process results with byte decoding
            chunks = [chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk 
                     for chunk in reversed(results[0])] if results[0] else []
            status = results[1].decode('utf-8') if isinstance(results[1], bytes) else results[1]
            error = results[2].decode('utf-8') if isinstance(results[2], bytes) else results[2]
            
            return chunks, status, error
        else:
            # Fallback to sequential operations using Django cache
            return get_streaming_data(task_id), get_streaming_status(task_id), get_streaming_error(task_id)
            
    except Exception as e:
        log.error(f"Error in batched streaming retrieval for task {task_id}: {e}")
        return [], None, None

def store_streaming_data_batch(task_id: str, chunk_list: list, ttl: int = 3600):
    """Store multiple chunks in single Redis pipeline"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = _get_cache_key("data", task_id)
            pipe = redis_client.pipeline()
            for chunk_data in chunk_list:
                pipe.lpush(key, chunk_data)
            pipe.expire(key, ttl)
            pipe.execute()
        else:
            # Fallback to sequential operations using Django cache
            for chunk_data in chunk_list:
                store_streaming_data(task_id, chunk_data, ttl)
    except Exception as e:
        log.error(f"Error storing batched streaming data for task {task_id}: {e}")


# ========================================
# Endpoint Caching
# ========================================

def get_endpoint_from_cache(endpoint_slug):
    """Get endpoint from cache or None if not found"""
    cache_key = f"endpoint:{endpoint_slug}"
    try:
        cached_endpoint = cache.get(cache_key)
        if cached_endpoint:
            log.info(f"Retrieved endpoint {endpoint_slug} from cache.")
            return cached_endpoint
    except Exception as e:
        log.warning(f"Cache error for endpoint {endpoint_slug}: {e}")
    return None

def cache_endpoint(endpoint_slug, endpoint_data):
    """Cache endpoint data (5 minute TTL)"""
    cache_key = f"endpoint:{endpoint_slug}"
    try:
        cache.set(cache_key, endpoint_data, 300)
        log.info(f"Cached endpoint {endpoint_slug}.")
    except Exception as e:
        log.warning(f"Failed to cache endpoint {endpoint_slug}: {e}")

def remove_endpoint_from_cache(endpoint_slug):
    """Remove endpoint from cache"""
    cache_key = f"endpoint:{endpoint_slug}"
    try:
        cache.delete(cache_key)
        log.info(f"Removed endpoint {endpoint_slug} from cache.")
    except Exception as e:
        log.warning(f"Failed to remove endpoint {endpoint_slug} from cache: {e}")


# Get HTTP response
async def get_response(content, code, request):
    """Create database entries and prepare the HTTP response for the user."""

    # If this is an error, define the cache key to see whether the error occured recently (during high traffic)
    skip_database_operation = False
    if code != 200:
        try: 
            cache_key = request.auth.username + str(content) + str(code)
            skip_database_operation = is_cached(cache_key, create_empty=True)
        except:
            skip_database_operation = False

    # Try to create database entries
    # Skip if this is a repeating cached error during high traffic
    if not skip_database_operation:
      try:

        # First, create AccessLog database entry (should always have one)
        if hasattr(request, "access_log_data"):
            access_log = await create_access_log(request.access_log_data, content, code)
        else:
            return HttpResponse(json.dumps("Error: get_response did not receive AccessLog data"), status=400)
        
        # Create RequestLog database entry
        if hasattr(request, "request_log_data"):
            request.request_log_data.access_log = access_log
            req_obj = await create_request_log(request.request_log_data, content, code)

            # Create RequestMetrics immediately for non-streaming requests
            await _upsert_request_metrics_auto(req_obj, access_log)

        # Create BatchLog database entry
        if hasattr(request, "batch_log_data"):
            request.batch_log_data.access_log = access_log
            batch_obj = await create_batch_log(request.batch_log_data, content, code)
            # Create BatchMetrics skeleton; later updates can fill tokens/throughput
            try:
                await _upsert_batch_metrics(
                    batch_obj=batch_obj,
                    total_tokens=None,
                    num_responses=None,
                    response_time_sec=None,
                    throughput_tokens_per_sec=None,
                )
            except Exception as e:
                log.error(f"Error creating BatchMetrics: {e}")

      # Error message if something went wrong while creating database entries
      except Exception as e:
        return HttpResponse(json.dumps(f"Error: Could not create database entries: {e}"), status=400)

    # Prepare and return the HTTP response
    if code == 200:
        # Ensure JSON content-type and avoid double-encoding
        try:
            # If content is a JSON string, validate and return as-is
            if isinstance(content, str):
                _ = json.loads(content)
                return HttpResponse(content, status=code, content_type='application/json')
            # If it's already a dict/list, dump once
            return HttpResponse(json.dumps(content), status=code, content_type='application/json')
        except Exception:
            # Fallback: return raw
            return HttpResponse(content, status=code)
    else:
        log.error(content)
        return HttpResponse(json.dumps(content), status=code)


async def get_batch_response(db_data, content, code, db_Model):
    """Log result or error in the current database model and return the HTTP response."""
    
    # Create database entry
    try:
        await update_database(db_Model=db_Model, db_data=db_data)
    except Exception as e:
        error_message = f"Error: Could not update database: {e}"
        log.error(error_message)
        return HttpResponse(json.dumps(error_message), status=400)
        
    # Return the response or the error message from previous steps
    if code == 200:
        return HttpResponse(content, status=code)
    else:
        log.error(content)
        return HttpResponse(json.dumps(content), status=code)


# ========================================
# Streaming Setup
# ========================================

def prepare_streaming_task_data(data, stream_task_id):
    """Prepare streaming task data with server config and auth token"""
    stream_server_host = getattr(settings, 'STREAMING_SERVER_HOST', 'data-portal-dev.cels.anl.gov')
    stream_server_port = getattr(settings, 'STREAMING_SERVER_PORT', 443)
    stream_server_protocol = getattr(settings, 'STREAMING_SERVER_PROTOCOL', 'https')
    
    task_token = generate_and_store_streaming_token(stream_task_id)
    data["model_params"].update({
        "streaming_server_host": stream_server_host,
        "streaming_server_port": stream_server_port,
        "streaming_server_protocol": stream_server_protocol,
        "stream_task_id": stream_task_id,
        "stream_task_token": task_token
    })
    
    return data

def create_streaming_response_headers():
    """Create standard headers for SSE streaming responses"""
    return {
        'Cache-Control': 'no-cache',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Cache-Control'
    }


# Create access log
async def create_access_log(access_log_data: AccessLogPydantic, content, code):
    """Create a new AccessLog database entry."""

    # Finalize data
    access_log_data.timestamp_response = timezone.now()
    access_log_data.status_code = code
    if not code == 200:
        access_log_data.error = json.dumps(content)

    # Create and return database entry
    return await update_database(db_Model=AccessLog, db_data=access_log_data.model_dump(), return_obj=True)
    

# Create request log
async def create_request_log(request_log_data: RequestLogPydantic, content, code):
    """Create a new RequestLog database entry."""

    # Finalize data
    if code == 200:
        request_log_data.result = _ensure_json_string(content)

    # Create database entry
    return await update_database(db_Model=RequestLog, db_data=request_log_data.model_dump(), return_obj=True)


# ---- Metrics helpers ----
def _safe_json(value: str):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return None

def _ensure_json_string(content):
    """Normalize any content into a proper JSON string.
    - dict/list -> json.dumps(dict)
    - str JSON -> parse then dump to avoid nested escaping
    - str non-JSON -> wrap as {"raw": content}
    - other -> json.dumps(str(content))
    """
    try:
        if isinstance(content, (dict, list)):
            return json.dumps(content)
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                return json.dumps(parsed)
            except Exception:
                return json.dumps({"raw": content})
        return json.dumps(str(content))
    except Exception:
        return json.dumps({"raw": "unserializable"})

def _compute_response_time_sec(start_ts, end_ts):
    try:
        if start_ts and end_ts:
            return (end_ts - start_ts).total_seconds()
    except Exception:
        return None
    return None

def _compute_throughput_tokens_per_sec(total_tokens, response_time_sec):
    try:
        if total_tokens is not None and response_time_sec and response_time_sec > 0:
            return total_tokens / response_time_sec
    except Exception:
        return None
    return None

async def _upsert_request_metrics_auto(request_obj: RequestLog, access_obj: AccessLog,
                                       response_time_sec: float = None,
                                       prompt_tokens=None, completion_tokens=None, total_tokens=None):
    # Derive tokens from stored result if not provided
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        p, c, t = _extract_usage_tokens_from_result(request_obj.result)
        prompt_tokens, completion_tokens, total_tokens = p, c, t

    # Derive response time from timestamps if not provided
    if response_time_sec is None:
        response_time_sec = _compute_response_time_sec(
            request_obj.timestamp_compute_request, request_obj.timestamp_compute_response
        )

    throughput = _compute_throughput_tokens_per_sec(total_tokens, response_time_sec)

    try:
        await _upsert_request_metrics(
            request_obj=request_obj,
            access_obj=access_obj,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            response_time_sec=response_time_sec,
            throughput_tokens_per_sec=throughput,
        )
    except Exception as e:
        log.error(f"Error upserting RequestMetrics: {e}")

def _extract_usage_tokens_from_result(result_str: str):
    prompt_tokens = None
    completion_tokens = None
    total_tokens = None
    data = _safe_json(result_str)
    if isinstance(data, dict):
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        if usage:
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
        # Some responses might nest metrics differently
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else None
        if metrics and total_tokens is None:
            total_tokens = metrics.get("total_tokens")
    return prompt_tokens, completion_tokens, total_tokens

@sync_to_async
def _upsert_request_metrics(request_obj: RequestLog, access_obj: AccessLog,
                            prompt_tokens, completion_tokens, total_tokens,
                            response_time_sec, throughput_tokens_per_sec):
    defaults = {
        "cluster": request_obj.cluster,
        "framework": request_obj.framework,
        "model": request_obj.model,
        "status_code": access_obj.status_code,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "response_time_sec": response_time_sec,
        "throughput_tokens_per_sec": throughput_tokens_per_sec,
        "timestamp_compute_request": request_obj.timestamp_compute_request,
        "timestamp_compute_response": request_obj.timestamp_compute_response,
    }
    obj, _ = RequestMetrics.objects.update_or_create(request=request_obj, defaults=defaults)
    # Mark processed on the request to avoid external re-processing
    if not request_obj.metrics_processed:
        request_obj.metrics_processed = True
        request_obj.save()
    return obj

@sync_to_async
def _upsert_batch_metrics(batch_obj: BatchLog, total_tokens, num_responses,
                          response_time_sec, throughput_tokens_per_sec):
    defaults = {
        "cluster": batch_obj.cluster,
        "framework": batch_obj.framework,
        "model": batch_obj.model,
        "status": batch_obj.status,
        "total_tokens": total_tokens,
        "num_responses": num_responses,
        "response_time_sec": response_time_sec,
        "throughput_tokens_per_sec": throughput_tokens_per_sec,
        "completed_at": batch_obj.completed_at,
    }
    obj, _ = BatchMetrics.objects.update_or_create(batch=batch_obj, defaults=defaults)
    return obj


# Create batch log
async def create_batch_log(batch_log_data: BatchLogPydantic, content, code):
    """Create a new BatchLog database entry."""

    # Finalize data
    if code == 200:
        batch_log_data.result = _ensure_json_string(content)

    # Create database entry
    return await update_database(db_Model=BatchLog, db_data=batch_log_data.model_dump(), return_obj=True)
    

# Enhanced streaming utilities for content collection

def format_streaming_error_for_openai(error_message: str):
    """Pass through JSON errors as-is, minimal processing for non-JSON errors"""
    
    try:
        # Try to parse if it's already a JSON error from vLLM
        if error_message.strip().startswith('{') and error_message.strip().endswith('}'):
            try:
                parsed_error = json.loads(error_message)
                if 'object' in parsed_error and parsed_error['object'] == 'error':
                    # Already in OpenAI error format, return as-is
                    return f"data: {json.dumps(parsed_error)}\n\n"
            except json.JSONDecodeError:
                pass
        
        # Look for JSON error in "Response text:" sections and extract it as-is
        response_text_match = re.search(r'Response text[:\s]*(\{.*?\})', error_message, re.DOTALL)
        if response_text_match:
            try:
                json_error = response_text_match.group(1)
                parsed_error = json.loads(json_error)
                if 'object' in parsed_error and parsed_error['object'] == 'error':
                    # Found a valid JSON error, return it as-is
                    return f"data: {json.dumps(parsed_error)}\n\n"
            except json.JSONDecodeError:
                pass
        
        # Fallback for non-JSON errors - minimal generic error
        fallback_error = {
            "object": "error", 
            "message": "An error occurred during processing",
            "type": "InternalServerError",
            "param": None,
            "code": 500
        }
        return f"data: {json.dumps(fallback_error)}\n\n"
        
    except Exception as e:
        # Ultimate fallback
        fallback_error = {
            "object": "error", 
            "message": "An error occurred during processing",
            "type": "InternalServerError",
            "param": None,
            "code": 500
        }
        return f"data: {json.dumps(fallback_error)}\n\n"

def extract_status_code_from_error(error_message: str):
    """Extract status code from error message for database logging"""
    
    try:
        # Look for explicit status codes in error message
        if "status code:" in error_message:
            match = re.search(r"status code[:\s]+(\d+)", error_message)
            if match:
                return int(match.group(1))
        
        # Look for status codes in JSON error objects
        if '"code"' in error_message:
            code_match = re.search(r'"code"\s*:\s*(\d+)', error_message)
            if code_match:
                return int(code_match.group(1))
        
        # Common error patterns
        if "max_tokens must be at least" in error_message or "maximum context length" in error_message:
            return 400  # Bad request
        elif "unauthorized" in error_message.lower() or "authentication" in error_message.lower():
            return 401
        elif "forbidden" in error_message.lower() or "permission" in error_message.lower():
            return 403
        elif "not found" in error_message.lower():
            return 404
        elif "rate limit" in error_message.lower() or "too many requests" in error_message.lower():
            return 429
        
        # Default to 500 for unknown errors
        return 500
        
    except:
        return 500

def collect_and_aggregate_streaming_content(task_id: str, original_prompt=None):
    """Collect all streaming content and create a complete response"""
    chunks = get_streaming_data(task_id)
    if not chunks:
        return None
    
    try:
        # Reconstruct the complete streaming response
        full_content = ""
        usage_info = {}
        model_info = {}
        finish_reason = None
        content_chunks = 0
        
        for chunk in chunks:
            if chunk.startswith('data: '):
                chunk_data = chunk[6:]  # Remove "data: " prefix
                if chunk_data.strip() == '[DONE]':
                    continue
                
                try:
                    parsed_chunk = json.loads(chunk_data)
                    
                    # Collect usage info (usually in the last chunk or special chunks)
                    if 'usage' in parsed_chunk and parsed_chunk['usage']:
                        usage_info.update(parsed_chunk['usage'])
                    
                    # Collect model info (from first chunk usually)
                    if 'model' in parsed_chunk:
                        model_info['model'] = parsed_chunk['model']
                    if 'id' in parsed_chunk:
                        model_info['id'] = parsed_chunk['id']
                    if 'object' in parsed_chunk:
                        model_info['object'] = parsed_chunk['object']  
                    if 'created' in parsed_chunk:
                        model_info['created'] = parsed_chunk['created']
                    
                    # Collect content from streaming chunks
                    choices = parsed_chunk.get('choices', [])
                    if choices and len(choices) > 0:
                        choice = choices[0]
                        
                        # For streaming responses, content is in delta
                        delta = choice.get('delta', {})
                        content = delta.get('content', '')
                        if content:
                            full_content += content
                            content_chunks += 1
                        
                        # Check for finish reason (in final chunks)
                        if 'finish_reason' in choice and choice['finish_reason']:
                            finish_reason = choice['finish_reason']
                
                except json.JSONDecodeError:
                    continue
        
        # If no usage info was captured from chunks, estimate from content
        if not usage_info or not usage_info.get('total_tokens', 0):
            # Enhanced token estimation using multiple methods
            char_estimate = len(full_content) // 4  # ~4 chars per token
            word_estimate = len(full_content.split()) * 1.3  # ~1.3 tokens per word
            
            # Use average of methods for better accuracy  
            estimated_completion_tokens = int((char_estimate + word_estimate) / 2)
            estimated_completion_tokens = max(1, estimated_completion_tokens)
            
            # Estimate prompt tokens more accurately if we have the original prompt
            estimated_prompt_tokens = 50  # Conservative default
            if original_prompt:
                try:
                    if isinstance(original_prompt, str):
                        prompt_text = original_prompt
                    elif isinstance(original_prompt, list):
                        # Handle messages format - extract all content
                        prompt_parts = []
                        for msg in original_prompt:
                            if isinstance(msg, dict) and msg.get('content'):
                                prompt_parts.append(msg['content'])
                        prompt_text = " ".join(prompt_parts)
                    else:
                        prompt_text = str(original_prompt)
                    
                    # Better prompt token estimation using same dual method
                    prompt_char_estimate = len(prompt_text) // 4
                    prompt_word_estimate = len(prompt_text.split()) * 1.3
                    estimated_prompt_tokens = int((prompt_char_estimate + prompt_word_estimate) / 2)
                    estimated_prompt_tokens = max(10, estimated_prompt_tokens)
                    
                    log.info(f"Prompt token estimation for {task_id}: {estimated_prompt_tokens} tokens from {len(prompt_text)} chars")
                except Exception as e:
                    log.warning(f"Error parsing prompt for token estimation: {e}")
            
            usage_info = {
                "prompt_tokens": estimated_prompt_tokens,
                "completion_tokens": estimated_completion_tokens,
                "total_tokens": estimated_prompt_tokens + estimated_completion_tokens,
                "prompt_tokens_details": None
            }
            log.info(f"Token estimation for {task_id}: {usage_info['total_tokens']} total ({usage_info['completion_tokens']} completion, {usage_info['prompt_tokens']} prompt)")
        
        # Ensure we have the correct object type for a complete response (not chunk)
        model_info['object'] = 'chat.completion'  # Always set to completion, not chunk
        
        # Create a complete response in authentic OpenAI/vLLM streaming format
        # Only include fields that are actually provided by vLLM/OpenAI streaming
        complete_response = {
            **model_info,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_content
                },
                "finish_reason": finish_reason or "stop"
            }],
            "usage": usage_info
        }
        
        return complete_response
        
    except Exception as e:
        log.error(f"Error aggregating streaming content: {e}")
        return None

@sync_to_async
def get_log_entry_with_access_log(log_id: str) -> RequestLog:
    """Extract a RequestLog entry and also recover the related AccessLog object."""
    return RequestLog.objects.select_related("access_log").get(id=log_id)


async def update_streaming_log_async(log_id: str, final_metrics: dict, complete_response: dict, stream_task_id: str = None):
    """Asynchronously update streaming log entry with final content"""
    
    try:
        # Get the RequestLog and AccessLog entry without using lazy loading
        log_entry = await get_log_entry_with_access_log(log_id)
        access_log = log_entry.access_log  # Safe: already fetched
        
        # Preserve the original task_uuid
        original_task_uuid = log_entry.task_uuid
        
        # Check if there was a streaming error
        error_status = final_metrics.get('final_status')
        streaming_error = None
        response_status = 200  # Default success

        if error_status == "error" and stream_task_id:
            # Get the actual error message
            streaming_error = get_streaming_error(stream_task_id)
            if streaming_error:
                # Extract status code using the simple utility function
                response_status = extract_status_code_from_error(streaming_error)

        # Update the response status in the log entry
        access_log.status_code = response_status
        
        if complete_response and not streaming_error:
            # Calculate and add basic metrics to match non-streaming format
            usage = complete_response.get('usage', {})
            total_tokens = usage.get('total_tokens', 0)
            response_time = final_metrics.get('total_processing_time', 0)
            
            # Add throughput calculation like non-streaming
            if response_time > 0 and total_tokens > 0:
                throughput = total_tokens / response_time
                complete_response['response_time'] = response_time
                complete_response['throughput_tokens_per_second'] = throughput
            
            log_entry.result = json.dumps(complete_response, indent=4)
        elif streaming_error:
            # Handle error case - store the full original error message
            error_response = {
                "streaming_response": True,
                "error": True,
                "error_message": streaming_error,  # Store full original error
                "response_time": final_metrics.get('total_processing_time', 0),
                "throughput_tokens_per_second": 0,
                "status": "failed"
            }
            log_entry.result = None
            access_log.error = json.dumps(error_response, indent=4)
        else:
            # Fallback if we couldn't reconstruct the response
            log_entry.result = json.dumps({
                "streaming_response": True,
                "error": "Could not reconstruct complete response",
                "metrics": final_metrics,
                "response_time": final_metrics.get('total_processing_time', 0),
                "throughput_tokens_per_second": 0
            }, indent=4)
        
        log_entry.timestamp_compute_response = timezone.now()
        
        # Ensure task_uuid is preserved (don't let it get overwritten)
        if original_task_uuid and not log_entry.task_uuid:
            log_entry.task_uuid = original_task_uuid
        
        # Save the updated log and access entries
        await update_database(db_Model=AccessLog, db_object=access_log)
        await update_database(db_Model=RequestLog, db_object=log_entry)

        # Upsert RequestMetrics for streaming (derive from final data)
        await _upsert_request_metrics_auto(
            request_obj=log_entry,
            access_obj=access_log,
            response_time_sec=(final_metrics.get('total_processing_time') if final_metrics else None)
        )

        log.info(f"Updated streaming log entry {log_id} with final content (status: {response_status})")
        
    except Exception as e:
        log.error(f"Error updating streaming log entry {log_id}: {e}")

def cleanup_streaming_data(task_id: str):
    """Clean up all streaming data for a task"""
    try:
        redis_client = get_redis_client()
        key_types = ["data", "status", "error", "token"]
        
        if redis_client:
            # Batch delete all Redis keys (more efficient than individual deletes)
            keys = [_get_cache_key(kt, task_id) for kt in key_types]
            redis_client.delete(*keys)
        else:
            # Fallback to Django cache delete
            for key_type in key_types:
                cache.delete(_get_cache_key(key_type, task_id))
        
        log.info(f"Cleaned up streaming data for task {task_id}")
    except Exception as e:
        log.error(f"Error cleaning up streaming data for task {task_id}: {e}")

async def process_streaming_completion_async(task_id: str, stream_task_id: str, log_id: str, globus_task_future, start_time: float, original_prompt=None):
    """Background task to process streaming completion and update database"""
    
    try:
        # Wait a bit for initial streaming data to arrive
        await asyncio.sleep(2)
        
        # Wait for streaming to complete
        max_wait = 300  # Wait up to 5 minutes for streaming completion
        wait_start = time.time()
        while time.time() - wait_start < max_wait:
            status = get_streaming_status(stream_task_id)
            if status in ["completed", "error"]:
                break
            await asyncio.sleep(0.5)
        
        # Collect final streaming data
        end_time = time.time()
        complete_response = collect_and_aggregate_streaming_content(stream_task_id, original_prompt)
        
        # Simple metrics
        simple_metrics = {
            'total_processing_time': end_time - start_time,
            'final_status': get_streaming_status(stream_task_id) or 'completed'
        }
        
        # Update the database log entry with final data
        await update_streaming_log_async(log_id, simple_metrics, complete_response, stream_task_id)
        
        # Wait a moment before cleanup to ensure SSE generator reads the "completed" status
        # The SSE generator polls every 25ms, so 500ms gives plenty of time
        await asyncio.sleep(0.5)
        
        # Clean up streaming data from cache
        cleanup_streaming_data(stream_task_id)
        
        log.info(f"Completed streaming processing for task {task_id}")
        
    except Exception as e:
        log.error(f"Error in streaming completion processing: {e}")
        try:
            # Try to update log with error info
            await update_streaming_log_async(log_id, {"error": str(e), "final_status": "error"}, None, stream_task_id)
        except:
            pass


# ============================================================================
# Metis Cluster Utilities
# ============================================================================

async def handle_metis_streaming_inference(
    request, 
    model_info: dict, 
    endpoint_id: str, 
    api_request_data: dict, 
    requested_model: str,
    access_log
):
    """
    Handle streaming inference for Metis cluster.
    
    Args:
        request: Django request object with request_log_data
        model_info: Model information from Metis status
        endpoint_id: Metis endpoint UUID for token lookup
        api_request_data: Request payload in OpenAI format
        requested_model: Name of the requested model
        access_log: AccessLog database object
    
    Returns:
        StreamingHttpResponse with SSE content
    """
    from django.http import StreamingHttpResponse
    import utils.metis_utils as metis_utils
    
    # Create initial log entry
    try:
        request.request_log_data.access_log = access_log
        request.request_log_data.result = "streaming_response_in_progress"
        request.request_log_data.timestamp_compute_response = timezone.now()
        request.request_log_data.task_uuid = None  # Metis doesn't use task UUIDs
        
        db_log = RequestLog(**request.request_log_data.model_dump())
        await sync_to_async(db_log.save, thread_sensitive=True)()
        log_id = db_log.id
        log.info(f"Created Metis streaming log {log_id} for {requested_model}")
    except Exception as e:
        log.error(f"Error creating Metis streaming log: {e}")
        log_id = None
    
    # Shared state for tracking streaming (optimized - minimal memory)
    streaming_state = {
        'chunks': [],  # Limited to 100 chunks
        'total_chunks': 0,
        'completed': False,
        'error': None,
        'start_time': time.time()
    }
    
    # SSE generator
    async def metis_sse_generator():
        """Stream SSE chunks from Metis API"""
        try:
            async for chunk in metis_utils.stream_metis_api(model_info, endpoint_id, api_request_data):
                if chunk:
                    streaming_state['total_chunks'] += 1
                    yield chunk  # Pass through SSE format
                    
                    # Collect limited chunks for logging (optimize memory)
                    if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                        if len(streaming_state['chunks']) < 100:
                            try:
                                streaming_state['chunks'].append(chunk[6:].strip())
                            except:
                                pass
            
            streaming_state['completed'] = True
                    
        except Exception as e:
            error_str = str(e)
            log.error(f"Metis streaming error: {error_str}")
            streaming_state['error'] = error_str
            streaming_state['completed'] = True
            
            # Send error as OpenAI streaming chunk format (compatible with OpenAI clients)
            error_chunk = {
                "id": f"chatcmpl-metis-error",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": requested_model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": f"\n\n[ERROR] {error_str}"
                    },
                    "finish_reason": "stop"
                }]
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
    
    # Start background task to update log
    if log_id:
        asyncio.create_task(update_metis_streaming_log(
            log_id, streaming_state, requested_model
        ))
    
    # Create streaming response
    response = StreamingHttpResponse(
        streaming_content=metis_sse_generator(),
        content_type='text/event-stream'
    )
    
    # Set SSE headers
    for key, value in create_streaming_response_headers().items():
        response[key] = value
    
    return response


async def update_metis_streaming_log(log_id, streaming_state: dict, requested_model: str):
    """
    Background task to update RequestLog after Metis streaming completes.
    
    Optimized to:
    - Wait efficiently for completion
    - Update database once
    - Handle errors gracefully
    """
    try:
        # Wait for completion (efficient polling with timeout)
        max_wait = 600  # 10 minutes
        waited = 0
        poll_interval = 0.5  # 500ms
        
        while not streaming_state['completed'] and waited < max_wait:
            await asyncio.sleep(poll_interval)
            waited += poll_interval
        
        # Get metrics
        duration = time.time() - streaming_state['start_time']
        total_chunks = streaming_state['total_chunks']
        
        # Update database (single query)
        db_log = await sync_to_async(RequestLog.objects.get)(id=log_id)
        
        if streaming_state['error']:
            db_log.result = f"error: {streaming_state['error']}"
            log.error(f"Metis streaming failed for {requested_model}: {streaming_state['error']}")
        else:
            # Store limited chunks or completion marker
            db_log.result = "\n".join(streaming_state['chunks']) if streaming_state['chunks'] else "streaming_completed"
            log.info(f"Metis streaming completed for {requested_model}: {total_chunks} chunks in {duration:.2f}s")
        
        db_log.timestamp_compute_response = timezone.now()
        await sync_to_async(db_log.save, thread_sensitive=True)()
        
    except Exception as e:
        log.error(f"Error in update_metis_streaming_log: {e}")
