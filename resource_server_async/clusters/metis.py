from resource_server_async.clusters.cluster import BaseCluster, GetJobsResponse, Jobs
from utils import metis_utils
from typing import Dict

# Metis implementation of a BaseCluster
class MetisCluster(BaseCluster):
    """Metis implementation of BaseCluster."""
    
    # Class initialization
    def __init__(self,
        id: str,
        cluster_name: str,
        cluster_adapter: str,
        openai_endpoints: Dict[str,str],
        allowed_globus_groups: str = None,
        allowed_domains: str = None,
        config: Dict = None
    ):
        # Initialize the rest of the common attributes
        super().__init__(id, cluster_name, cluster_adapter, openai_endpoints, allowed_globus_groups, allowed_domains)


    # Get jobs
    async def get_jobs(self) -> GetJobsResponse:
        """Provides a status of the cluster as a whole, including which models are running."""

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
                experts = model_info.get("experts", [])
                
                # Format models list consistently with other clusters
                models_str = ",".join(experts) if isinstance(experts, list) else str(experts)
                
                # Build description from model name and description
                model_name = model_info.get("model", "")
                description = model_info.get("description", "")
                full_description = f"{model_name} - {description}" if model_name and description else (model_name or description)
                
                # Do not expose sensitive fields like model_key, endpoint_id, or url to users
                # Format consistently with Sophia/Polaris jobs output
                job_entry = {
                    "Models": models_str,
                    "Framework": "api",
                    "Cluster": "metis",
                    "Model Status": "running" if status == "Live" else status.lower(),
                    "Description": full_description,
                    "Model Version": model_info.get("model_version", "")
                }
                
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
        
        # Return 
        return GetJobsResponse(status=formatted)
