import asyncio
import time
import json
from django.conf import settings
import globus_sdk
from globus_compute_sdk import Client, Executor
from globus_compute_sdk.errors import TaskExecutionFailed
from cachetools import TTLCache, cached
from utils.pydantic_models.batch import BatchStatusEnum

import logging
log = logging.getLogger(__name__)


# Exception to raise in case of errors
class ResourceServerError(Exception):
    pass

# Define separate cache object for Globus executor
executor_cache = TTLCache(maxsize=1024, ttl=60*10)

# Get authenticated Compute Client using secret
#@cached(cache=LRUCache(maxsize=1024))
@cached(cache=TTLCache(maxsize=1024, ttl=60*60))
def get_compute_client_from_globus_app() -> globus_sdk.GlobusHTTPResponse:
    """
    Create and return an authenticated Compute client using the Globus SDK ClientApp.

    Returns
    -------
        globus_compute_sdk.Client: Compute client to operate Globus Compute
    """

    # Try to create and return the Compute client
    try:
        return Client(
            app=globus_sdk.ClientApp(
                client_id=settings.POLARIS_ENDPOINT_ID,
                client_secret=settings.POLARIS_ENDPOINT_SECRET
            )
        )
    except Exception as e:
        raise ResourceServerError("Exception in creating client. Error",e)


# Get authenticated Compute Executor using existing client
@cached(cache=executor_cache)
def get_compute_executor(endpoint_id=None, client=None, amqp_port=443):
    """
    Create and return an authenticated Compute Executor using using existing client.

    Returns
    -------
        globus_compute_sdk.Executor: Compute Executor to operate Globus Compute
    """

    # Try to create and return the Compute executor
    try:
        return Executor(endpoint_id=endpoint_id, client=client, amqp_port=amqp_port)
    except Exception as e:
        raise ResourceServerError("Exception in creating executor. Error", e)


# Get endpoint status
@cached(cache=TTLCache(maxsize=1024, ttl=60))
def get_endpoint_status(endpoint_uuid=None, client=None, endpoint_slug=None):
    """
    Query the status of a Globus Compute endpoint. It caches the 
    result for x seconds to avoid generating a too-many-request
    from Globus services when sereval incoming requests target 
    an endpoint that is offline.
    """

    try:
        return client.get_endpoint_status(endpoint_uuid), ""
    except globus_sdk.GlobusAPIError as e:
        return None, f"Error: Cannot access the status of endpoint {endpoint_slug}: {e}"
    except Exception as e:
        return None, f"Error: Cannot access the status of endpoint {endpoint_slug}: {e}"


# Submit function and wait for result
async def submit_and_get_result(gce, endpoint_uuid, function_uuid, resources_ready, data=None, timeout=60*28):
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

    # Wait for the Globus Compute result using asyncio and coroutine
    try:
        asyncio_future = asyncio.wrap_future(future)
        result = await asyncio.wait_for(asyncio_future, timeout=timeout)
    except TimeoutError as e:
        if resources_ready:
            error_message = "Error: TimeoutError with compute resources not responding. Please try again or contact adminstrators."
        else:
            error_message = "Error: TimeoutError while attempting to acquire compute resources. Please try again in 10 minutes."
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


# Get batch status
@cached(cache=TTLCache(maxsize=1024, ttl=30))
def get_batch_status(task_uuids_comma_separated):
    """
    Get status and results (if available) of all Globus tasks 
    associated with a batch object. Return error message instead
    of rasing exeptions so that the response can be cached.
    """

    # Recover list of Globus task UUIDs tied to the batch
    try:
        task_uuids = task_uuids_comma_separated.split(",")
    except Exception as e:
        return None, f"Error: Could not extract list of batch task UUIDs: {e}", 400

    # Get Globus Compute client (using the endpoint identity)
    try:
        gcc = get_compute_client_from_globus_app()
    except Exception as e:
        return None, f"Error: Could not get the Globus Compute client: {e}", 500

    # Get batch status from Globus and return the response
    try:

        # TODO: Switch back to this when Globus added a fix for the Exceptions
        #return gcc.get_batch_result(task_uuids), "", 200 
        
        # TODO: Remove what's below once we can use the above line
        response = {}
        for task_uuid in task_uuids:
            task = gcc.get_task(task_uuid)
            response[task_uuid] = {
                "pending": task["pending"],
                "status": task["status"],
                "result": task.get("result", None)
            }
        return response, "", 200
    
    # Error is the function execution failed
    except TaskExecutionFailed as e:
        return None, f"Error: TaskExecutionFailed: {e}", 400

    # Other errors that could be un-related to the task execution (e.g. Globus connection)
    except Exception as e:
        return None, f"Error: Could not recover batch status: {e}", 500
    

# Get authenticated Globus specific flow client using secret
@cached(cache=TTLCache(maxsize=1024, ttl=60*60))
def get_specific_flow_client_from_globus_app(flow_id) -> globus_sdk.GlobusHTTPResponse:
    """Create and return an authenticated Globus specific flow client using the Globus SDK ClientApp."""

    # Try to create and return the Flows client
    try:
        return globus_sdk.SpecificFlowClient(
            flow_id=flow_id,
            app=globus_sdk.ClientApp(
                client_id=settings.POLARIS_ENDPOINT_ID,
                client_secret=settings.POLARIS_ENDPOINT_SECRET
            )
        )
    except Exception as e:
        raise ResourceServerError(f"Error: Could not create SpecificFlowClient: {e}")
    

# Get authenticated Globus Flows client using secret
@cached(cache=TTLCache(maxsize=1024, ttl=60*60))
def get_flows_client_from_globus_app() -> globus_sdk.GlobusHTTPResponse:
    """Create and return an authenticated Globus Flows client using the Globus SDK ClientApp."""

    # Try to create and return the Flows client
    try:
        return globus_sdk.FlowsClient(
            app=globus_sdk.ClientApp(
                client_id=settings.POLARIS_ENDPOINT_ID,
                client_secret=settings.POLARIS_ENDPOINT_SECRET
            )
        )
    except Exception as e:
        raise ResourceServerError(f"Error: Could not create FlowsClient: {e}")


# Get flow run status
@cached(cache=TTLCache(maxsize=1024, ttl=30))
def get_flow_status(globus_flow_run_uuid):
    """
    Get flow run status and results (if available)
    associated with a batch object. Return error message instead
    of rasing exeptions so that the response can be cached.
    """

    # Get Globus Flows client (using the endpoint identity)
    try:
        flows_client = get_flows_client_from_globus_app()
    except Exception as e:
        return None, str(e), 500

    # Get flow status from Globus
    try:
        flow_run = flows_client.get_run(globus_flow_run_uuid)
        status = flow_run["status"]
    except Exception as e:
        return None, f"Error: Could not recover flow run status: {e}", 500
    
    # Convert the status to match the API's nomenclature
    if status == "ACTIVE":
        status = BatchStatusEnum.pending.value
    elif status == "FAILED":
        status = BatchStatusEnum.failed.value
    elif status == "SUCCEEDED":
        status = BatchStatusEnum.completed.value
    else:
        return None, f"Error: Could not convert the flow run status: {e}", 400

    # Initialize variables related to results
    error_message = None
    compute_result = None
    data_access_url = None

    # Extract error message if the flow failed
    if status == BatchStatusEnum.failed.value:
        try:
            error_message = f"FlowFailed: {json.dumps(flow_run['details']['description'])}"
        except Exception as e:
            return None, f"Error: Could not extract failed flow details: {e}", 400
    
    # Extract results if the flow completed
    elif status == BatchStatusEnum.completed.value:
        try:
            compute_result = flow_run["details"]["output"]["ComputeInference_output"]["details"]["result"][0]
        except Exception as e:
            return None, f"Error: Could not extract compute result from completed flow: {e}", 400
        try:
            data_access_url = flow_run["details"]["output"]["ShareResults_output"]["details"]["result"][0]["data_access"]
        except Exception as e:
            return None, f"Error: Could not extract data access URL from completed flow: {e}", 400
    
    # Return the status response
    status_response = {
        "status": status,
        "compute_result": compute_result,
        "data_access_url": data_access_url,
        "error_message": error_message
    }
    return status_response, "", 200
    