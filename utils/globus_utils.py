import asyncio
import time
from django.conf import settings
import globus_sdk
from globus_compute_sdk import Client, Executor
from globus_compute_sdk.errors import TaskExecutionFailed
from globus_compute_sdk.sdk.executor import log as EXECUTOR_LOG
from cachetools import TTLCache, cached

import logging
log = logging.getLogger(__name__)


# Exception to raise in case of errors
class ResourceServerError(Exception):
    pass

# Define separate cache object for Globus executor
executor_cache = TTLCache(maxsize=1024, ttl=60*10)

# Get authenticated Compute Client using secret
# NOTE: Using in-memory TTLCache since Globus Client objects cannot be serialized to Redis
@cached(cache=TTLCache(maxsize=1024, ttl=60*60))
def get_compute_client_from_globus_app() -> globus_sdk.GlobusHTTPResponse:
    """
    Create and return an authenticated Compute client using the Globus SDK ClientApp.
    
    NOTE: This function uses in-memory caching (TTLCache) instead of Redis because
    Globus SDK Client objects are not serializable.

    Returns
    -------
        globus_compute_sdk.Client: Compute client to operate Globus Compute
    """

    # Try to create and return the Compute client
    try:
        return Client(
            app=globus_sdk.ClientApp(
                client_id=settings.SERVICE_ACCOUNT_ID,
                client_secret=settings.SERVICE_ACCOUNT_SECRET
            )
        )
    except Exception as e:
        raise ResourceServerError("Exception in creating client. Error",e)


# Get authenticated Compute Executor using existing client
# NOTE: Using in-memory TTLCache since Globus Executor objects cannot be serialized to Redis
@cached(cache=executor_cache)
def get_compute_executor(endpoint_id=None, client=None, amqp_port=443):
    """
    Create and return an authenticated Compute Executor using using existing client.
    
    NOTE: This function uses in-memory caching (TTLCache) instead of Redis because
    Globus SDK Executor objects are not serializable.

    Returns
    -------
        globus_compute_sdk.Executor: Compute Executor to operate Globus Compute
    """

    # Set log level
    if settings.GLOBUS_COMPUTE_EXECUTOR_DEBUG:
        EXECUTOR_LOG.setLevel(logging.DEBUG)

    # Try to create and return the Compute executor
    try:
        return Executor(
            endpoint_id=endpoint_id,
            client=client,
            amqp_port=amqp_port,
            batch_size=settings.GLOBUS_EXECUTOR_BATCH_SIZE, 
            api_burst_limit=settings.GLOBUS_EXECUTOR_API_BURST_LIMIT,
            api_burst_window_s=settings.GLOBUS_EXECUTOR_API_BURST_WINDOW_S
        )
    except Exception as e:
        raise ResourceServerError("Exception in creating executor. Error", e)


# Get endpoint status - Redis compatible
from django.core.cache import cache
def get_endpoint_status(endpoint_uuid=None, client=None, endpoint_slug=None):
    """
    Query the status of a Globus Compute endpoint. This version uses Redis cache
    for multi-worker support while keeping Globus objects serializable.
    """
    
    cache_key = f"endpoint_status:{endpoint_uuid}"
    
    # Try to get from Redis cache first
    try:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result
    except Exception as e:
        log.warning(f"Redis cache error for endpoint status: {e}")
    
    # If not in cache, fetch from Globus
    try:
        status_response = client.get_endpoint_status(endpoint_uuid)
        # Convert to serializable dict
        serializable_status = dict(status_response.data) if hasattr(status_response, 'data') else dict(status_response)
        result = (serializable_status, "")
        
        # Cache the result for 60 seconds
        try:
            cache.set(cache_key, result, 60)
        except Exception as e:
            log.warning(f"Failed to cache endpoint status: {e}")
            
        return result
        
    except globus_sdk.GlobusAPIError as e:
        error_result = (None, f"Error: Cannot access the status of endpoint {endpoint_slug}: {e}")
        # Cache error for shorter time (10 seconds)
        try:
            cache.set(cache_key, error_result, 10)
        except:
            pass
        return error_result
    except Exception as e:
        error_result = (None, f"Error: Cannot access the status of endpoint {endpoint_slug}: {e}")
        try:
            cache.set(cache_key, error_result, 10)
        except:
            pass
        return error_result


# Submit function and wait for result
async def submit_and_get_result(gce, endpoint_uuid, function_uuid, data=None, timeout=60*5, endpoint_slug=None):
    """
    Assign endpoint UUID to the executor, submit task to the endpoint,
    wait for the result asynchronously, and return the result or the
    error message. Here we return the error messages instead of rasing
    execptions in order to be able to cache function results if needed.
    """

    # Assign endpoint UUID to the executor 
    gce.endpoint_id = endpoint_uuid

    # Submit Globus Compute task and collect the future object
    # NOTE: Do not await here, the submit* function return the future "immediately"
    try:
        if type(data) == type(None):
            future = gce.submit_to_registered_function(function_uuid)
        else:    
            future = gce.submit_to_registered_function(function_uuid, args=[data])
    
    # Error message if something goes wrong
    # Clear cache if the Executor is shut down in order for subsequent requests to work
    except Exception as e:
        if "is shutdown" in str(e):
            executor_cache.clear()
            time.sleep(2)
        return None, None, f"Error: Could not start the Globus Compute task: {e}", 500
    
    # Cache the endpoint slug to tell the application that a user already submitted a request to this endpoint
    if endpoint_slug:
        cache_key = f"endpoint_triggered:{endpoint_slug}"
        ttl = 600 # 10 minutes
        try:
            cache.set(cache_key, True, ttl)
        except Exception as e:
            log.warning(f"Failed to cache endpoint_triggered:{endpoint_slug}: {e}")

    # Wait for the Globus Compute result using asyncio and coroutine
    try:
        asyncio_future = asyncio.wrap_future(future)
        result = await asyncio.wait_for(asyncio_future, timeout=timeout)
    except TimeoutError as e:
        error_message = "Error: TimeoutError while attempting to access compute resources. Please try again later."
        return None, get_task_uuid(future), error_message, 408
    except Exception as e:
        return None, get_task_uuid(future), f"Error: Could not recover future result: {repr(e)}", 500

    # Return result if succesful
    return result, get_task_uuid(future), "", 200


# Try to extract Globus task UUID from a future object
def get_task_uuid(future):
    try:
        return future.task_id
    except:
        return None
    

# Get batch status - Redis compatible
def get_batch_status(task_uuids_comma_separated):
    """
    Get status and results (if available) of all Globus tasks 
    associated with a batch object. Uses Redis cache for multi-worker support.
    """
    
    cache_key = f"batch_status:{task_uuids_comma_separated}"
    
    # Try to get from Redis cache first
    try:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result
    except Exception as e:
        log.warning(f"Redis cache error for batch status: {e}")

    # Recover list of Globus task UUIDs tied to the batch
    try:
        task_uuids = task_uuids_comma_separated.split(",")
    except Exception as e:
        error_result = (None, f"Error: Could not extract list of batch task UUIDs: {e}", 400)
        return error_result

    # Get Globus Compute client (using the endpoint identity)
    try:
        gcc = get_compute_client_from_globus_app()
    except Exception as e:
        error_result = (None, f"Error: Could not get the Globus Compute client: {e}", 500)
        return error_result

    # Get batch status from Globus and return the response
    try:
        # TODO: Switch back to this when Globus added a fix for the Exceptions
        #return gcc.get_batch_result(task_uuids), "", 200 
        
        # TODO: Remove what's below once we can use the above line
        response = {}
        for task_uuid in task_uuids:
            task = gcc.get_task(task_uuid)
            # Ensure the task data is serializable
            response[task_uuid] = {
                "pending": task["pending"],
                "status": task["status"],
                "result": task.get("result", None)
            }
        
        result = (response, "", 200)
        
        # Cache successful result for 30 seconds
        try:
            cache.set(cache_key, result, 30)
        except Exception as e:
            log.warning(f"Failed to cache batch status: {e}")
            
        return result
    
    # Error is the function execution failed
    except TaskExecutionFailed as e:
        error_result = (None, f"Error: TaskExecutionFailed: {e}", 400)
        # Cache error for shorter time (5 seconds)
        try:
            cache.set(cache_key, error_result, 5)
        except:
            pass
        return error_result

    # Other errors that could be un-related to the task execution (e.g. Globus connection)
    except Exception as e:
        error_result = (None, f"Error: Could not recover batch status: {e}", 500)
        try:
            cache.set(cache_key, error_result, 5)
        except:
            pass
        return error_result