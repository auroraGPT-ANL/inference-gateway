# Tool to log access requests
import logging
from typing import Dict, List

from django.core.cache import cache

from resource_server_async.clusters.cluster import (
    JobInfo,
    Jobs,
)
from resource_server_async.clusters.direct_api import DirectAPICluster

log = logging.getLogger(__name__)


# Metis implementation of a BaseCluster
class MetisCluster(DirectAPICluster):
    """Metis implementation of BaseCluster."""

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
        # Initialize the rest of the common attributes
        super().__init__(
            id,
            cluster_name,
            cluster_adapter,
            frameworks,
            openai_endpoints,
            allowed_globus_groups,
            allowed_domains,
            config,
        )

    # Get formatted cluster status
    async def get_status(self) -> Dict:
        """Fetch and return cluster status. Can be overwritten to format output."""

        # Redis cache key
        cache_key = "metis_status_response"

        # Try to get status details from Redis
        try:
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result
        except Exception as e:
            log.warning(f"Redis cache error for metis_status_response: {e}")

        # Get the raw status data
        metis_status = await super().get_status()

        # Declare data structure
        formatted = Jobs()
        formatted.cluster_status = {
            "cluster": "metis",
            "total_models": len(metis_status),
            "live_models": 0,
            "stopped_models": 0,
        }

        # For each model in the Metis cluster status
        for model_key, model_info in metis_status.items():
            status = model_info.get("status", "Unknown")

            # Extract model name and description
            model_name = model_info.get("model", "")
            description = model_info.get("description", "")
            full_description = f"{model_name} - {description}"

            # Do not expose sensitive fields like model_key, endpoint_id, or url to users
            # Format consistently with Sophia/Polaris jobs output
            job_entry = {
                "Models": model_name,
                "Framework": "api",
                "Cluster": "metis",
                "Model Status": "running" if status == "Live" else status.lower(),
                "Description": full_description,
                "Model Version": model_info.get("model_version", ""),
            }
            job_entry = JobInfo(**job_entry)

            if status == "Live":
                formatted.running.append(job_entry)
                formatted.cluster_status["live_models"] += 1
            elif status == "Stopped":
                formatted.stopped.append(job_entry)
                formatted.cluster_status["stopped_models"] += 1
            else:
                # Any other status goes to queued
                formatted.queued.append(job_entry)

        # Cache the result for 60 seconds
        try:
            cache.set(cache_key, formatted.model_dump(), 60)
        except Exception as e:
            log.warning(f"Failed to cache metis_status_response: {e}")

        # Return formatted status
        return formatted.model_dump()
