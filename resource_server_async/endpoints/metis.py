import json
from pydantic import BaseModel, Field
from typing import Any, Optional
from resource_server_async.endpoints.endpoint import (
    BaseModelWithError,
    SubmitTaskResponse,
    SubmitStreamingTaskResponse
)
from resource_server_async.endpoints.direct_api import DirectAPIEndpoint
from utils import metis_utils
import logging
log = logging.getLogger(__name__)


class GetEndpointStatusResponse(BaseModelWithError):
    status: Optional[Any] = None

class ModelStatus(BaseModel):
    model_info: Any
    endpoint_id: str

class MetisEndpointConfig(BaseModel):
    api_url: str
    api_key_env_name: str
    api_request_timeout: Optional[int] = Field(default=120)


# Metis endpoint implementation of a DirectAPIEndpoint
class MetisEndpoint(DirectAPIEndpoint):
    """Metis endpoint implementation of BaseEndpoint."""
    
    # Class initialization
    def __init__(self,
        id: str = None,
        endpoint_slug: str = None,
        cluster: str = None,
        framework: str = None,
        model: str = None,
        endpoint_adapter: str = None,
        allowed_globus_groups: str = None,
        allowed_domains: str = None,
        config: dict = None
    ):
        # Validate endpoint configuration
        self.__config = MetisEndpointConfig(**config)

        # Initialize the rest of the common attributes
        super().__init__(id, endpoint_slug, cluster, framework, model, endpoint_adapter, allowed_globus_groups, allowed_domains, config)


    # Get endpoint status
    async def get_endpoint_status(self) -> GetEndpointStatusResponse:
        """Return endpoint status or an error is the endpoint cannot receive requests."""
        
        # Check external API status to see if it can accept request
        metis_status, error_message = await metis_utils.fetch_metis_status(use_cache=True)
        if error_message:
            return GetEndpointStatusResponse(
                error_message=error_message,
                error_code=500
            )

        # Log requested model name
        log.info(f"Metis inference request for model: {self.model}")
        
        # Find matching model in Metis status (returns endpoint_id for API token lookup)
        model_info, endpoint_id, error_message = metis_utils.find_metis_model(metis_status, self.model)
        if error_message:
            return GetEndpointStatusResponse(error_code=404, error_message=error_message)
        
        # Check if the model is Live
        if model_info.get("status") != "Live":
            return GetEndpointStatusResponse(
                error_message=f"Error: '{self.model}' is not currently live on Metis. Status: {model_info.get('status')}",
                error_code=503
            )
        
        # Create model status object
        try:
            model_status = ModelStatus(model_info=model_info, endpoint_id=endpoint_id)
        except Exception as e:
            return GetEndpointStatusResponse(
                error_message=f"Error: Could not generate model status for endtpoint {self.endpoint_slug}: {e}",
                error_code=500
            )

        # Return endpoint status
        return GetEndpointStatusResponse(status=model_status)


    # Submit task
    async def submit_task(self, data) -> SubmitTaskResponse:
        """Submits a single interactive task to the compute resource."""

        # Check endpoint status
        response = await self.get_endpoint_status()
        if response.error_message:
            return SubmitTaskResponse(
                error_message=response.error_message,
                error_code=response.error_code
            )
        model_status: ModelStatus = response.status
        
        # Use validated request data as-is (already in OpenAI format)
        # Only update the stream parameter to match the request
        api_request_data = {**data["model_params"]}
        api_request_data["stream"] = False
        
        # Remove internal field that shouldn't be sent to Metis
        api_request_data.pop("openai_endpoint", None)
        api_request_data.pop("api_port", None)
        
        # Log model and Metis endpoint ID
        log.info(f"Making Metis API call for model {self.model} (stream=False, endpoint={model_status.endpoint_id})")
        
        # Send request to Metis
        response: SubmitTaskResponse = await self.call_api(api_request_data)
        
        # Return Metis API results or error
        return response
            

    # Submit streaming task
    async def submit_streaming_task(self, data: dict, request_log_id: str) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""
        
        # Check endpoint status
        response = await self.get_endpoint_status()
        if response.error_message:
            return SubmitStreamingTaskResponse(
                error_message=response.error_message,
                error_code=response.error_code
            )
        model_status = response.status

        # Log requested model name
        log.info(f"Metis inference request for model: {self.model}")
        
        # Use validated request data as-is (already in OpenAI format)
        # Only update the stream parameter to match the request
        api_request_data = {**data["model_params"]}
        api_request_data["stream"] = True

        # Remove internal field that shouldn't be sent to Metis
        api_request_data.pop("openai_endpoint", None)
        api_request_data.pop("api_port", None)
        
        # Log model and Metis endpoint ID
        log.info(f"Making Metis API call for model {self.model} (stream=True, endpoint={model_status.endpoint_id})")

        # Send streaming request to Metis
        response: SubmitStreamingTaskResponse = await self.call_stream_api(api_request_data, request_log_id)

        # Return response with StreamingHttpResponse object or errors
        return response


    # Read-only access to the configuration
    @property
    def config(self):
        return self.__config