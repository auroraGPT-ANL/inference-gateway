from rest_framework.views import APIView
from rest_framework.response import Response
from utils.auth_utils import globus_authenticated
import json
import time
import globus_sdk
from django.utils.text import slugify
from django.utils import timezone
from django.db import IntegrityError, transaction
from resource_server.models import Endpoint, Log

# Data validation
from rest_framework.exceptions import ValidationError
from utils.serializers import OpenAICompletionsParamSerializer, OpenAIChatCompletionsParamSerializer, OpenAIEmbeddingsParamSerializer

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
            for endpoint in Endpoint.objects.all():
                all_endpoints.append({
                    "completion_endpoint_url": f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/completions/",
                    "chat_endpoint_url": f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/chat/completions/",
                    "embedding_endpoint_url": f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/embeddings/",
                    "model_name": endpoint.model
                })
            if not all_endpoints:
                return Response({"Error": "No endpoints found."}, status=400)
        except Exception as e:
            return Response({"Error": f"Exception while fetching endpoints.{e}"}, status=400)
        return Response(all_endpoints)


# Reusable base view class for different clusters
class ClusterBase(APIView):
    """Common functions for API view that reaches Globus Compute endpoints."""

    # Generic post request call
    def _post_base(self, request, framework, openai_endpoint, *args, **kwargs):
        """Point of entry to call Globus Compute endpoints on a specific cluster."""

        # Create data for the database entry
        # The database entry creation is done in the self.__get_response() function
        db_data = {
            "name": kwargs["user"]["name"],
            "username": kwargs["user"]["username"],
            "timestamp_receive": kwargs["timestamp_receive"],
            "sync": True
        }

        # Make sure the requested framework is supported
        if not framework:
            return self.__get_response(db_data, "Error: Framework not provided.", 400)
        if not framework in self.allowed_frameworks:
            return self.__get_response(db_data, f"Error: {framework} framework not supported.", 400)

        # Make sure the requested endpoint is supported
        if not openai_endpoint:
            return self.__get_response(db_data, f"Error: Openai endpoint of type {self.allowed_openai_endpoints} not provided.", 400)
        if not openai_endpoint in self.allowed_openai_endpoints:
            return self.__get_response(db_data, f"Error: {openai_endpoint} endpoint not supported. Currently supporting {self.allowed_openai_endpoints}.", 400)
        
        # Validate and build the inference request data
        data = self.__validate_request_body(request, framework, openai_endpoint)
        if "error" in data.keys():
            return self.__get_response(db_data, f"Error: {data['error']}", 400)
        log.info("data", data)

        # Update the database with the input text from user
        if "prompt" in data["model_params"]:
            prompt = data["model_params"]["prompt"]
        elif "messages" in data["model_params"]:
            prompt = data["model_params"]["messages"]
        elif "input" in data["model_params"]:
            prompt = data["model_params"]["input"]
        else:
            prompt = "default"
        db_data["prompt"] = json.dumps(prompt)

        # Build the requested endpoint slug
        endpoint_slug = slugify(" ".join([
            self.cluster,
            framework, 
            data["model_params"]["model"].lower()
        ]))
        log.info("endpoint_slug", endpoint_slug)
        print("endpoint_slug", endpoint_slug)
        db_data["endpoint_slug"] = endpoint_slug

        # Pull the targetted endpoint UUID and function UUID from the database
        try:
            endpoint = Endpoint.objects.get(endpoint_slug=endpoint_slug)
            endpoint_uuid = endpoint.endpoint_uuid
            function_uuid = endpoint.function_uuid
            data["model_params"]["api_port"] = endpoint.api_port
            db_data["openai_endpoint"] = data["model_params"]["openai_endpoint"]
        except Endpoint.DoesNotExist:
            return self.__get_response(db_data, "Error: The requested endpoint does not exist.", 400)
        except Exception as e:
            return self.__get_response(db_data, f"Error: {e}", 400)
        
        # Get Globus Compute client (using the endpoint identity)
        gcc = utils.get_compute_client_from_globus_app()

        # Check if the endpoint is running
        try:
            endpoint_status = gcc.get_endpoint_status(endpoint_uuid)
            if not endpoint_status["status"] == "online":
                return self.__get_response(db_data, f"Error: Endpoint {endpoint_slug} is not online.", 400)
        except globus_sdk.GlobusAPIError as e:
            log.error(e)
            return self.__get_response(db_data, f"Error: Cannot access the status of endpoint {endpoint_slug}.", 400)

        # Start a Globus Compute task
        try:
            db_data["timestamp_submit"] = timezone.now()
            task_uuid = gcc.run(
                data,
                endpoint_id=endpoint_uuid,
                function_id=function_uuid,
            )
            db_data["task_uuid"] = task_uuid
        except Exception as e:
            return self.__get_response(db_data, f"Error: {e}.", 400)

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
            time.sleep(0.5)

        # Get result from the Globus Compute task
        try:
            result = gcc.get_result(task_uuid)
        except Exception as e:
            return self.__get_response(db_data, f"Error: {e}.", 400)

        # Return Globus Compute results
        return self.__get_response(db_data, result, 200)
    

    # Validate request body
    def __validate_request_body(self, request, framework, openai_endpoint):
        """Build data dictionary for inference request if user inputs are valid."""

        # Make sure the requested framework is supported
        if not framework or not framework in self.allowed_frameworks:
            return {"error": f"The requested {framework} framework is not supported."}
        
        # Select the appropriate data validation serializer based on the openai endpoint
        if "chat/completions" in openai_endpoint:
            serializer_class = OpenAIChatCompletionsParamSerializer
        elif "completion" in openai_endpoint:
            serializer_class = OpenAICompletionsParamSerializer
        elif "embeddings" in openai_endpoint:
            serializer_class = OpenAIEmbeddingsParamSerializer
        else:
            return {"error": f"The requested {openai_endpoint} openai endpoint is not supported. Currently supporting {self.allowed_openai_endpoints}."}
        
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


    # Log and get response
    def __get_response(self, db_data, content, code):
        """Log result or error in the current database model and return the HTTP response."""
        
        # Update the current database data
        db_data["response_status"] = code
        db_data["result"] = content
        db_data["timestamp_response"] = timezone.now()

        # Create and save database entry
        try:
            with transaction.atomic():
                db_log = Log(**db_data)
                db_log.save()
        except IntegrityError as e:
            return Response({SERVER_RESPONSE: f"Error: Something went wrong when saving the database entry. {e}"}, status=400)

        # Return the error response
        return Response({SERVER_RESPONSE: content}, status=code)


# Polaris view
class Polaris(ClusterBase):
    """API view to reach Polaris Globus Compute endpoints."""

    # Define the targetted cluster
    cluster = "polaris"
    allowed_frameworks = ["llama-cpp", "vllm"]
    allowed_openai_endpoints = ["chat/completions", "completions", "embeddings"]
    
    # Post request call
    @globus_authenticated
    def post(self, request, framework, openai_endpoint, *args, **kwargs):
        """Point of entry to call Globus Compute endpoints on Polaris."""
        return self._post_base(request, framework, openai_endpoint, *args, **kwargs)


# Sophia view
class Sophia(ClusterBase):
    """API view to reach Sophia Globus Compute endpoints."""

    # Define the targetted cluster
    cluster = "sophia"
    allowed_frameworks = ["vllm"]
    allowed_openai_endpoints = ["chat/completions", "completions", "embeddings"]
    
    # Post request call
    @globus_authenticated
    def post(self, request, framework, openai_endpoint, *args, **kwargs):
        """Point of entry to call Globus Compute endpoints on Sophia."""
        return self._post_base(request, framework, openai_endpoint, *args, **kwargs)