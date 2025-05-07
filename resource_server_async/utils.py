from ninja import FilterSchema
from django.utils import timezone
from django.db import IntegrityError
from enum import Enum
from utils.pydantic_models.openai_chat_completions import OpenAIChatCompletionsPydantic
from utils.pydantic_models.openai_completions import OpenAICompletionsPydantic
from utils.pydantic_models.openai_embeddings import OpenAIEmbeddingsPydantic
from utils.pydantic_models.batch import BatchPydantic, UploadedBatchFilePydantic
from rest_framework.exceptions import ValidationError
from resource_server.models import Batch, Endpoint
import json
from uuid import UUID
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
from django.conf import settings # Import settings
# Import models to load configuration dynamically
# from resource_server.models import Cluster, SupportedBackend, SupportedOpenAIEndpoint, ClusterStatusEndpoint # Removed
from collections import defaultdict
import logging # Add logging

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


# Validate file body
#def validate_file_body(request):
#    """Build data dictionary for inference file path import request if user inputs are valid."""
#    return validate_body(request, OpenAIFileUploadParamSerializer)


# Validate body
def validate_body(request, pydantic_class):
    """Validate body data from incoming user requests against a given pydantic model."""
                
    # Decode request body into a dictionary
    try:
        params = json.loads(request.body.decode("utf-8"))
    except:
        return {"error": f"Error: Request body cannot be decoded."}

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

    # Gather the qstat endpoint info using the loaded config
    qstat_config = ALLOWED_QSTAT_ENDPOINTS.get(cluster)
    if not qstat_config:
        return None, None, f"Error: no qstat endpoint configuration exists for cluster {cluster}.", None

    endpoint_slug = f"{cluster}/jobs"
    endpoint_uuid = qstat_config["endpoint_uuid"]
    function_uuid = qstat_config["function_uuid"]

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
                batch.error = error_message
                await update_database(db_object=batch)
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
                    batch.error = "Error: Globus task lost. Likely due to node failure or endpoint restart."
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

        # Update batch result in the database
        try:
            batch.result = batch_result
            await update_database(db_object=batch)
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
        gce = get_compute_executor(client=gcc, amqp_port=443)
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

# Read uploaded file
async def read_uploaded_file(file):
    
    # Try to read the file
    try:
        data = await sync_to_async(file.read)()
    except Exception as e:
        raise Exception(f"Error: Could not read uploaded file: {e}")
    
    # Decode request body into a list of input entries, and return data
    try:
        return data.decode("utf-8").split("\n")
    except Exception as e:
        raise Exception(f"Error: Could not decode uploaded file: {e}")


# Validate uploaded batch data
def validate_uploaded_batch_data(data):

    # Make sure data is a list
    if not isinstance(data, list):
        raise Exception("Error: uploaded data must be a list.")

    # For each entry ...
    for i_entry in range(len(data)):

        # Base error message
        base_error = f"Error: line {i_entry+1} in uploaded file"

        # Convert the entry string into a dictionary
        try:
            entry_dict = json.loads(data[i_entry])
        except Exception as e:
            raise Exception(f"{base_error}: Could not convert to dictionary: {e}")

        # Validate entry
        try:
            _ = UploadedBatchFilePydantic(**entry_dict)
        except ValidationError as e:
            raise ValidationError(f"{base_error}: {e}", 400)
        except Exception as e:
            raise Exception(f"{base_error}: Data validation went wrong: {e}", 400)
        

# Validate number of active batches
async def validate_number_of_active_batches(username):
    
    # Collect the number of active batches associated with the given username
    try:
        number_of_active_batches = 0
        async for batch in Batch.objects.filter(username=username, status__in=["pending", "running"]):
            number_of_active_batches += 1
    except Exception as e:
        raise Exception(f"Error: Could not query active batches owned by user: {e}")

    # Raise an error if the user is not allowed to submit another batch
    if number_of_active_batches >= settings.MAX_BATCHES_PER_USER:
        raise Exception(f"Error: Quota of {settings.MAX_BATCHES_PER_USER} active batch(es) per user exceeded.")
    

# Get endpoint object from slug
async def get_endpoint_from_slug(endpoint_slug):

    # Find endpoint in the database and return the object
    try:
        return await sync_to_async(Endpoint.objects.get)(endpoint_slug=endpoint_slug)
    
    # Raise errors if the endpoint does not exist
    except Endpoint.DoesNotExist:
        raise Endpoint.DoesNotExist(f"Error: The requested endpoint {endpoint_slug} does not exist.")
    
    # Raise errors if something went wrong more broadly
    except Exception as e:
        raise Exception(f"Error: Could not extract endpoint and function UUIDs: {e}")
    

# Validate whether the user can access a given endpoint
def validate_user_access(atv_response, endpoint):

    # Gather the list of Globus Group memberships of the authenticated user
    try:
        user_group_uuids = atv_response.user_group_uuids
    except Exception as e:
        raise Exception(f"Error: Could access user's Globus Group memberships: {e}")
    
    # Extract the list of allowed group UUIDs tied to the targetted endpoint
    allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
    if len(error_message) > 0:
        raise Exception(error_message)
    
    # Block access if the user is not a member of at least one of the required groups
    if len(allowed_globus_groups) > 0: # This is important to check if there is a restriction
        if len(set(user_group_uuids).intersection(allowed_globus_groups)) == 0:
            raise Exception(f"Error: Permission denied to endpoint {endpoint.endpoint_slug}.")
