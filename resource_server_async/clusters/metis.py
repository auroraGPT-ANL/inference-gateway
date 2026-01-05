from resource_server_async.clusters.cluster import BaseCluster, GetJobsResponse, Jobs, JobInfo
from utils import metis_utils
from typing import Dict, List
from django.core.cache import cache

# Tool to log access requests
import logging
log = logging.getLogger(__name__)

# Metis implementation of a BaseCluster
class MetisCluster(BaseCluster):
    """Metis implementation of BaseCluster."""
    
    # Class initialization
    def __init__(self,
        id: str,
        cluster_name: str,
        cluster_adapter: str,
        frameworks: List[str],
        openai_endpoints: List[str],
        allowed_globus_groups: List[str] = [],
        allowed_domains: List[str] = [],
        config: Dict = None
    ):
        # Initialize the rest of the common attributes
        super().__init__(id, cluster_name, cluster_adapter, frameworks, openai_endpoints, allowed_globus_groups, allowed_domains)


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

        # Metis uses a status API instead of qstat
        metis_status, error_msg = await metis_utils.fetch_metis_status(use_cache=True)
        if error_msg:
            return GetJobsResponse(error_message=error_msg, error_code=503)
        
        # Declare data structure
        formatted = Jobs()
        formatted.cluster_status = {
            "cluster": "metis",
            "total_models": len(metis_status),
            "live_models": 0,
            "stopped_models": 0
        }
        
        # For each model in the Metis cluster status
        try:
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
                    "Model Version": model_info.get("model_version", "")
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

        # Error if something went wrong
        except Exception as e:
            return GetJobsResponse(error_message=f"Error: Something went wrong in Metis get_jobs: {e}", error_code=500)
        
        # Build response
        try:
            response = GetJobsResponse(jobs=formatted)
        except Exception as e:
            return GetJobsResponse(error_message=f"Error: Could not generate GetJobsResponse: {e}", error_code=500)

        # Cache the result for 60 seconds
        try:
            cache.set(cache_key, response, 60)
        except Exception as e:
            log.warning(f"Failed to cache cluster status: {e}")

        # Return jobs result
        return response
