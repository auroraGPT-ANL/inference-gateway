# Tool to log access requests
import logging
from typing import Dict, List

from resource_server_async.clusters.cluster import (
    JobInfo,
    Jobs,
)
from resource_server_async.clusters.direct_api import DirectAPICluster
from resource_server_async.utils import SubmitHTTPXCallResponse
from utils import metis_utils

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
        config["status_url"] = metis_utils.get_metis_status_url()
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
    async def get_formatted_status(self) -> SubmitHTTPXCallResponse:
        """Fetch and return cluster status. Can be overwritten to format output."""

        # Metis uses a status API instead of qstat
        metis_status, error_msg = await metis_utils.fetch_metis_status(use_cache=True)
        if error_msg:
            return SubmitHTTPXCallResponse(error_message=error_msg, error_code=503)

        # Declare data structure
        formatted = Jobs()
        formatted.cluster_status = {
            "cluster": "metis",
            "total_models": len(metis_status),
            "live_models": 0,
            "stopped_models": 0,
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

        # Error if something went wrong
        except Exception as e:
            return SubmitHTTPXCallResponse(
                error_message=f"Error: Something went wrong in Metis get_jobs: {e}",
                error_code=500,
            )

        # Return formatted status
        return SubmitHTTPXCallResponse(result=formatted)
