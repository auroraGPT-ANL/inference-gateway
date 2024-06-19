from rest_framework.views import APIView
from rest_framework.response import Response
from django.conf import settings
import functools
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

# Check Globus Policies
def check_globus_policies(client, bearer_token):
    """
        Define whether an authenticated user respect the Globus policies.
        User should meet all Globus policies requirements.
    """

    # Build the body that will be sent to the introspect Globus API
    introspect_body = {
        "token": bearer_token,
        "authentication_policies": settings.GLOBUS_POLICIES
    }

    # Post call to the introspect API
    try: 
        response = client.post("/v2/oauth2/token/introspect", data=introspect_body, encoding="form")
    except:
        return False, "Something went wrong in the Globus introspect API call."

    # Return False if policies cannot be evaluated went wrong
    if not len(response["policy_evaluations"]) == settings.NUMBER_OF_GLOBUS_POLICIES:
        return False, "Some Globus policies could not be passed to the introspect API call."

    # Return False if the user failed to meet one of the policies 
    for policies in response["policy_evaluations"].values():
        if policies.get("evaluation",False) == False:
            return False, "One of the Globus policies blocked the access to the service."

    # Return True if the user met all of the policies requirements
    return True, ""


# User In Allowed Groups
def user_in_allowed_groups(user_email):
    """
        Define whether an authenticated user has the proper Globus memberships.
        User should be member of at least in one of the allowed Globus groups.
    """
    return False # In dev


# Globus Authenticated
def globus_authenticated(f):
    """
        Decorator that will validate request headers to make sure the user
        is authenticated and allowed to access the vLLM service API.
    """

    @functools.wraps(f)
    def check_bearer_token(self, request, *args, **kwargs):
        try:
            # Create vLLM service client
            client =  globus_sdk.ConfidentialAppAuthClient(
                settings.GLOBUS_APPLICATION_ID, 
                settings.GLOBUS_APPLICATION_SECRET
            )

            # Make sure the request is authenticated
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                return Response(
                    {"Error": "Missing ('Authorization': 'Bearer <your-access-token>') in request headers."},status=400
                )
            
            # Make sure the bearer flag is mentioned
            try:
                ttype, bearer_token = auth_header.split()
                if ttype != "Bearer":
                    return Response({"Error": "Only Authorization: Bearer <token> is allowed."}, status=400)
            except (AttributeError, ValueError):
                return Response({"Error": "Auth only allows header type Authorization: Bearer <token>"}, status=400)
            
            # Introspect the access token
            introspection = client.oauth2_token_introspect(bearer_token)

            # Make sure the access token is active and filled with user information
            if introspection["active"] is False:
                return Response({"Error": "Token is either not active or invalid"}, status=401)

            # Prepare user details to be passed to the Django view
            kwargs["user"] = {
                "name": introspection["name"],
                "username": introspection["username"]
            }

            # Log access request
            log.info(f"{introspection['name']} requesting {introspection['scope']}")

            # Make sure the token is not expired
            expires_in = introspection["exp"] - time.time()
            if expires_in <= 0:
                return Response({"Error": "User not Authorized. Access token expired"}, status=401)
            
            # Make sure the authenticated user comes from an allowed domain
            if settings.NUMBER_OF_GLOBUS_POLICIES > 0:
                successful, error_message = check_globus_policies(client, bearer_token)
                if not successful:
                    return Response({"Error": error_message}, status=401)

            return f(self, request, *args, **kwargs) 
        except Exception as e:
            return Response({"Error Here": str(e)}, status=500)

    return check_bearer_token

class ListEndpoints(APIView):
    """API view to list the available frameworks."""

    @globus_authenticated
    def get(self,request):
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
        data = self.__validate_request_body(request)
        if len(data) == 0:
            return Response({"Error": "Request data invalid."}, status=400)
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
                prompt=data["model_params"]["prompt"],
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
    def __validate_request_body(self, request):
        """Build data dictionary for inference request if user inputs are valid."""

        # Define the expected keys and their types
        mandatory_keys = {
            "model": str
        }

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
            "prompt": str,  # New parameter for user input prompt
            "messages": list,  # New parameter for maintaining dialogue context
            "max_tokens": int,  # New parameter for specifying maximum tokens to generate
            "best_of": int,  # New parameter for selecting the best response out of several generated
            "session_id": str,  # New parameter for session tracking
            "include_debug": bool,  # New parameter to include debug information in response
            "audio_config": dict  # New parameter for specifying audio output configuration
        } # TODO: Add more parameters
        
        # Decode request body into a dictionary
        model_params = json.loads(request.body.decode("utf-8"))

        # Check mandatory keys
        for key, expected_type in mandatory_keys.items():
            if not isinstance(model_params.get(key), expected_type):
                return "Mandatory parameter missing or invalid: " + key
        
        # Check optional keys
        for key, expected_type in optional_keys.items():
            if key in model_params and not isinstance(model_params.get(key), expected_type):
                return "Optional parameter invalid for key: " + key

        # Build request data if nothing wrong was caught
        return {"model_params": model_params}
