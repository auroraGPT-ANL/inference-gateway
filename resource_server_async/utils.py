import uuid
import json
import logging
import redis
import time
import asyncio
import re
from enum import Enum
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone
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
    get_compute_executor
)
from resource_server.models import ModelStatus
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


# Validate body
def validate_body(request, pydantic_class):
    """Validate body data from incoming user requests against a given pydantic model."""
                
    # Decode request body into a dictionary
    try:
        params = json.loads(request.body.decode("utf-8"))
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
@asynccached(TTLCache(maxsize=1024, ttl=30))
async def get_qstat_details(cluster, gcc=None, gce=None, timeout=60):
    """
    Collect details on all jobs running/submitted on a given cluster.
    Here return the error message instead of raising exceptions to 
    make sure the outcome gets cached.
    Returns result, task_uuid, error_message, error_code
    """

    # Try to get cached qstat details
    try:
        latest_status = await sync_to_async(ModelStatus.objects.get)(cluster=cluster)
    except ModelStatus.DoesNotExist:
        latest_status = None
    
    # If there is cached information ...
    if latest_status:

        # If the last entry was written not too long ago (here 2 min where the cron job is 1 min) ...
        # This ensures that user get an up-to-date response if the cron job crashed
        # TODO: Make this 120 sec a global parameters
        try:
            delta_seconds = (timezone.now() - latest_status.timestamp).total_seconds()
            if delta_seconds < 120:

                # Return cached information if there is a valid qstat response
                if len(latest_status.result) > 0:
                    return latest_status.result, "", "", 200
                
        # Continue as normal if this fails so that user can have an up-to-date response
        except Exception as e:
            return None, None, f"Error: Could not recover qstat details from database: {e}", 500

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


# Redis streaming data management functions
def get_redis_client():
    """Get Redis client for streaming data storage"""
    try:
        # Try to use Django's cache if it's Redis
        if hasattr(settings, 'CACHES') and 'redis' in str(settings.CACHES.get('default', {})):
            return redis.Redis.from_url(settings.CACHES['default']['LOCATION'])
        else:
            # Fallback to local Redis
            return redis.Redis(host='localhost', port=6379, db=1, decode_responses=False)
    except:
        # If Redis is not available, fallback to Django cache
        return None

def store_streaming_data(task_id: str, chunk_data: str, ttl: int = 3600):
    """Store streaming chunk with automatic cleanup"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            # Use Redis with TTL
            key = f"stream:data:{task_id}"
            redis_client.lpush(key, chunk_data)
            redis_client.expire(key, ttl)  # Auto-cleanup after 1 hour
        else:
            # Fallback to Django cache
            key = f"stream_data_{task_id}"
            existing = cache.get(key, [])
            existing.append(chunk_data)
            cache.set(key, existing, ttl)
    except Exception as e:
        log.error(f"Error storing streaming data: {e}")

def get_streaming_data(task_id: str):
    """Get streaming chunks for a task"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = f"stream:data:{task_id}"
            chunks = redis_client.lrange(key, 0, -1)
            return [chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk for chunk in reversed(chunks)]
        else:
            # Fallback to Django cache
            key = f"stream_data_{task_id}"
            return cache.get(key, [])
    except Exception as e:
        log.error(f"Error getting streaming data: {e}")
        return []

def set_streaming_status(task_id: str, status: str, ttl: int = 3600):
    """Set streaming status with automatic cleanup"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = f"stream:status:{task_id}"
            redis_client.set(key, status, ex=ttl)
        else:
            # Fallback to Django cache
            key = f"stream_status_{task_id}"
            cache.set(key, status, ttl)
    except Exception as e:
        log.error(f"Error setting streaming status: {e}")

def get_streaming_status(task_id: str):
    """Get streaming status for a task"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = f"stream:status:{task_id}"
            status = redis_client.get(key)
            return status.decode('utf-8') if isinstance(status, bytes) else status
        else:
            # Fallback to Django cache
            key = f"stream_status_{task_id}"
            return cache.get(key)
    except Exception as e:
        log.error(f"Error getting streaming status: {e}")
        return None

def set_streaming_error(task_id: str, error: str, ttl: int = 3600):
    """Set streaming error with automatic cleanup"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = f"stream:error:{task_id}"
            redis_client.set(key, error, ex=ttl)
        else:
            # Fallback to Django cache
            key = f"stream_error_{task_id}"
            cache.set(key, error, ttl)
    except Exception as e:
        log.error(f"Error setting streaming error: {e}")

def get_streaming_error(task_id: str):
    """Get streaming error for a task"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = f"stream:error:{task_id}"
            error = redis_client.get(key)
            return error.decode('utf-8') if isinstance(error, bytes) else error
        else:
            # Fallback to Django cache
            key = f"stream_error_{task_id}"
            return cache.get(key)
    except Exception as e:
        log.error(f"Error getting streaming error: {e}")
        return None


# Endpoint caching utilities
def get_endpoint_from_cache(endpoint_slug):
    """Get endpoint from Redis cache with fallback to in-memory"""
    cache_key = f"endpoint:{endpoint_slug}"
    try:
        cached_endpoint = cache.get(cache_key)
        if cached_endpoint:
            log.info(f"Retrieved endpoint {endpoint_slug} from Redis cache.")
            return cached_endpoint
    except Exception as e:
        log.warning(f"Redis cache error for endpoint {endpoint_slug}: {e}")
    return None

def cache_endpoint(endpoint_slug, endpoint_data):
    """Cache endpoint data in Redis with TTL"""
    cache_key = f"endpoint:{endpoint_slug}"
    try:
        # Cache endpoint data for 5 minutes
        cache.set(cache_key, endpoint_data, 300)
        log.info(f"Cached endpoint {endpoint_slug} in Redis.")
    except Exception as e:
        log.warning(f"Failed to cache endpoint {endpoint_slug}: {e}")

def remove_endpoint_from_cache(endpoint_slug):
    """Remove endpoint from Redis cache"""
    cache_key = f"endpoint:{endpoint_slug}"
    try:
        cache.delete(cache_key)
        log.info(f"Removed endpoint {endpoint_slug} from Redis cache.")
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


# Streaming setup utilities
def prepare_streaming_task_data(data, stream_task_id):
    """Prepare data payload for streaming task with server configuration"""
    
    # Get the streaming server configuration from settings
    stream_server_host = getattr(settings, 'STREAMING_SERVER_HOST', 'data-portal-dev.cels.anl.gov')
    stream_server_port = getattr(settings, 'STREAMING_SERVER_PORT', 443)
    stream_server_protocol = getattr(settings, 'STREAMING_SERVER_PROTOCOL', 'https')
    
    # Add streaming server details to the model_params section for the vLLM function
    data["model_params"]["streaming_server_host"] = stream_server_host
    data["model_params"]["streaming_server_port"] = stream_server_port
    data["model_params"]["streaming_server_protocol"] = stream_server_protocol
    data["model_params"]["stream_task_id"] = stream_task_id
    
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
    """Clean up streaming data from Redis/cache after processing"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            # Clean up all streaming-related keys
            keys_to_delete = [
                f"stream:data:{task_id}",
                f"stream:status:{task_id}",
                f"stream:error:{task_id}"
            ]
            for key in keys_to_delete:
                redis_client.delete(key)
        else:
            # Fallback cleanup for Django cache
            cache_keys = [
                f"stream_data_{task_id}",
                f"stream_status_{task_id}",
                f"stream_error_{task_id}"
            ]
            for key in cache_keys:
                cache.delete(key)
        
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
