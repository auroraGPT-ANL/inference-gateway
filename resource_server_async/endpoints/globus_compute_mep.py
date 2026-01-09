from pydantic import BaseModel, Field
from typing import Optional, List
from utils import globus_utils
from resource_server_async.endpoints.globus_compute import GlobusComputeEndpoint
from resource_server_async.endpoints.endpoint import SubmitBatchResponse, GetBatchStatusResponse
import logging
log = logging.getLogger(__name__)


class GlobusComputeMEPConfig(BaseModel):
    api_port: int
    endpoint_uuid: str
    function_uuid: str
    account: str
    queue: str
    walltime: str
    worker_init: str
    select_options: Optional[str] = Field(default=None)
    max_retries_on_system_failure: Optional[int] = Field(default=2)
    max_workers_per_node: Optional[int] = Field(default=100)
    max_idletime: Optional[int] = Field(default=7200)
    max_blocks: Optional[int] = Field(default=1)
    min_blocks: Optional[int] = Field(default=0)
    nodes_per_block: Optional[int] = Field(default=1)


# Globus Compute Multi-User endpoint extension of GlobusComputeEndpoint
class GlobusComputeMEP(GlobusComputeEndpoint):
    """Globus Compute Multi-User Endpoint implementation of GlobusComputeEndpoint."""
    
    # Class initialization
    def __init__(self,
        id: str,
        endpoint_slug: str,
        cluster: str,
        framework: str,
        model: str,
        endpoint_adapter: str,
        allowed_globus_groups: List[str] = None,
        allowed_domains: List[str] = None,
        config: GlobusComputeMEPConfig = None
    ):

        # Initialize the rest of the common attributes
        super().__init__(id, endpoint_slug, cluster, framework, model, endpoint_adapter, allowed_globus_groups, allowed_domains, config)

        # Validate multi-user endpoint configuration
        self.__mep_config = GlobusComputeMEPConfig(**config)

        # Disable managers check for multi-user endpoint
        self.check_managers = False
    

    # Call to Globus Utils submit_and_get_result function
    # Re-definition from the inherited GlobusComputeEndpoint class
    async def _submit_and_get_result(self, gce, data):

        # Attach multi-user endpoint configuration to the Globus Compute executor
        gce.user_endpoint_config = {
            "account": self.mep_config.account,
            "queue": self.mep_config.queue,
            "walltime": self.mep_config.walltime,
            "worker_init": self.mep_config.worker_init,
            "select_options": self.mep_config.select_options,
            "max_retries_on_system_failure": self.mep_config.max_retries_on_system_failure,
            "max_workers_per_node": self.mep_config.max_workers_per_node,
            "max_idletime": self.mep_config.max_idletime,
            "max_blocks": self.mep_config.max_blocks,
            "min_blocks": self.mep_config.min_blocks,
            "nodes_per_block": self.mep_config.nodes_per_block
        }

        # Send request to Metis using parent submit_task
        return await globus_utils.submit_and_get_result(
            gce, self.config.endpoint_uuid, self.config.function_uuid, data=data, endpoint_slug=self.endpoint_slug
        )

    
    # Batch mode disabled
    def has_batch_enabled(self) -> bool:
        """Return True if batch can be used for this endpoint, False otherwise."""
        return False

    # Batch mode disabled
    async def submit_batch(self, batch_data, username) -> SubmitBatchResponse:
        """Submits a batch job to the compute resource."""
        return SubmitBatchResponse(
            error_message=f"Error: submit_batch unavailable for endpoint {self.endpoint_slug}", 
            error_code=501
        )

    # Batch mode disabled
    async def get_batch_status(self, batch) -> GetBatchStatusResponse:
        """Get the status and results of a batch job."""
        return GetBatchStatusResponse(
            error_message=f"Error: get_batch_status unavailable for endpoint {self.endpoint_slug}", 
            error_code=501
        )
    
    # Read-only access to the configuration
    @property
    def mep_config(self) -> GlobusComputeMEPConfig:
        return self.__mep_config