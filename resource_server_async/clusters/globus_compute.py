from resource_server_async.clusters.cluster import BaseCluster, GetJobsResponse, ClusterStatus
from resource_server_async.models import Endpoint
from utils import globus_utils
from pydantic import BaseModel
from django.core.cache import cache
from django.utils.text import slugify
from asgiref.sync import sync_to_async
from typing import Dict
import json

# Tool to log access requests
import logging
log = logging.getLogger(__name__)

class ClusterConfig(BaseModel):
    qstat_endpoint_uuid: str
    qstat_function_uuid: str


# Globus Compute implementation of a BaseCluster
class GlobusComputeCluster(BaseCluster):
    """Globus Compute implementation of BaseCluster."""
    
    # Class initialization
    def __init__(self,
        id: str,
        cluster_name: str,
        cluster_adapter: str,
        openai_endpoints: Dict[str,str],
        allowed_globus_groups: str = None,
        allowed_domains: str = None,
        config: ClusterConfig = None
    ):
        # Validate endpoint configuration
        self._config = ClusterConfig(**config)

        # Initialize the rest of the common attributes
        super().__init__(id, cluster_name, cluster_adapter, openai_endpoints, allowed_globus_groups, allowed_domains)


    # Get jobs
    async def get_jobs(self) -> GetJobsResponse:
        """Provides a status of the cluster as a whole, including which models are running."""

        # Redis cache key
        cache_key = f"qstat_details:{self.cluster_name}"
        
        # Try to get qstat details from Redis
        try:
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result
        except Exception as e:
            log.warning(f"Redis cache error for cluster status: {e}")

        # Get Globus Compute client and executor
        try:
            gcc = globus_utils.get_compute_client_from_globus_app()
            gce = globus_utils.get_compute_executor(client=gcc)
        except Exception as e:
            return GetJobsResponse(error_message=str(e), error_code=500)
        
        # Build temporary qstat endpoint slug
        endpoint_slug = f"{self.cluster_name}/jobs"

        # Get the status of the qstat endpoint
        # NOTE: Do not await here, cache the "first" request to avoid too-many-requests Globus error
        endpoint_status, error_message = globus_utils.get_endpoint_status(
            endpoint_uuid=self.config.qstat_endpoint_uuid, client=gcc, endpoint_slug=endpoint_slug
        )
        if len(error_message) > 0:
            return GetJobsResponse(error_message=error_message, error_code=500)
            
        # Return error message if endpoint is not online
        if not endpoint_status["status"] == "online":
            return GetJobsResponse(error_message=f"Error: Endpoint {endpoint_slug} is offline.", error_code=500)
        
        # Submit task and wait for result
        result, task_uuid, error_message, error_code = await globus_utils.submit_and_get_result(
            gce, self.config.qstat_endpoint_uuid, self.config.qstat_function_uuid, timeout=60
        )
        if len(error_message) > 0:
            return GetJobsResponse(error_message=error_message, error_code=error_code)
        
        # Try to refine the status of each endpoint (in case Globus Compute managers are lost)
        try:

            # For each running endpoint ...
            result = json.loads(result)
            for i, running in enumerate(result["running"]):

                # If the model is in a "running" state (not "starting")
                if running["Model Status"] == "running":

                    # Get compute endpoint ID from database
                    running_framework = running["Framework"]
                    running_model = running["Models"].split(",")[0]
                    running_cluster = running["Cluster"]
                    endpoint_slug = slugify(" ".join([running_cluster, running_framework, running_model]))
                    endpoint = await sync_to_async(Endpoint.objects.get)(endpoint_slug=endpoint_slug)
                    endpoint_config = json.loads(endpoint.config)
                    endpoint_uuid = endpoint_config["endpoint_uuid"]

                    # Turn the model to "disconnected" if managers are lost
                    endpoint_status, error_message = globus_utils.get_endpoint_status(
                        endpoint_uuid=endpoint_uuid, client=gcc, endpoint_slug=endpoint_slug
                    )
                    if int(endpoint_status["details"].get("managers", 0)) == 0:
                        result["running"][i]["Model Status"] = "disconnected"

            # Turn the result back to a string
            result = json.dumps(result)

        except Exception as e:
            log.warning(f"Failed to refine qstat model status: {e}")

        # Build response
        try:
            response = GetJobsResponse(status=ClusterStatus(**result))
        except Exception as e:
            return GetJobsResponse(error_message=f"Error: Could not generate GetJobsResponse: {e}", error_code=500)

        # Cache the result for 60 seconds
        try:
            cache.set(cache_key, response, 60)
        except Exception as e:
            log.warning(f"Failed to cache cluster status: {e}")

        # Return qstat result
        return response
    

    # Read-only access to the configuration
    @property
    def config(self):
        return self._config