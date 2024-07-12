from rest_framework.views import APIView
from rest_framework.response import Response
from utils.auth_utils import globus_authenticated
import json
import time
import globus_sdk
from django.utils.text import slugify
from resource_server.models import Endpoint, Log
from django.urls import resolve

# Data validation
from rest_framework.exceptions import ValidationError
from utils.serializers import OpenAILegacyParamSerializer, OpenAIParamSerializer

# Tool to log access requests
import logging
log = logging.getLogger(__name__)

# Utils functions
import resource_server.utils as utils
log.info("Utils functions loaded.")

# Constants
SERVER_RESPONSE = "server_response"

class ListEndpoints(APIView):
    """API view to list the available frameworks."""

    @globus_authenticated
    def get(self, request, *args, **kwargs):
        # Fetch all relevant data
        all_endpoints = []
        try:
            endpoints = Endpoint.objects.all()
            for endpoint in endpoints:
                all_endpoints.append({
                    "completion_endpoint_url": f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/completions/",
                    "chat_endpoint_url": f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/chat/completions/",
                    "model_name": endpoint.model
                })
            if not all_endpoints:
                return Response({"Error": "No endpoints found."}, status=400)
        except Exception as e:
            return Response({"Error": f"Exception while fetching endpoints.{e}"}, status=400)
        return Response(all_endpoints)

# Polaris view
class Polaris(APIView):
    """API view to reach Polaris Globus Compute endpoints."""

    # Define the targetted cluster
    cluster = "polaris"
    allowed_frameworks = ["llama-cpp", "vllm"]
    allowed_openai_endpoints = ["chat/completions", "completions"]
    
    # Post request call
    # TODO: We might want to pull this out of the Polaris view if 
    #       we want to reuse the post definition for other cluster.
    @globus_authenticated
    def post(self, request, framework, openai_endpoint, *args, **kwargs):
        """Public point of entry to call Globus Compute endpoints on Polaris."""

        # Make sure the requested framework is supported
        if not framework:
            return Response({"Error": "framework not provided."}, status=400)
        if not framework in self.allowed_frameworks:
            return Response({"Error": f"The requested {framework} is not supported."}, status=400)

        # Make sure the requested endpoint is supported
        if not openai_endpoint:
            return Response({"Error": f"openai endpoint of type {self.allowed_openai_endpoints} not provided."}, status=400)
        if not openai_endpoint in self.allowed_openai_endpoints:
            return Response({"Error": f"The requested {openai_endpoint} is not supported."}, status=400)
        
        # Validate and build the inference request data
        data = self.__validate_request_body(request, framework, openai_endpoint)
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
            return Response({SERVER_RESPONSE: "The requested endpoint does not exist."})
        except Exception as e:
            return Response({SERVER_RESPONSE: f"Error: {e}"})
        
        # Get Globus Compute client (using the endpoint identity)
        gcc = utils.get_compute_client_from_globus_app()

        # Check if the endpoint is running
        try:
            endpoint_status = gcc.get_endpoint_status(endpoint_uuid)
            if not endpoint_status["status"] == "online":
                return Response({SERVER_RESPONSE: f"Endpoint {endpoint_slug} is not online."})
        except globus_sdk.GlobusAPIError as e:
            return Response({SERVER_RESPONSE: f"Cannot access the status of endpoint {endpoint_slug}."})

        # Start a Globus Compute task
        try:
            task_uuid = gcc.run(
                data,
                endpoint_id=endpoint_uuid,
                function_id=function_uuid,
            )
        except Exception as e:
            return Response({SERVER_RESPONSE: f"Error: {e}"})

        # Log request in the Django database
        try:
            db_log = Log(
                name=kwargs["user"]["name"],
                username=kwargs["user"]["username"],
                cluster=self.cluster.lower(),
                framework=framework.lower(),
                model=data["model_params"]["model"],
                openai_endpoint=data["model_params"]["openai_endpoint"],
                prompt=data["model_params"]["prompt"] if "prompt" in data["model_params"] else data["model_params"]["messages"],
                task_uuid=task_uuid,
                completed=False,
                sync=True
            )
            db_log.save()
        except Exception as e:
            return Response({SERVER_RESPONSE: f"Error: {e}"})

        # Wait until results are done
        # TODO: We need to be careful here if we are thinking of using Executor and future().
        #       With Executor you can deactivate a client if a parallel request creates an 
        #       other executor with the same Globus App credentials.
        task = gcc.get_task(task_uuid)
        pending = task["pending"]
        # NOTE: DO NOT set pending = True since it will slow down the automated test suite
        while pending:
            task = gcc.get_task(task_uuid)
            pending = task["pending"]
            time.sleep(1)

        # TODO: Check status to see if it succeeded
        result = gcc.get_result(task_uuid)

        # Update the database log
        db_log.completed = True
        db_log.save()

        # Return Globus Compute results
        return Response({SERVER_RESPONSE: result})


    # Validate request body
    def __validate_request_body(self, request, framework, openai_endpoint):
        """Build data dictionary for inference request if user inputs are valid."""

        # Select the appropriate data validation serializer based on the openai endpoint
        if "chat/completions" in openai_endpoint:
            serializer_class = OpenAIParamSerializer
        elif "completion" in openai_endpoint:
            serializer_class = OpenAILegacyParamSerializer
        else:
            return {"error": f"The requested {openai_endpoint} is not supported."}
        
        # Decode request body into a dictionary
        try:
            model_params = json.loads(request.body.decode("utf-8"))
        except:
            return {"error": f"Request body cannot be decoded"}
        
        # Send an error if the input data is not valid
        try:
            serializer = serializer_class(data=model_params)
            is_valid = serializer.is_valid(raise_exception=True)
        except ValidationError as e:
            return {"error": f"Data validation error: {e}"}

        # Add the 'url' parameter to model_params
        model_params['openai_endpoint'] = openai_endpoint

        # Build request data if nothing wrong was caught
        return {"model_params": model_params}
