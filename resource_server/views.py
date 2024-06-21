from rest_framework.views import APIView
from rest_framework.response import Response
from utils.auth_utils import globus_authenticated

from django.conf import settings
import time
import json
import globus_sdk
from django.utils.text import slugify
from resource_server.models import Endpoint, Log

# Tool to log access requests
import logging
log = logging.getLogger(__name__)

# Utils functions
from resource_server.utils import get_compute_client_from_globus_app
log.info("Utils functions loaded.")


class ListEndpoints(APIView):
    """API view to list the available frameworks."""

    @globus_authenticated
    def get(self,request, *args, **kwargs):
        # Fetch all relevant data
        endpoints = Endpoint.objects.all()
        # Prepare the list of endpoint URLs and model names
        result = []
        for endpoint in endpoints:
            url = f"/resource_server/{endpoint.cluster}/{endpoint.framework}/completions/"
            result.append({
                "endpoint_url": url,
                "model_name": endpoint.model
            })

        if not result:
            return Response({"Error": "No endpoints found."}, status=404)

        return Response(result)

# Polaris view
class Polaris(APIView):
    """API view to reach Polaris Globus Compute endpoints."""

    # Define the targetted cluster
    cluster = "polaris"
    allowed_frameworks = ["llama-cpp", "vllm"]
    
    # Post request call
    # TODO: We might want to pull this out of the Polaris view if 
    #       we want to reuse the post definition for other cluster.
    @globus_authenticated
    def post(self, request, framework, *args, **kwargs):
        """Public point of entry to call Globus Compute endpoints on Polaris."""
        
        # Make sure the requested framework is supported
        if not framework:
            return Response({"Error": "framework not provided."}, status=400)
        if not framework in self.allowed_frameworks:
            return Response({"Error": f"The requested {framework} is not supported."}, status=400)
        
        # Validate and build the inference request data
        data = self.__validate_request_body(request, framework)
        if "error" in data.keys():
            return Response({"Error": data["error"]}, status=400)
        log.info("data", data)

        # Build the requested endpoint slug
        endpoint_slug = slugify(" ".join([
            self.cluster,
            framework, 
            data["model_params"]["model"].lower()
        ]))
        log.info("endpoint_slug", endpoint_slug)
        print("endpoint_slug", endpoint_slug)
        # Pull the targetted endpoint UUID and function UUID from the database
        try:
            endpoint = Endpoint.objects.get(endpoint_slug=endpoint_slug)
            endpoint_uuid = endpoint.endpoint_uuid
            function_uuid = endpoint.function_uuid
        except Endpoint.DoesNotExist:
            return Response({"server_response": "The requested endpoint does not exist."})
        except Exception as e:
            return Response({"server_response": f"Error: {e}"})
        
        # Get Globus Compute client (using the endpoint identity)
        gcc = get_compute_client_from_globus_app()

        # Check if the endpoint is running
        try:
            endpoint_status = gcc.get_endpoint_status(endpoint_uuid)
            if not endpoint_status["status"] == "online":
                return Response({"server_response": f"Endpoint {endpoint_slug} is not online."})
        except globus_sdk.GlobusAPIError as e:
            return Response({"server_response": f"Cannot access the status of endpoint {endpoint_slug}."})

        # Start a Globus Compute task
        try:
            task_uuid = gcc.run(
                data,
                endpoint_id=endpoint_uuid,
                function_id=function_uuid,
            )
        except Exception as e:
            return Response({"server_response": f"Error: {e}"})

        # Log request in the Django database
        try:
            db_log = Log(
                name=kwargs["user"]["name"],
                username=kwargs["user"]["username"],
                cluster=self.cluster.lower(),
                framework=framework.lower(),
                model=data["model_params"]["model"],
                prompt=data["model_params"]["prompt"] if "prompt" in data["model_params"] else data["model_params"]["messages"],
                task_uuid=task_uuid,
                completed=False,
                sync=True
            )
            db_log.save()
        except Exception as e:
            return Response({"server_response": f"Error: {e}"})

        # Wait until results are done
        # TODO: We need to be careful here if we are thinking of using Executor and future().
        #       With Executor you can deactivate a client if a parallel request creates an 
        #       other executor with the same Globus App credentials.
        pending = True
        while pending:
            task = gcc.get_task(task_uuid)
            pending = task["pending"]
            time.sleep(2)

        # TODO: Check status to see if it succeeded
        result = gcc.get_result(task_uuid)

        # Update the database log
        db_log.completed = True
        db_log.save()

        #return Response({"server_response": f"{name} ({username}) should have access. {response_json}"})
        return Response({"server_response": result})


    # Validate request body
    def __validate_request_body(self, request, framework):
        """Build data dictionary for inference request if user inputs are valid."""

        # Define the expected keys and their types
        mandatory_keys = {
            "model": str
        }
        if framework == "vllm":
            mandatory_keys["messages"] = list # New parameter for maintaining dialogue context]
            
        elif framework == "llama-cpp":
            mandatory_keys["prompt"] = str # New parameter for user input prompt
        else:
            return {"error": f"Framework input validation not supported: {framework}"}

        # Define optional keys that can be sent with requests
        optional_keys = {
            "temperature": (float, int),
            "dynatemp_range": (float, int),
            "dynatemp_exponent": (float, int),
            "top_k": int,
            "top_p": (float, int),
            "min_p": (float, int),
            "n_predict": int,
            "n_keep": int,
            "stream": bool,
            "stop": list,
            "tfs_z": (float, int),
            "typical_p": (float, int),
            "repeat_penalty": (float, int),
            "repeat_last_n": int,
            "penalize_nl": bool,
            "presence_penalty": (float, int),
            "frequency_penalty": (float, int),
            "penalty_prompt": (str, list, type(None)),
            "mirostat": int,
            "mirostat_tau": (float, int),
            "mirostat_eta": (float, int),
            "grammar": str,
            "json_schema": dict,
            "seed": int,
            "ignore_eos": bool,
            "logit_bias": list,
            "n_probs": int,
            "min_keep": int,
            "image_data": list,
            "id_slot": int,
            "cache_prompt": bool,
            "system_prompt": str,
            "samplers": list,
            "max_tokens": int,  # New parameter for specifying maximum tokens to generate
            "best_of": int,  # New parameter for selecting the best response out of several generated
            "session_id": str,  # New parameter for session tracking
            "include_debug": bool,  # New parameter to include debug information in response
            "audio_config": dict,  # New parameter for specifying audio output configuration
            "logprobs": bool,
            "top_logprobs": (int, type(None)),  # Integer or null
            "n": (int, type(None)),  # Integer or null
            "response_format": dict,  # Object specifying format
            "service_tier": (str, type(None)),  # String or null
            "stream_options": (dict, type(None)),  # Object or null
            "tools": list,  # Array of tools
            "tool_choice": (str, dict),  # String or object
            "parallel_tool_calls": bool,  # Boolean
            "user": str  # String
        } # TODO: Add more parameters
        
        # Decode request body into a dictionary
        try:
            model_params = json.loads(request.body.decode("utf-8"))
        except:
            return {"error": f"Request body cannot be decoded"}

        # Check mandatory keys
        for key, expected_type in mandatory_keys.items():
            if key not in model_params:
                return {"error": f"Mandatory parameter missing: {key}"}
            if not isinstance(model_params.get(key), expected_type):
                return {"error": f"Mandatory parameter invalid: {key} --> should be {expected_type}"}
        
        # Check optional keys
        for key, expected_type in optional_keys.items():
            if key in model_params and not isinstance(model_params.get(key), expected_type):
                return {"error": f"Optional parameter invalid: {key} --> should be {expected_type}"}
            
        # Check un-recognized keys
        for key in model_params:
            if key not in mandatory_keys and key not in optional_keys:
                return {"error": f"Input parameter not supported: {key}"}

        # Build request data if nothing wrong was caught
        return {"model_params": model_params}
