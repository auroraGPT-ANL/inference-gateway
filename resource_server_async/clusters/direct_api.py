import logging
from typing import Dict, List, Optional

from django.core.cache import cache
from pydantic import BaseModel, Field

from resource_server_async.clusters.cluster import (
    BaseCluster,
    GetJobsResponse,
)
from resource_server_async.models import User
from resource_server_async.utils import (
    SubmitHTTPXCallResponse,
    httpx_call_methods,
    submit_httpx_call,
)

log = logging.getLogger(__name__)


class ClusterConfig(BaseModel):
    status_url: str
    api_request_timeout: Optional[int] = Field(default=10)


# Direct API implementation of a BaseCluster
class DirectAPICluster(BaseCluster):
    """Direct API implementation of BaseCluster."""

    # Class initialization
    def __init__(
        self,
        id: str,
        cluster_name: str,
        cluster_adapter: str,
        frameworks: List[str],
        openai_endpoints: List[str],
        allowed_globus_groups: List[str] = [],
        allowed_domains: List[str] = [],
        config: Dict = None,
    ):
        # Validate endpoint configuration
        self.__config = ClusterConfig(**config)

        # Build request headers
        self.__headers = {
            "Content-Type": "application/json",
        }

        # Initialize the rest of the common attributes
        super().__init__(
            id,
            cluster_name,
            cluster_adapter,
            frameworks,
            openai_endpoints,
            allowed_globus_groups,
            allowed_domains,
        )

    # Get formatted cluster status
    async def get_formatted_status(self) -> SubmitHTTPXCallResponse:
        """Fetch and return cluster status. Can be overwritten to format output."""

        # Get status from cluster
        return await submit_httpx_call(
            self.config.status_url,
            headers=self.__headers,
            timeout=self.config.api_request_timeout,
            method=httpx_call_methods.get,
        )

    # Get jobs
    async def get_jobs(self, auth: User) -> GetJobsResponse:
        """Provides a status of the cluster as a whole, including which models are running."""

        # Redis cache key
        cache_key = f"qstat_details:{auth.username}:{auth.id}:{self.cluster_name}"

        # Try to get qstat details from Redis
        try:
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result
        except Exception as e:
            log.warning(f"Redis cache error for cluster status: {e}")

        # Get formatted cluster status
        response: SubmitHTTPXCallResponse = await self.get_formatted_status()

        # Send error if any
        if response.error_message:
            return GetJobsResponse(
                error_message=response.error_message,
                error_code=response.error_code,
            )

        # Build response
        try:
            response = GetJobsResponse(jobs=response.result)
        except Exception as e:
            return GetJobsResponse(
                error_message=f"Error: Could not generate GetJobsResponse: {e}",
                error_code=500,
            )

        # Cache the result for 60 seconds
        try:
            cache.set(cache_key, response, 60)
        except Exception as e:
            log.warning(f"Failed to cache cluster status: {e}")

        # Return jobs result
        return response

    # Read-only access to the configuration
    @property
    def config(self) -> ClusterConfig:
        return self.__config
