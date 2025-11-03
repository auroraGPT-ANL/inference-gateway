from resource_server_async.clusters.cluster import BaseCluster
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
        allowed_domains: str = None
    ):
        # Initialize the rest of the common attributes
        super().__init__(id, cluster_name, cluster_adapter, openai_endpoints, allowed_globus_groups, allowed_domains)