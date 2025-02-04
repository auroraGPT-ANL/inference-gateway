import uuid
import json
from django.utils import timezone
from django.utils.text import slugify
from django.http import HttpResponse
from django.db import IntegrityError

# Tool to log access requests
import logging
log = logging.getLogger(__name__)

# Local utils
from utils.auth_utils import validate_access_token
import utils.globus_utils as globus_utils
from resource_server_async.utils import (
    validate_url_inputs, 
    validate_cluster_framework,
    extract_prompt, 
    validate_request_body,
    validate_batch_body,
    #validate_file_body,
    extract_group_uuids,
    get_qstat_details,
    update_batch_status,
    update_database,
    ALLOWED_QSTAT_ENDPOINTS,
    BatchListFilter,
)
log.info("Utils functions loaded.")

# Django database
from resource_server.models import Endpoint, Log, ListEndpointsLog, Batch#, File

# Async tools
from asgiref.sync import sync_to_async

# Ninja API
from ninja import NinjaAPI, Router, Query
api = NinjaAPI(urls_namespace='resource_server_async_api')
router = Router()


# List Endpoints (GET)
@router.get("/list-endpoints")
async def get_list_endpoints(request):
    """GET request to list the available frameworks and models."""

    # Check if request is authenticated
    atv_response = validate_access_token(request)
    if not atv_response.is_valid:
        return await get_plain_response(atv_response.error_message, atv_response.error_code)
    
    # Gather the list of Globus Group memberships of the authenticated user
    try:
        user_group_uuids = atv_response.user_group_uuids
    except Exception as e:
        return await get_plain_response(f"Error: Could access user's Globus Group memberships: {e}", 400)

    # Collect endpoints objects from the database
    try:
        endpoint_list = await sync_to_async(list)(Endpoint.objects.all())
    except Exception as e:
        return await get_plain_response(f"Error: Could not access Endpoint database entries: {e}", 400)

    # Prepare the list of available frameworks and models
    all_endpoints = {"clusters": {}}
    try:

        # For each database endpoint entry ...
        for endpoint in endpoint_list:

            # Extract the list of allowed group UUIDs tied to the endpoint
            allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
            if len(error_message) > 0:
                log.error(error_message)
                return HttpResponse(json.dumps(error_message), status=400)
    
            # If the user is allowed to see the endpoint ...
            # i.e. if (there is no restriction) or (if the user is at least part of one allowed groups) ...
            if len(allowed_globus_groups) == 0 or len(set(user_group_uuids).intersection(allowed_globus_groups)) > 0:

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
        return await get_plain_response(f"Error: Could not generate list of frameworks and models from database: {e}", 400)

    # Return list of frameworks and models
    return HttpResponse(json.dumps(all_endpoints), status=200)


# List Endpoints Detailed (GET)
@router.get("/list-endpoints-detailed")
async def get_list_endpoints_detailed(request):
    """GET request to list the available frameworks and models with live status."""

    # Check if request is authenticated
    atv_response = validate_access_token(request)
    if not atv_response.is_valid:
        return await get_plain_response(atv_response.error_message, atv_response.error_code)
    
    # Start the data dictionary for the database entry
    # The actual database entry creation is performed in the get_list_response() function
    db_data = {
        "name": atv_response.name,
        "username": atv_response.username,
        "timestamp_receive": timezone.now(),
        "endpoint_slugs": "",
        "task_uuids": ""
    }
    
    # Gather the list of Globus Group memberships of the authenticated user
    try:
        user_group_uuids = atv_response.user_group_uuids
    except Exception as e:
        return await get_list_response(db_data, f"Error: Could access user's Globus Group memberships: {e}", 400)

    # Collect endpoints objects from the database
    try:
        endpoint_list = await sync_to_async(list)(Endpoint.objects.all())
    except Exception as e:
        return await get_list_response(db_data, f"Error: Could not access Endpoint database entries: {e}", 400)
    
    # Get Globus Compute client and executor
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests
    # NOTE: Do not include endpoint_id argument, otherwise it will cache multiple executors
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
        gce = globus_utils.get_compute_executor(client=gcc, amqp_port=443)
    except Exception as e:
        return await get_list_response(db_data, f"Error: Could not get the Globus Compute client or executor: {e}", 500)

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
                return await get_list_response(db_data, json.dumps(error_message), 400)
    
            # If the user is allowed to see the endpoint ...
            # i.e. if (there is no restriction) or (if the user is at least part of one allowed groups) ...
            if len(allowed_globus_groups) == 0 or len(set(user_group_uuids).intersection(allowed_globus_groups)) > 0:

                # If this is a new cluster for the dictionary ...
                if not endpoint.cluster in all_endpoints["clusters"]:

                    # Add new entry to the dictionary
                    all_endpoints["clusters"][endpoint.cluster] = {
                        "base_url": f"/resource_server/{endpoint.cluster}",
                        "frameworks": {}
                    }

                    # If it is possible to collect qstat details on the jobs running/queued on the cluster ...
                    if endpoint.cluster in ALLOWED_QSTAT_ENDPOINTS:

                        # Update database entry
                        if len(db_data["endpoint_slugs"]) > 0:
                            db_data["endpoint_slugs"] += "; "
                        db_data["endpoint_slugs"] += f"{endpoint.cluster}/jobs"

                        # Collect qstat details on the jobs running/queued on the cluster
                        qstat_result, task_uuid, error_message, error_code = await get_qstat_details(
                            endpoint.cluster, gcc, gce, timeout=60
                        )
                        qstat_result = json.loads(qstat_result)

                        # Update database entry                        
                        if len(db_data["task_uuids"]) > 0:
                            db_data["task_uuids"] += "; "
                        db_data["task_uuids"] += str(task_uuid)

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
                    return await get_list_response(db_data, error_message, 500)
                
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
        return await get_list_response(db_data, f"Error: Could not generate list of frameworks and models from database: {e}", 400)

    # Return list of frameworks and models
    return await get_list_response(db_data, all_endpoints, 200)


# Endpoint Status (GET)
@router.get("/{cluster}/{framework}/{path:model}/status")
async def get_endpoint_status(request, cluster: str, framework: str, model: str, *args, **kwargs):
    """GET request to get a detailed status of a specific Globus Compute endpoint."""

    # Check if request is authenticated
    atv_response = validate_access_token(request)
    if not atv_response.is_valid:
        return await get_plain_response(atv_response.error_message, atv_response.error_code)
    
    # Start the data dictionary for the database entry
    # The actual database entry creation is performed in the get_list_response() function
    db_data = {
        "name": atv_response.name,
        "username": atv_response.username,
        "timestamp_receive": timezone.now(),
        "endpoint_slugs": "",
        "task_uuids": ""
    }

    # Gather the list of Globus Group memberships of the authenticated user
    try:
        user_group_uuids = atv_response.user_group_uuids
    except Exception as e:
        return await get_list_response(db_data, f"Error: Could access user's Globus Group memberships: {e}", 400)

    # Get the requested endpoint from the database
    endpoint_slug = slugify(" ".join([cluster, framework, model]))
    try:
        endpoint = await sync_to_async(Endpoint.objects.get)(endpoint_slug=endpoint_slug)
    except Endpoint.DoesNotExist:
        return await get_list_response(db_data, f"Error: The requested endpoint {endpoint_slug} does not exist.", 400)
    except Exception as e:
        return await get_list_response(db_data, f"Error: Could not extract endpoint and function UUIDs: {e}", 400)
    
    # Error message if user is not allowed to see the endpoint
    allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
    if len(error_message) > 0:
        return await get_list_response(db_data, json.dumps(error_message), 400)
    if len(allowed_globus_groups) > 0 and len(set(user_group_uuids).intersection(allowed_globus_groups)) == 0:
        return await get_list_response(db_data, f"Error: User not authorized to access endpoint {endpoint_slug}", 401)

    # Get Globus Compute client and executor
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests
    # NOTE: Do not include endpoint_id argument, otherwise it will cache multiple executors
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
        gce = globus_utils.get_compute_executor(client=gcc, amqp_port=443)
    except Exception as e:
        return await get_list_response(db_data, f"Error: Could not get the Globus Compute client or executor: {e}", 500)
    
    # Extract the status of the current Globus Compute endpoint
    endpoint_status, error_message = globus_utils.get_endpoint_status(
        endpoint_uuid=endpoint.endpoint_uuid, client=gcc, endpoint_slug=endpoint.endpoint_slug
    )
    if len(error_message) > 0:
        return await get_list_response(db_data, error_message, 500)

    # If it is possible to collect qstat details on the jobs running/queued on the cluster ...
    model_status = {}
    if endpoint_status["status"] == "online":
        if cluster in ALLOWED_QSTAT_ENDPOINTS:

            # Update database entry
            db_data["endpoint_slugs"] = f"{cluster}/jobs"

            # Collect qstat details on the jobs running/queued on the cluster
            qstat_result, task_uuid, error_message, error_code = await get_qstat_details(
                cluster, gcc, gce, timeout=60
            )
            qstat_result = json.loads(qstat_result)

            # Update database entry
            db_data["task_uuids"] = str(task_uuid)

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
    return await get_list_response(db_data, status, 200)


# List Endpoints (GET)
@router.get("/{cluster}/jobs")
async def get_jobs(request, cluster:str):
    """GET request to list the available frameworks and models."""

    # Check if request is authenticated
    atv_response = validate_access_token(request)
    if not atv_response.is_valid:
        return await get_plain_response(atv_response.error_message, atv_response.error_code)
    
    # Start the data dictionary for the database entry
    # The actual database entry creation is performed in the get_response() function
    db_data = {
        "name": atv_response.name,
        "username": atv_response.username,
        "timestamp_receive": timezone.now(),
        "endpoint_slugs": "",
        "task_uuids": ""
    }

    # Make sure the URL inputs point to an available endpoint 
    error_message = validate_url_inputs(cluster, framework="vllm", openai_endpoint="chat/completions")
    if len(error_message):
        return await get_list_response(db_data, error_message, 400)

    # Get Globus Compute client and executor
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests
    # NOTE: Do not include endpoint_id argument, otherwise it will cache multiple executors
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
        gce = globus_utils.get_compute_executor(client=gcc, amqp_port=443)
    except Exception as e:
        return await get_list_response(db_data, f"Error: Could not get the Globus Compute client or executor: {e}", 500)
    
    # Collect (qstat) details on the jobs running/queued on the cluster
    db_data["endpoint_slugs"] = f"{cluster}/jobs"
    result, task_uuid, error_message, error_code = await get_qstat_details(cluster, gcc, gce, timeout=60)
    if len(error_message) > 0:
        return await get_list_response(db_data, error_message, error_code)
    result = json.loads(result)
    db_data["task_uuids"] = task_uuid
    
     # Return Globus Compute results
    return await get_list_response(db_data, result, 200)


# Import file path (POST)
# TODO: Should we check if the input file path already exists in the database?
# TODO: Use primary identity username to claim ownership on files and batches
#@router.post("/v1/files")
#async def post_batch_file(request, *args, **kwargs):
#    """POST request to import input file path for running batches."""
#
#    # Check if request is authenticated
#    atv_response = validate_access_token(request)
#    if not atv_response.is_valid:
#        return await get_plain_response(atv_response.error_message, atv_response.error_code)
#    
#    # Start the data dictionary for the database entry
#    # The actual database entry creation is performed in the get_response() function
#    db_data = {
#        "input_file_id": str(uuid.uuid4()),
#        "name": atv_response.name,
#        "username": atv_response.username,
#        "created_at": timezone.now(),
#    }
#
#    # Validate and build the file input request data
#    file_data = validate_file_body(request)
#    if "error" in file_data.keys():
#        return await get_batch_response(db_data, file_data['error'], 400, db_Model=File)
#        
#    # Update database entry
#    db_data["input_file_path"] = file_data["input_file_path"]
#
#    # Create file entry in the database and return the file UUID to the user
#    response = {
#        "id": db_data["input_file_id"],
#        "object": "file",
#        "created_at": int(db_data["created_at"].timestamp()),
#        "filename": db_data["input_file_path"],
#        "purpose": "",
#    }
#    return await get_batch_response(db_data, json.dumps(response), 200, db_Model=File)


# Inference batch (POST)
# TODO: Use primary identity username to claim ownership on files and batches
@router.post("/{cluster}/{framework}/v1/batches")
async def post_batch_inference(request, cluster: str, framework: str, *args, **kwargs):
    """POST request to send a batch to Globus Compute endpoints."""

    # Check if request is authenticated
    atv_response = validate_access_token(request)
    if not atv_response.is_valid:
        return await get_plain_response(atv_response.error_message, atv_response.error_code)
    
    # Gather the list of Globus Group memberships of the authenticated user
    try:
        user_group_uuids = atv_response.user_group_uuids
    except Exception as e:
        return await get_plain_response(f"Error: Could access user's Globus Group memberships: {e}", 400)
    
    # Start the data dictionary for the database entry
    # The actual database entry creation is performed in the get_response() function
    db_data = {
        "batch_id": str(uuid.uuid4()),
        "name": atv_response.name,
        "username": atv_response.username,
        "created_at": timezone.now(),
    }
    
    # Validate and build the inference request data
    batch_data = validate_batch_body(request)
    if "error" in batch_data.keys():
        return await get_batch_response(db_data, batch_data['error'], 400, db_Model=Batch)

    # Strip the last forward slash of endpoint if needed
    #if batch_data["endpoint"][-1] == "/":
    #    batch_data["endpoint"] = batch_data["endpoint"][:-1]

    # Make sure the URL inputs point to an available endpoint 
    #error_message = validate_url_inputs(cluster, framework, batch_data["endpoint"])
    error_message = validate_cluster_framework(cluster, framework)
    if len(error_message):
        return await get_batch_response(db_data, error_message, 400, db_Model=Batch)
    
    # Update database entry
    db_data["cluster"] = cluster
    db_data["framework"] = framework
    db_data["model"] = batch_data["model"]
    db_data["input_file"] = batch_data["input_file"]
    db_data["output_file_path"] = batch_data.get("output_file_path", "")
    db_data["status"] = "failed" # First assume it fails, overwrite if successful

    # Build the requested endpoint slug
    endpoint_slug = slugify(" ".join([cluster, framework, batch_data["model"].lower()]))
    
    # Pull the targetted endpoint from database to check if user is permitted to run this model
    try:
        endpoint = await sync_to_async(Endpoint.objects.get)(endpoint_slug=endpoint_slug)
    except Endpoint.DoesNotExist:
        return await get_batch_response(db_data, f"Error: Endpoint {endpoint_slug} does not exist.", 400, db_Model=Batch)
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Could not extract endpoint: {e}", 400, db_Model=Batch)
    
    # Extract the list of allowed group UUIDs tied to the targetted endpoint
    allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
    if len(error_message) > 0:
        return await get_batch_response(db_data, error_message, 401, db_Model=Batch)
    
    # Block access if the user is not a member of at least one of the required groups
    if len(allowed_globus_groups) > 0: # This is important to check if there is a restriction
        if len(set(user_group_uuids).intersection(allowed_globus_groups)) == 0:
            return await get_batch_response(db_data, f"Permission denied to endpoint {endpoint_slug}.", 401, db_Model=Batch)
        
    # Make sure the endpoint has batch UUIDs
    if len(endpoint.batch_endpoint_uuid) == 0 or len(endpoint.batch_function_uuid) == 0:
        return await get_batch_response(db_data, f"Endpoint {endpoint_slug} does not have batch enabled.", 501, db_Model=Batch)

    # Error if an ongoing batch already exists with the same input_file
    # TODO: More checks here to make sure we don't duplicate batches?
    #       Do we allow multiple batches on the same file on different clusters?
    #       Do we allow re-run of the same batch if previous ones are completed?
    try:
        async for batch in Batch.objects.filter(input_file=batch_data["input_file"]):
            if not batch.status in ["failed", "completed"]:
                error_message = f"Error: Input file {batch_data['input_file']} already used by ongoing batch {batch.batch_id}."
                return await get_batch_response(db_data, error_message, 400, db_Model=Batch)
    except Batch.DoesNotExist:
        pass # Batch can be submitted if the input_file is not used by any other batches
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Could not filter Batch database entries: {e}", 400, db_Model=Batch)

    # Get Globus Compute client (using the endpoint identity)
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Could not get the Globus Compute client: {e}", 500, db_Model=Batch)

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
        return await get_batch_response(db_data, error_message, 500, db_Model=Batch)

    # Error if the endpoint is not online
    if not endpoint_status["status"] == "online":
        return await get_batch_response(db_data, f"Error: Endpoint {endpoint_slug} is offline.", 503, db_Model=Batch)
    
    # Recover file database entry
    #try:
    #    file = await sync_to_async(File.objects.get)(input_file_id=batch_data["input_file_id"])
    #except Batch.DoesNotExist:
    #    error_message = f"Error: Input file ID {batch_data['input_file_id']} does not exist."
    #    return await get_batch_response(db_data, error_message, 400, db_Model=Batch)
    #except Exception as e:
    #    error_message = f"Error: Could not extract Input file ID {batch_data['input_file_id']} from database: {e}"
    #    return await get_batch_response(db_data, error_message, 400, db_Model=Batch)

    # Recover file path and check if the user owns the batch job
    #try:
    #    input_file_path = file.input_file_path
    #    if not file.username == atv_response.username:
    #        error_message = f"Error: Permission denied to File {batch_data['input_file_id']}."
    #        return await get_batch_response(db_data, error_message, 403, db_Model=Batch)
    #except Exception as e:
    #    error_message = f"Error: Something went accessing file.username or file.input_file_path: {e}"
    #    return await get_batch_response(db_data, error_message, 400, db_Model=Batch)

    # Prepare input parameter for the compute tasks
    # NOTE: This is already in list format in case we submit multiple tasks per batch
    params_list = [
        {
            "model_params": {
                "input_file": batch_data["input_file"],
                "model": batch_data["model"]
            },
            "batch_id": db_data["batch_id"],
        }
    ]
    if "output_file_path" in batch_data:
        params_list[0]["model_params"]["output_file_path"] = batch_data["output_file_path"]

    # Prepare the batch job
    try:
        batch = gcc.create_batch()
        for params in params_list:
            batch.add(function_id=function_uuid, args=[params])
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Could not create Globus Compute batch: {e}", 500, db_Model=Batch)

    # Submit batch to Globus Compute and update batch status if submission is successful
    try:
        batch_response = gcc.batch_run(endpoint_id=endpoint_uuid, batch=batch)
        db_data["status"] = "submitted"
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Could not submit the Globus Compute batch: {e}", 500, db_Model=Batch)
    
    # Extract the Globus batch UUID from submission
    try:
        db_data["globus_batch_uuid"] = batch_response["request_id"]
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Batch submitted but no batch UUID recovered: {e}", 400, db_Model=Batch)

    # Extract the batch and task UUIDs from submission
    try:
        db_data["globus_task_uuids"] = ""
        for _, task_uuids in batch_response["tasks"].items():
                db_data["globus_task_uuids"] += ",".join(task_uuids) + ","
        db_data["globus_task_uuids"] = db_data["globus_task_uuids"][:-1]
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Batch submitted but no task UUID recovered: {e}", 400, db_Model=Batch)

    # Create batch entry in the database and return response to the user
    # TODO: Make serializer for batch object
    db_data["status"] = "pending"
    response = {
        "batch_id": db_data["batch_id"],
        "input_file": db_data["input_file"],
        "status": db_data["status"]
    }
    return await get_batch_response(db_data, json.dumps(response), 200, db_Model=Batch)


# List of batches (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches")
async def get_batch_list(request, filters: BatchListFilter = Query(...), *args, **kwargs):
    """GET request to list all batches linked to the authenticated user."""

    # Check if request is authenticated
    atv_response = validate_access_token(request)
    if not atv_response.is_valid:
        return await get_plain_response(atv_response.error_message, atv_response.error_code)

    # Declare the list of batches to be returned to the user
    batch_list = []
    try:

        # Build database filters based on username and optional batch status
        filter_params = {"username": atv_response.username}
        if isinstance(filters.status, str):
            filter_params["status"] = filters.status.value

        # For each filtered batch object owned by the user ...
        async for batch in Batch.objects.filter(**filter_params):

            # Get a status update for the batch (this will update the database if needed)
            batch_status, error_message, code = await update_batch_status(batch)
            if len(error_message) > 0:
                return await get_plain_response(error_message, code)

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
    except Batch.DoesNotExist:
        pass

    # Error message if something went wrong
    except Exception as e:
        return await get_plain_response(f"Error: Could not filter Batch database entries: {e}", 400)

    # Return list of batches
    return await get_plain_response(batch_list, 200)


# Inference batch status (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches/{batch_id}")
async def get_batch_status(request, batch_id: str, *args, **kwargs):
    """GET request to query status of an existing batch job."""

    # Check if request is authenticated
    atv_response = validate_access_token(request)
    if not atv_response.is_valid:
        return await get_plain_response(atv_response.error_message, atv_response.error_code)

    # Recover batch object in the database
    try:
        batch = await sync_to_async(Batch.objects.get)(batch_id=batch_id)
    except Batch.DoesNotExist:
        return await get_plain_response(f"Error: Batch {batch_id} does not exist.", 400)
    except Exception as e:
        return await get_plain_response(f"Error: Could not access Batch {batch_id} from database: {e}", 400)

    # Make sure user has permission to access this batch_id
    try:
        if not atv_response.username == batch.username:
            error_message = f"Error: Permission denied to Batch {batch_id}."
            return await get_plain_response(error_message, 403)
    except Exception as e:
        return await get_plain_response(f"Error: Something went wrong while parsing Batch {batch_id}: {e}", 400)
    
    # Get a status update for the batch (this will update the database if needed)
    batch_status, error_message, code = await update_batch_status(batch)
    if len(error_message) > 0:
        return await get_plain_response(error_message, code)

    # Return status of the batch job
    return await get_plain_response(batch_status, 200)


# Inference batch result (GET)
# TODO: Use primary identity username to claim ownership on files and batches
@router.get("/v1/batches/{batch_id}/result")
async def get_batch_result(request, batch_id: str, *args, **kwargs):
    """GET request to recover result from an existing batch job."""

    # Check if request is authenticated
    atv_response = validate_access_token(request)
    if not atv_response.is_valid:
        return await get_plain_response(atv_response.error_message, atv_response.error_code)

    # Recover batch object in the database
    try:
        batch = await sync_to_async(Batch.objects.get)(batch_id=batch_id)
    except Batch.DoesNotExist:
        return await get_plain_response(f"Error: Batch {batch_id} does not exist.", 400)
    except Exception as e:
        return await get_plain_response(f"Error: Could not access Batch {batch_id} from database: {e}", 400)

    # Make sure user has permission to access this batch_id
    try:
        if not atv_response.username == batch.username:
            error_message = f"Error: Permission denied to Batch {batch_id}.."
            return await get_plain_response(error_message, 403)
    except Exception as e:
        return await get_plain_response(f"Error: Something went wrong while parsing Batch {batch_id}: {e}", 400)

    # Get a status update for the batch (this will update the database if needed)
    batch_status, error_message, code = await update_batch_status(batch)
    if len(error_message) > 0:
        return await get_plain_response(error_message, code)

    # Return error if batch failed
    if batch_status == "failed":
        return await get_plain_response("Error: Batch failed.", 400)

    # Return error if results are not ready yet
    if not batch_status == "completed":
        return await get_plain_response("Error: Batch not completed yet. Results not ready.", 400)

    # Return result if already in the database
    if len (batch.result) > 0:
        return await get_plain_response(batch.result, 200)

    # Get the Globus batch status response
    status_response, error_message, code = globus_utils.get_batch_status(batch.globus_task_uuids)
    if len(error_message) > 0:
        return await get_plain_response(error_message, 500, code)
    
    # Collect results from each task
    try:
        result_list = []
        for _, status in status_response.items():
            if status["pending"] or not status["status"] == "success":
                return await get_plain_response("Error: Internal inconsistency in batch status report.", 400, code)
            result_list.append(status["result"])
        result = ",".join(result_list) + ","
        result = result[:-1]
    except Exception as e:
        return await get_plain_response(f"Error: Could not parse gcc.get_batch_result response : {e}", 400)
    
    # Update batch result in the database
    try:
        batch.result = result
        await update_database(db_object=batch)
    except Exception as e:
        return await get_plain_response(f"Error: Could not update batch {batch_id} result in database: {e}", 400)

    # Return status of the batch job
    #TODO: Implement response structure that is not just result (look at OpenAI)
    return await get_plain_response(result, 200)


# Inference (POST)
@router.post("/{cluster}/{framework}/v1/{path:openai_endpoint}")
async def post_inference(request, cluster: str, framework: str, openai_endpoint: str, *args, **kwargs):
    """POST request to reach Globus Compute endpoints."""

    # Check if request is authenticated
    atv_response = validate_access_token(request)
    if not atv_response.is_valid:
        return await get_plain_response(atv_response.error_message, atv_response.error_code)
    
    # Gather the list of Globus Group memberships of the authenticated user
    try:
        user_group_uuids = atv_response.user_group_uuids
    except Exception as e:
        return await get_plain_response(f"Error: Could access user's Globus Group memberships: {e}", 400)
    
    # Start the data dictionary for the database entry
    # The actual database entry creation is performed in the get_response() function
    db_data = {
        "name": atv_response.name,
        "username": atv_response.username,
        "timestamp_receive": timezone.now(),
        "sync": True # True means the server response is the Globus result, not the task UUID
    }

    # Strip the last forward slash is needed
    if openai_endpoint[-1] == "/":
        openai_endpoint = openai_endpoint[:-1]

    # Make sure the URL inputs point to an available endpoint 
    error_message = validate_url_inputs(cluster, framework, openai_endpoint)
    if len(error_message):
        return await get_response(db_data, error_message, 400)

    # Validate and build the inference request data
    data = validate_request_body(request, openai_endpoint)
    if "error" in data.keys():
        return await get_response(db_data, data['error'], 400)
    
    # Update the database with the input text from user
    db_data["prompt"] = json.dumps(extract_prompt(data["model_params"]))

    # Build the requested endpoint slug
    endpoint_slug = slugify(" ".join([cluster, framework, data["model_params"]["model"].lower()]))
    log.info("endpoint_slug", endpoint_slug)
    print("endpoint_slug", endpoint_slug, "-", atv_response.username)
    db_data["endpoint_slug"] = endpoint_slug
    
    # Pull the targetted endpoint UUID and function UUID from the database
    try:
        endpoint = await sync_to_async(Endpoint.objects.get)(endpoint_slug=endpoint_slug)
        data["model_params"]["api_port"] = endpoint.api_port
        db_data["openai_endpoint"] = data["model_params"]["openai_endpoint"]
    except Endpoint.DoesNotExist:
        return await get_response(db_data, f"Error: The requested endpoint {endpoint_slug} does not exist.", 400)
    except Exception as e:
        return await get_response(db_data, f"Error: Could not extract endpoint and function UUIDs: {e}", 400)
    
    # Extract the list of allowed group UUIDs tied to the targetted endpoint
    allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
    if len(error_message) > 0:
        return await get_response(db_data, error_message, 401)
    
    # Block access if the user is not a member of at least one of the required groups
    if len(allowed_globus_groups) > 0: # This is important to check if there is a restriction
        if len(set(user_group_uuids).intersection(allowed_globus_groups)) == 0:
            return await get_response(db_data, f"Permission denied to endpoint {endpoint_slug}.", 401)

    # Get Globus Compute client and executor
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests
    # NOTE: Do not include endpoint_id argument, otherwise it will cache multiple executors
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
        gce = globus_utils.get_compute_executor(client=gcc, amqp_port=443)
    except Exception as e:
        return await get_response(db_data, f"Error: Could not get the Globus Compute client or executor: {e}", 500)
    
    # Query the status of the targetted Globus Compute endpoint
    # NOTE: Do not await here, cache the "first" request to avoid too-many-requests Globus error
    endpoint_status, error_message = globus_utils.get_endpoint_status(
        endpoint_uuid=endpoint.endpoint_uuid, client=gcc, endpoint_slug=endpoint_slug
    )
    if len(error_message) > 0:
        return await get_response(db_data, error_message, 500)
        
    # Check if the endpoint is running and whether the compute resources are ready (worker_init completed)
    if not endpoint_status["status"] == "online":
        return await get_response(db_data, f"Error: Endpoint {endpoint_slug} is offline.", 503)
    resources_ready = int(endpoint_status["details"]["managers"]) > 0
    
    # Submit task and wait for result
    db_data["timestamp_submit"] = timezone.now()
    result, task_uuid, error_message, error_code = await globus_utils.submit_and_get_result(
        gce, endpoint.endpoint_uuid, endpoint.function_uuid, resources_ready, data=data
    )
    if len(error_message) > 0:
        return await get_response(db_data, error_message, error_code)
    db_data["task_uuid"] = task_uuid

    # Return Globus Compute results
    return await get_response(db_data, result, 200)


# Get plain response
async def get_plain_response(content, code):
    """Log error (if any) and return HTTP json.dumps response without writting to the database."""
    if code >= 300:
        log.error(content)
    return HttpResponse(json.dumps(content), status=code)


# Get response for list-endpoints URL
async def get_list_response(db_data, content, code):
    """Log database model (including error message if any) and return the HTTP response."""

    # Update the current database data
    db_data["response_status"] = code
    db_data["timestamp_response"] = timezone.now()
    if not code == 200:
        db_data["error_message"] = content

    # Create and save database entry
    try:
        db_log = ListEndpointsLog(**db_data)
        await sync_to_async(db_log.save, thread_sensitive=True)()
    except IntegrityError as e:
        message = f"Error: Could not create or save ListEndpointLog database entry: {e}"
        log.error(message)
        return HttpResponse(json.dumps(message), status=400)
    except Exception as e:
        message = f"Error: Something went wrong while trying to write to the ListEndpointLog database: {e}"
        log.error(message)
        return HttpResponse(json.dumps(message), status=400)
        
    # Return the response or the error message
    return HttpResponse(json.dumps(content), status=code)


# Log and get response
async def get_response(db_data, content, code):
    """Log result or error in the current database model and return the HTTP response."""
    
    # Update the current database data
    db_data["response_status"] = code
    db_data["result"] = content
    db_data["timestamp_response"] = timezone.now()

    # Create and save database entry
    try:
        db_log = Log(**db_data)
        await sync_to_async(db_log.save, thread_sensitive=True)()
    except IntegrityError as e:
        message = f"Error: Could not create or save Log database entry: {e}"
        log.error(message)
        return HttpResponse(json.dumps(message), status=400)
    except Exception as e:
        message = f"Error: Something went wrong while trying to write to the Log database: {e}"
        log.error(message)
        return HttpResponse(json.dumps(message), status=400)
        
    # Return the response or the error message
    if code == 200:
        return HttpResponse(content, status=code)
    else:
        log.error(content)
        return HttpResponse(json.dumps(content), status=code)

    
# Update database and get HTTP response
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


# Add URLs to the Ninja API
api.add_router("/", router)
