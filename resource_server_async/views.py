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
    extract_prompt, 
    validate_request_body,
    extract_group_uuids,
    get_qstat_details,
    ALLOWED_QSTAT_ENDPOINTS,
    ALLOWED_BATCH_ENDPOINTS
)
log.info("Utils functions loaded.")

# Django database
from resource_server.models import Endpoint, Log, ListEndpointsLog, Batch

# Async tools
from asgiref.sync import sync_to_async

# Ninja API
from ninja import NinjaAPI, Router
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
        return await get_plain_response(f"Error: Could access user's Globus Group memberships. {e}", 400)

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
        return await get_list_response(db_data, f"Error: Could access user's Globus Group memberships. {e}", 400)

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
        return await get_list_response(db_data, f"Error: Could access user's Globus Group memberships. {e}", 400)

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


# Inference batch (POST)
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
        return await get_plain_response(f"Error: Could access user's Globus Group memberships. {e}", 400)
    
    # Start the data dictionary for the database entry
    # The actual database entry creation is performed in the get_response() function
    db_data = {
        "id": uuid.uuid4(),
        "name": atv_response.name,
        "username": atv_response.username,
        "created_at": timezone.now(),
        "object": "batch"
    }
    
    # Validate and build the inference request data
    batch_data = validate_request_body(request)
    if "error" in batch_data.keys():
        return await get_batch_response(db_data, batch_data['error'], 400)
    
    # Strip the last forward slash of endpoint if needed
    if batch_data["endpoint"][-1] == "/":
        batch_data["endpoint"] = batch_data["endpoint"][:-1]

    # Make sure the URL inputs point to an available endpoint 
    error_message = validate_url_inputs(cluster, framework, batch_data["endpoint"])
    if len(error_message):
        return await get_batch_response(db_data, error_message, 400)
    
    # Update database entry
    db_data["cluster"] = cluster
    db_data["framework"] = framework
    db_data["model"] = batch_data["model"]
    db_data["endpoint"] = batch_data["endpoint"]
    db_data["input_file_id"] = batch_data["input_file_id"]
    db_data["completion_window"] = batch_data["completion_window"]
    db_data["metadata"] = batch_data["metadata"]
    db_data["task_uuids"] = ""

    # Error if a batch already exists with the same input_file_id
    # TODO: More checks here to make sure we don't duplicate batches?
    try:
        if await sync_to_async(Batch.objects.filter)(input_file_id=batch_data["input_file_id"]).exists():
            error_message = f"Error: Input file ID {batch_data['input_file_id']} already used by another batch."
            return await get_batch_response(db_data, error_message, 400)
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Could not filter Batch database entries: {e}", 400)

    # Build the requested endpoint slug
    endpoint_slug = slugify(" ".join([cluster, framework, batch_data["model"].lower()]))
    
    # Pull the targetted endpoint UUID and function UUID from the database
    try:
        endpoint = await sync_to_async(Endpoint.objects.get)(endpoint_slug=endpoint_slug)
    except Endpoint.DoesNotExist:
        return await get_batch_response(db_data, f"Error: The requested endpoint {endpoint_slug} does not exist.", 400)
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Could not extract endpoint and function UUIDs: {e}", 400)
    
    # Extract the list of allowed group UUIDs tied to the targetted endpoint
    allowed_globus_groups, error_message = extract_group_uuids(endpoint.allowed_globus_groups)
    if len(error_message) > 0:
        return await get_batch_response(db_data, error_message, 401)
    
    # Block access if the user is not a member of at least one of the required groups
    if len(allowed_globus_groups) > 0: # This is important to check if there is a restriction
        if len(set(user_group_uuids).intersection(allowed_globus_groups)) == 0:
            return await get_batch_response(db_data, f"Permission denied to endpoint {endpoint_slug}.", 401)

    # Get Globus Compute client (using the endpoint identity)
    try:
        gcc = globus_utils.get_compute_client_from_globus_app()
    except Exception as e:
        return await get_batch_response(db_data, f"Error: Could not get the Globus Compute client: {e}", 500)

    # Make sure the cluster has a batch endpoint
    if not cluster in ALLOWED_BATCH_ENDPOINTS:
        return await get_batch_response(db_data, f"Cluster {cluster} does not currently have a batch endpoint.", 501)

    # Query the status of the Globus Compute batch endpoint
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests,
    # otherwise, coroutines can set off the too-many-requests Globus error before the "first" requests can cache the status
    endpoint_slug = f"{cluster}/batch"
    endpoint_uuid = ALLOWED_BATCH_ENDPOINTS[cluster]["endpoint_uuid"]
    function_uuid = ALLOWED_BATCH_ENDPOINTS[cluster]["function_uuid"]
    endpoint_status, error_message = globus_utils.get_endpoint_status(
        endpoint_uuid=endpoint.endpoint_uuid, 
        client=gcc, 
        endpoint_slug=endpoint_slug
    )
    if len(error_message) > 0:
        return await get_batch_response(db_data, error_message, 500)
        
    # Check if the endpoint is running and whether the compute resources are ready (worker_init completed)
    if not endpoint_status["status"] == "online":
        return await get_batch_response(db_data, f"Error: Endpoint {endpoint_slug} is offline.", 503)

    # TODO: extract inputs from input_file_id database entry
    # TODO: create database model for InputBatchFile

    # Temp: This should be replace with proper inputs from database and from request
    params_list = [
        {
            "input_file_path": "/path/to/file",
            "framework": framework,
            "model": batch_data["model"]
        }
    ]

    # Prepare the batch job
    # TODO: Maybe send the db_data["id"] to the compute function to organize and keep track of output folders/files?
    #       This is likely needed to make sure we don't loose results if Globus get rid of them after 3 days
    #       We will have a Django cron job to gather results, but we need to have multiple safety nets
    batch = gcc.create_batch()
    for params in params_list:
        batch.add(function_id=function_uuid, args=[params])

    # Submit batch to Globus Compute
    try:
        batch_response = gcc.batch_run(endpoint_id=endpoint_uuid, batch=batch)
    except Exception as e:
        return await get_response(db_data, f"Error: Could not submit the Globus Compute batch: {e}", 500)
    
    # Extract the batch and task UUIDs from submission
    try:
        db_data["globus_batch_uuid"] = batch_response["request_id"]
        for _, task_uuids in batch_response["tasks"].items():
                db_data["globus_task_uuids"] += ",".join(task_uuids) + ","
        db_data["globus_task_uuids"] = db_data["globus_task_uuids"][:-1]
    except Exception as e:
        return await get_response(db_data, f"Error: Batch submitted but could not extract Globus UUIDs: {e}", 400)

    # TODO: Return something more OpenAI style
    response = {
        "request_id": db_data["id"],
        "globus_batch_uuid": db_data["globus_batch_uuid"],
        "globus_task_uuids": db_data["globus_task_uuids"]
    }
    return await get_response(db_data, response, 200)


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
        return await get_plain_response(f"Error: Could access user's Globus Group memberships. {e}", 400)
    
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


# Log and get batch response
async def get_batch_response(db_data, content, code):
    """Log result or error in the current Batch database model and return the HTTP response."""
    
    # Create and save database entry
    try:
        db_log = Batch(**db_data)
        await sync_to_async(db_log.save, thread_sensitive=True)()
    except IntegrityError as e:
        message = f"Error: Could not create or save database entry: {e}"
        log.error(message)
        return HttpResponse(json.dumps(message), status=400)
    except Exception as e:
        message = f"Error: Something went wrong while trying to write to the database: {e}"
        log.error(message)
        return HttpResponse(json.dumps(message), status=400)
        
    # Return the response or the error message
    if code == 200:
        return HttpResponse(content, status=code)
    else:
        log.error(content)
        return HttpResponse(json.dumps(content), status=code)


# Add URLs to the Ninja API
api.add_router("/", router)
