from utils import globus_utils
from django.http import StreamingHttpResponse
from django.core.cache import cache
from pydantic import BaseModel, Field
from typing import List, Optional
from utils.pydantic_models.db_models import AccessLogPydantic, RequestLogPydantic
from resource_server_async.utils import (
    remove_endpoint_from_cache,
    is_cached
)
from resource_server_async.endpoints.endpoint import (
    BaseEndpoint,
    SubmitTaskResponse,
    SubmitStreamingTaskResponse,
    SubmitBatchResponse,
    GetBatchResultResponse,
    GetBatchStatusResponse
)
from utils import metis_utils

# Tool to log access requests
import logging
log = logging.getLogger(__name__)


# Configuration data structure
class DirectAPIConfig(BaseModel):
    something: str


# Direct API implementation of a BaseEndpoint
class DirectAPI(BaseEndpoint):
    """Direct API implementation of BaseEndpoint."""
    
    # Class initialization
    def __init__(self,
        id: str = None,
        endpoint_slug: str = None,
        cluster: str = None,
        framework: str = None,
        model: str = None,
        endpoint_type: str = None,
        allowed_globus_groups: str = None,
        allowed_domains: str = None,
        config: dict = None
    ):
        # Validate endpoint configuration
        self._config = DirectAPIConfig(**config)

        # Initialize the rest of the common attributes
        super().__init__(id, endpoint_slug, cluster, framework, model, endpoint_type, allowed_globus_groups, allowed_domains)


    # Submit task
    async def submit_task(self, data) -> SubmitTaskResponse:
        """Submits a single interactive task to the compute resource."""

        # Check external API status to see if it can accept request
        metis_status, error_message = await metis_utils.fetch_metis_status(use_cache=True)
        if error_message:
            return SubmitTaskResponse(
                error_code=500,
                error_message=error_message
            )

        # Log requested model name
        log.info(f"Metis inference request for model: {self.model}")
        
        # Find matching model in Metis status (returns endpoint_id for API token lookup)
        model_info, endpoint_id, error_message = metis_utils.find_metis_model(metis_status, self.model)
        if error_message:
            return SubmitTaskResponse(
                error_code=404,
                error_message=error_message
            )
        
        # Check if the model is Live
        if model_info.get("status") != "Live":
            return SubmitTaskResponse(
                error_code=503,
                error_message=f"Error: '{self.model}' is not currently live on Metis. Status: {model_info.get('status')}"
            )
        
        # Use validated request data as-is (already in OpenAI format)
        # Only update the stream parameter to match the request
        api_request_data = {**data["model_params"]}
        api_request_data["stream"] = False
        # Remove internal field that shouldn't be sent to Metis
        api_request_data.pop("openai_endpoint", None)
        api_request_data.pop("api_port", None)
        
        # Log model and Metis endpoint ID
        log.info(f"Making Metis API call for model {self.model} (stream=False, endpoint={endpoint_id})")
        
        # Send request to Metis
        result, status_code, error_message = await metis_utils.call_metis_api(
            model_info,
            endpoint_id,
            api_request_data,
            stream=False
        )            
        if error_message:
            return SubmitTaskResponse(
                error_code=status_code,
                error_message=error_message
            )

        # Return Metis API results
        return SubmitTaskResponse(
            result=result,
            task_id=None
        )
            

    # Submit streaming task
    async def submit_streaming_task(self,
        data, 
        access_log_data: AccessLogPydantic = None,
        request_log_data: RequestLogPydantic = None,
        ) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""
        
        """
        if stream:
            # Handle streaming request - create AccessLog first
            request.access_log_data.status_code = 200
            try:
                access_log = await create_access_log(request.access_log_data, "", 200)
            except Exception as e:
                request.request_log_data.timestamp_compute_response = timezone.now()
                return await get_response(f"Error: Could not create AccessLog entry: {e}", 500, request)
            
            return await handle_metis_streaming_inference(
                request, model_info, endpoint_id, api_request_data, self.model, access_log
            )
        """


        return None

    async def submit_batch(self) -> SubmitBatchResponse: # <-- Needs arguments here ...
        """Submits a batch job to the compute resource."""
        pass

    async def get_batch_list(self) -> List[SubmitBatchResponse]: # <-- Needs arguments here ...
        """Get the list of a all batch jobs and their statuses."""
        pass

    async def get_batch_status(self) -> GetBatchStatusResponse: # <-- Needs arguments here ...
        """Get the status of a batch job."""
        pass

    async def get_batch_result(self) -> GetBatchResultResponse: # <-- Needs arguments here ...
        """Get the result of a completed batch job."""
        pass

    # Read-only access to the configuration
    @property
    def config(self):
        return self._config