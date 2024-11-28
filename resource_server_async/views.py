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
import resource_server.utils as utils
from resource_server_async.utils import (
    validate_url_inputs, 
    extract_prompt, 
    validate_request_body,
    extract_group_uuids
)
log.info("Utils functions loaded.")

# Django database
from resource_server.models import Endpoint, Log

# Async tools
from asgiref.sync import sync_to_async
import asyncio

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

    # Get Globus Compute client (using the endpoint identity)
    try:
        # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests
        gcc = utils.get_compute_client_from_globus_app()
        # NOTE: Make sure there will only be one executor for the whole application
        #       Do not include endpoint_id argument otherwise it will cache multiple executors
        #       Do not await anything before after future.result in order preserve the endpoint_id
        gce = utils.get_compute_executor(client=gcc, amqp_port=443)
    except Exception as e:
        return await get_response(db_data, f"Error: Could not get the Globus Compute client: {e}", 500)
    
    # Query the status of the targetted Globus Compute endpoint
    # If the endpoint status query failed, it retuns a string with the error message
    # NOTE: Do not await here, let the "first" request cache the client/executor before processing more requests,
    # otherwise, coroutines can set off the too-many-requests Globus error before the "first" requests can cache the status
    endpoint_status, error_message = utils.get_endpoint_status(
        endpoint_uuid=endpoint.endpoint_uuid, 
        client=gcc, 
        endpoint_slug=endpoint_slug
    )
    if len(error_message) > 0:
        return await get_response(db_data, error_message, 500)
        
    # Check if the endpoint is running and whether the compute resources are ready (worker_init completed)
    if not endpoint_status["status"] == "online":
        return await get_response(db_data, f"Error: Endpoint {endpoint_slug} is offline.", 503)
    resources_ready = int(endpoint_status["details"]["managers"]) > 0
    
    # Start a Globus Compute task
    try:
        db_data["timestamp_submit"] = timezone.now()
        # NOTE: No need to do await here, the submit* function return the future "immediately"
        gce.endpoint_id = endpoint.endpoint_uuid
        future = gce.submit_to_registered_function(endpoint.function_uuid, args=[data])
    except Exception as e:
        return await get_response(db_data, f"Error: Could not start the Globus Compute task: {e}", 500)
    
    # Convert concurrent future received by Globus into an asyncio future
    # Wait for the Globus Compute result using asyncio and coroutine
    try:
        asyncio_future = asyncio.wrap_future(future)
        result = await asyncio.wait_for(asyncio_future, timeout=60*28)
        db_data["task_uuid"] = future.task_id
    except TimeoutError as e:
        if resources_ready:
            error_message = "Error: TimeoutError with compute resources not responding. Please try again or contact adminstrators."
        else:
            error_message = "Error: TimeoutError while attempting to acquire compute resources. Please try again in 10 minutes."
        return await get_response(db_data, error_message, 408)
    except Exception as e:
        return await get_response(db_data, f"Error: Could not recover future result: {repr(e)}", 500)

    # Return Globus Compute results
    return await get_response(db_data, result, 200)

# Get plain response
async def get_plain_response(content, code):
    """Log error (if any) and return HTTP json.dumps response without writting to the database."""
    if code >= 300:
        log.error(content)
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
        message = f"Error: Could not create or save database entry: {e}"
        log.error(message)
        return HttpResponse(message, status=400)
        
    # Return the response or the error message
    if code == 200:
        return HttpResponse(content, status=code)
    else:
        log.error(content)
        return HttpResponse(json.dumps(content), status=code)


# Add URLs to the Ninja API
api.add_router("/", router)
