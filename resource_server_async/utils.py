from ninja import FilterSchema
from django.utils import timezone
from django.db import IntegrityError
from enum import Enum
from utils.serializers import (
    OpenAICompletionsParamSerializer, 
    OpenAIChatCompletionsParamSerializer, 
    OpenAIEmbeddingsParamSerializer,
    BatchParamSerializer,
    #OpenAIFileUploadParamSerializer
)
from rest_framework.exceptions import ValidationError
import json
from uuid import UUID
from asgiref.sync import sync_to_async
from asyncache import cached as asynccached
from cachetools import TTLCache
from utils.globus_utils import submit_and_get_result, get_endpoint_status, get_batch_status

# Constants
ALLOWED_FRAMEWORKS = {
    "polaris": ["llama-cpp", "vllm"],
    "sophia": ["vllm","infinity"]
}
ALLOWED_OPENAI_ENDPOINTS = {
    "polaris": ["chat/completions", "completions", "embeddings"],
    "sophia": ["chat/completions", "completions", "embeddings"]
}
ALLOWED_CLUSTERS = list(ALLOWED_FRAMEWORKS.keys())

ALLOWED_QSTAT_ENDPOINTS = {
    "sophia":{
        "endpoint_uuid":"23c852cb-e780-49d3-9103-5ef4b1fcfd1c",
        "function_uuid":"d2a1c52d-6776-4e22-80b5-fbaae6519a72"
    }
}


# Batch list filter
class BatchStatusEnum(str, Enum):
    in_progress = 'in_progress'
    failed = 'failed'
    completed = 'completed'
class BatchListFilter(FilterSchema):
    status: BatchStatusEnum = None


# Exception to raise in case of errors
class ResourceServerError(Exception):
    pass


# Validate cluster and framework
def validate_cluster_framework(cluster: str, framework: str):

    # Error message if cluster not available
    if not cluster in ALLOWED_CLUSTERS:
        return f"Error: {cluster} cluster not supported. Currently supporting {ALLOWED_CLUSTERS}."
    
    # Error message if framework not available
    if not framework in ALLOWED_FRAMEWORKS[cluster]:
        return f"Error: {framework} framework not supported. Currently supporting {ALLOWED_FRAMEWORKS[cluster]}."

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
    if not framework in ALLOWED_FRAMEWORKS[cluster]:
        return f"Error: {framework} framework not supported. Currently supporting {ALLOWED_FRAMEWORKS[cluster]}."
    
    # Error message if openai endpoint not available
    if not openai_endpoint in ALLOWED_OPENAI_ENDPOINTS[cluster]:
        return f"Error: {openai_endpoint} openai endpoint not supported. Currently supporting {ALLOWED_OPENAI_ENDPOINTS[cluster]}."

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
        
    # Select the appropriate data validation serializer based on the openai endpoint
    if "chat/completions" in openai_endpoint:
        serializer_class = OpenAIChatCompletionsParamSerializer
    elif "completion" in openai_endpoint:
        serializer_class = OpenAICompletionsParamSerializer
    elif "embeddings" in openai_endpoint:
        serializer_class = OpenAIEmbeddingsParamSerializer
    else:
        return {"error": f"Error: {openai_endpoint} endpoint not supported."}
        
    # Decode request body into a dictionary
    try:
        model_params = json.loads(request.body.decode("utf-8"))
    except:
        return {"error": f"Error: Request body cannot be decoded."}
        
    # Send an error if the input data is not valid
    try:
        serializer = serializer_class(data=model_params)
        is_valid = serializer.is_valid(raise_exception=True)
    except ValidationError as e:
        return {"error": f"Error: Could not validate data: {e}"}
    except Exception as e:
        return {"error": f"Error: Something went wrong in validating with serializer: {e}"}

    # Add the 'url' parameter to model_params
    model_params['openai_endpoint'] = openai_endpoint

    # Build request data if nothing wrong was caught
    return {"model_params": model_params}


# Validate batch body
def validate_batch_body(request):
    """Build data dictionary for inference batch request if user inputs are valid."""
    return validate_body(request, BatchParamSerializer)


# Validate file body
#def validate_file_body(request):
#    """Build data dictionary for inference file path import request if user inputs are valid."""
#    return validate_body(request, OpenAIFileUploadParamSerializer)


# Validate body
def validate_body(request, serializer_class):
    """Build data dictionary from user inputs if valid from given parameter serializer."""
                
    # Decode request body into a dictionary
    try:
        params = json.loads(request.body.decode("utf-8"))
    except:
        return {"error": f"Error: Request body cannot be decoded."}

    # Send an error if the input data is not valid
    try:
        serializer = serializer_class(data=params)
        _ = serializer.is_valid(raise_exception=True)
    except ValidationError as e:
        return {"error": f"Error: Could not validate data: {e}"}
    except Exception as e:
        return {"error": f"Error: Something went wrong in validating with serializer: {e}"}

    # Build request data if nothing wrong was caught
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
            uuid_obj = UUID(uuid_to_test).version
        except Exception as e:
            return [], f"Error: Could not extract UUID format from the database. {e}"
    
    # Return the list of group UUIDs
    return group_uuids, ""


# Get qstat details
@asynccached(TTLCache(maxsize=1024, ttl=30))
async def get_qstat_details(cluster, gcc, gce, timeout=60):
    """
    Collect details on all jobs running/submitted on a given cluster.
    Here return the error message instead of raising exceptions to 
    make sure the outcome gets cached.
    Returns result, task_uuid, error_message, error_code
    """

    # Gather the qstat endpoint info
    if cluster in ALLOWED_QSTAT_ENDPOINTS:
        endpoint_slug = f"{cluster}/jobs"
        endpoint_uuid = ALLOWED_QSTAT_ENDPOINTS[cluster]["endpoint_uuid"]
        function_uuid = ALLOWED_QSTAT_ENDPOINTS[cluster]["function_uuid"]
    else:
        return None, None, f"Error: no qstat endpoint exists for cluster {cluster}.", None
    
    # Get the status of the qstat endpoint
    # NOTE: Do not await here, cache the "first" request to avoid too-many-requests Globus error
    endpoint_status, error_message = get_endpoint_status(
        endpoint_uuid=endpoint_uuid, client=gcc, endpoint_slug=endpoint_slug
    )
    if len(error_message) > 0:
        return None, None, error_message, None
        
    # Return error message if endpoint is not online
    if not endpoint_status["status"] == "online":
        return None, None, f"Error: Endpoint {endpoint_slug} is offline.", None
    
    # Submit task and wait for result
    result, task_uuid, error_message, error_code = await submit_and_get_result(
        gce, endpoint_uuid, function_uuid, True, timeout=timeout
    )
    if len(error_message) > 0:
        return None, task_uuid, error_message, error_code

    # Return qstat result without error_message
    return result, task_uuid, "", 200


# Update batch status result
async def update_batch_status_result(batch):
    """
    From a database Batch object, query batch status from Globus
    if necessary, update the "status" and "result" fields in the
    database, and return the batch details.
    Returns: status, result, "", 200 OR "", "", error_message, error_code
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
                batch.error = error_message
                await update_database(db_object=batch)
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

        # Gather satus states for each Globus task
        #pending_list = []
        #status_list = []
        #for status in status_response.values():
        #    pending_list.append(status["pending"])
        #    status_list.append(status["status"])

        # In progress
        if pending_list.count(True) > 0:
            batch_status = "in_progress"
            batch.in_progress_at = timezone.now()

        # Completed
        elif status_list.count("success") == len(status_list):
            batch_status = "completed"
            batch.completed_at = timezone.now()

        # Failed
        #TODO: Figure out how to be resilient and restart failed jobs?
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
    except Exception as e:
        return "", "", f"Error: Could not update batch {batch.batch_id} status in database: {e}", 400
    
    # If batch result is available ...
    if batch_status == "completed":

        # Parse Globus batch status response to extract result
        try:
            result_list = [status["result"] for status in status_response_values]
            #for status in status_response.values():
            #    result_list.append(status["result"])
            batch_result = ",".join(result_list) + ","
            batch_result = batch_result[:-1]

        # Error if something went wrong while parsing the batch status response
        except Exception as e:
            return "", "", f"Error: Could not parse get_batch_status response for result: {e}", 400

        # Update batch result in the database
        try:
            batch.result = batch_result
            await update_database(db_object=batch)
        except Exception as e:
            return "", "", f"Error: Could not update batch {batch.batch_id} result in database: {e}", 400

    # Return the new status if nothing went wrong
    return batch.status, batch.result, "", 200


# Update database
async def update_database(db_Model=None, db_data=None, db_object=None):
    """Create new entry in the database or save the modification of existing entry."""

    # Create new database object if needed
    try:
        if isinstance(db_object, type(None)):
            db_object = db_Model(**db_data)
    except Exception as e:
        raise Exception(f"Could not create database model from db_data: {e}")

    # Save database entry
    try:
        await sync_to_async(db_object.save, thread_sensitive=True)()
    except IntegrityError as e:
        raise IntegrityError(f"Could not save {type(db_Model)} database entry: {e}")
    except Exception as e:
        raise Exception(f"Could not save {type(db_Model)} database entry: {e}")