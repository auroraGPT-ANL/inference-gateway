import uuid
import time
import asyncio
from asgiref.sync import sync_to_async
from django.utils import timezone
from utils import globus_utils
from django.http import StreamingHttpResponse
from django.core.cache import cache
from pydantic import BaseModel, Field
from typing import List, Optional
from utils.pydantic_models.db_models import AccessLogPydantic, RequestLogPydantic
from resource_server_async.models import RequestLog
from resource_server_async.utils import (
    remove_endpoint_from_cache,
    prepare_streaming_task_data,
    process_streaming_completion_async,
    create_access_log,
    extract_prompt,
    get_streaming_status,
    get_streaming_error,
    get_streaming_data,
    format_streaming_error_for_openai,
    create_streaming_response_headers,
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

# Tool to log access requests
import logging
log = logging.getLogger(__name__)


# Configuration data structure
class EndpointConfig(BaseModel):
    api_port: int
    endpoint_uuid: str
    function_uuid: str
    batch_endpoint_uuid: Optional[str] = Field(default=None)
    batch_function_uuid: Optional[str] = Field(default=None)


# Globus Compute implementation of a BaseEndpoint
class GlobusCompute(BaseEndpoint):
    """Globus Compute implementation of BaseEndpoint."""
    
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
        self._config = EndpointConfig(**config)

        # Initialize the rest of the common attributes
        super().__init__(id, endpoint_slug, cluster, framework, model, endpoint_type, allowed_globus_groups, allowed_domains)


    # Submit task
    async def submit_task(self, data) -> SubmitTaskResponse:
        """Submits a single interactive task to the compute resource."""

        # Get Globus Compute client and executor
        try:
            gcc = globus_utils.get_compute_client_from_globus_app()
            gce = globus_utils.get_compute_executor(client=gcc)
        except Exception as e:
            return SubmitTaskResponse(
                error_code=500,
                error_message=str(e)
            )

        # Query the status of the targetted Globus Compute endpoint
        # NOTE: Do not await here, cache the "first" request to avoid too-many-requests Globus error
        endpoint_status, error_message = globus_utils.get_endpoint_status(
            endpoint_uuid=self.config.endpoint_uuid, client=gcc, endpoint_slug=self.endpoint_slug
        )
        if len(error_message) > 0:
            return SubmitTaskResponse(
                error_code=500,
                error_message=error_message
            )

        # Check if the endpoint is running and whether the compute resources are ready (managers deployed)
        if not endpoint_status["status"] == "online":
            return SubmitTaskResponse(
                error_code=503,
                error_message=f"Error: Endpoint {self.endpoint_slug} is offline."
            )
        resources_ready = int(endpoint_status["details"]["managers"]) > 0

        # If the compute resource is not ready (if node not acquired or worker_init phase not completed)
        if not resources_ready:

            # If a user already triggered the model (model currently loading) ...
            cache_key = f"endpoint_triggered:{self.endpoint_slug}"
            if is_cached(cache_key, create_empty=False):
                
                # Send an error to avoid overloading the Globus Compute endpoint
                # This also reduces memory footprint on the API application
                error_message = f"Error: Endpoint {self.endpoint_slug} online but not ready to receive tasks. "
                error_message += "Please try again later."
                return SubmitTaskResponse(
                    error_code=503,
                    error_message=error_message
                )

        # Add API port to the input data
        try:
            data["model_params"]["api_port"] = self.config.api_port
        except Exception as e:
            remove_endpoint_from_cache(self.endpoint_slug)
            return SubmitTaskResponse(
                error_code=400,
                error_message=f"Error: Could not process endpoint data for {self.endpoint_slug}: {e}"
            )

        # Submit Globus Compute task and wait for the result
        result, task_id, error_message, error_code = await globus_utils.submit_and_get_result(
            gce, self.config.endpoint_uuid, self.config.function_uuid, resources_ready, data=data, endpoint_slug=self.endpoint_slug
        )
        if len(error_message) > 0:
            return SubmitTaskResponse(
                error_code=error_code,
                error_message=error_message
            )

        # Return the successful result
        return SubmitTaskResponse(
            result=result,
            task_id=task_id
        )
    

    # Submit streaming task
    async def submit_streaming_task(self,
        data, 
        access_log_data: AccessLogPydantic = None,
        request_log_data: RequestLogPydantic = None,
        ) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""
        
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