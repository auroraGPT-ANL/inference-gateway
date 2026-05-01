import logging
from typing import Any, List, Optional

from pydantic import BaseModel

from resource_server_async.clusters.metis import MetisCluster
from resource_server_async.endpoints.direct_api import DirectAPIEndpoint
from resource_server_async.endpoints.endpoint import (
    BaseModelWithError,
)
from resource_server_async.schemas.endpoints import (
    SubmitStreamingTaskResponse,
    SubmitTaskResult,
)
from resource_server_async.utils import get_cluster_wrapper

log = logging.getLogger(__name__)


class CheckEndpointStatusResponse(BaseModelWithError):
    is_running: Optional[bool] = False


class ModelStatus(BaseModel):
    model_info: Any
    endpoint_id: str


# Metis endpoint implementation of a DirectAPIEndpoint
class MetisEndpoint(DirectAPIEndpoint):
    """Metis endpoint implementation of DirectAPIEndpoint."""

    # Class initialization
    def __init__(
        self,
        id: str,
        endpoint_slug: str,
        cluster: str,
        framework: str,
        model: str,
        endpoint_adapter: str,
        tpm_model: int,
        tpm_user: int,
        allowed_globus_groups: List[str] = None,
        allowed_domains: List[str] = None,
        config: dict = None,
    ):
        # Initialize the rest of the common attributes
        # Also pass config since it is using DirectAPIEndpoint to manage API calls
        super().__init__(
            id,
            endpoint_slug,
            cluster,
            framework,
            model,
            endpoint_adapter,
            tpm_model,
            tpm_user,
            allowed_globus_groups,
            allowed_domains,
            config,
        )

    # Check endpoint status
    async def check_endpoint_status(self) -> CheckEndpointStatusResponse:
        """Return endpoint status or an error is the endpoint cannot receive requests."""

        # Get Metis cluster wrapper from database
        response = await get_cluster_wrapper("metis")
        if response.error_message:
            return CheckEndpointStatusResponse(
                error_message=response.error_message,
                error_code=response.error_code,
            )
        cluster: MetisCluster = response.cluster

        # Get Metis cluster status
        metis_status = await cluster.get_status()

        # Extract list of running models
        model_list = []
        for running in metis_status.get("running", []):
            models = running["Models"]
            if isinstance(models, str):
                model_list.extend([model.strip() for model in models.split(",")])
            else:
                model_list.extend(models)

        # Error if model not available
        if self.model not in model_list:
            return CheckEndpointStatusResponse(
                error_message=f"Error: '{self.model}' is not currently live on Metis.",
                error_code=503,
            )

        # Return that the model is available
        return CheckEndpointStatusResponse(is_running=True)

    # Submit task
    async def submit_task(self, data) -> SubmitTaskResult:
        """Submits a single interactive task to the compute resource."""

        # Check endpoint status
        response = await self.check_endpoint_status()
        if response.error_message:
            return SubmitTaskResult(
                error_message=response.error_message, error_code=response.error_code
            )

        # Use validated request data as-is (already in OpenAI format)
        # Only update the stream parameter to match the request
        api_request_data = {**data["model_params"]}
        api_request_data["stream"] = False

        # Remove internal field that shouldn't be sent to Metis
        api_request_data.pop("openai_endpoint", None)
        api_request_data.pop("api_port", None)

        # Log model and Metis endpoint ID
        log.info(f"Making Metis API call for model {self.model} (stream=False)")

        # Send request to Metis using parent submit_task
        return await super().submit_task(api_request_data)

    # Submit streaming task
    async def submit_streaming_task(
        self, data: dict, request_log_id: str
    ) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""

        # Check endpoint status
        response = await self.check_endpoint_status()
        if response.error_message:
            return SubmitStreamingTaskResponse(
                error_message=response.error_message, error_code=response.error_code
            )

        # Use validated request data as-is (already in OpenAI format)
        # Only update the stream parameter to match the request
        api_request_data = {**data["model_params"]}
        api_request_data["stream"] = True

        # Remove internal field that shouldn't be sent to Metis
        api_request_data.pop("openai_endpoint", None)
        api_request_data.pop("api_port", None)

        # Log model and Metis endpoint ID
        log.info(f"Making Metis API call for model {self.model} (stream=True)")

        # Send streaming request to Metis using parent submit_task
        return await super().submit_streaming_task(api_request_data, request_log_id)
