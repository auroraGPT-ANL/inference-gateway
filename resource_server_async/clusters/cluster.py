import uuid
from pydantic import BaseModel, Field
from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from resource_server_async.models import User
from utils.auth_utils import check_permission as auth_utils_check_permission
from utils.auth_utils import CheckPermissionResponse

class BaseModelWithError(BaseModel):
    error_message: Optional[str] = Field(default=None)
    error_code: Optional[int] = Field(default=None)

class Jobs(BaseModel):
    running: List[Dict] = Field(default_factory=list)
    queued: List[Dict] = Field(default_factory=list)
    stopped: List[Dict] = Field(default_factory=list)
    others: List[Dict] = Field(default_factory=list)
    private_batch_running: List[Dict] = Field(default_factory=list)
    private_batch_queued: List[Dict] = Field(default_factory=list)
    cluster_status: Dict = Field(default_factory=dict)

class GetJobsResponse(BaseModelWithError):
    status: Optional[Jobs] = None


class BaseCluster(ABC):
    """Generic abstract base class that enforces a common set of methods for compute clusters."""

    # Class initialization
    def __init__(self,
        id: str,
        cluster_name: str,
        cluster_adapter: str,
        openai_endpoints: Dict[str,str],
        allowed_globus_groups: str = None,
        allowed_domains: str = None,
    ):
        # Assign common self variables
        self._id = id
        self._cluster_name = cluster_name
        self._cluster_adapter = cluster_adapter
        self._openai_endpoints = openai_endpoints
        self._allowed_globus_groups = allowed_globus_groups
        self._allowed_domains = allowed_domains

        # Extract list of allowed globus group IDs and make sure they are in the UUID format
        self._allowed_globus_groups = [g.strip() for g in self._allowed_globus_groups.split(",") if g.strip()]
        for uuid_to_test in self._allowed_globus_groups:
            try:
                _ = uuid.UUID(uuid_to_test).version
            except Exception as e:
                raise Exception(f"Error: Could not extract UUID format from the database. {e}")
        
        # Extract list of allowed domains
        self._allowed_domains = [d.strip() for d in self._allowed_domains.split(",") if d.strip()]

    # Check permission
    def check_permission(self, auth: User, user_group_uuids: List[str]) -> CheckPermissionResponse:
        """Verify is the user is permitted to access this endpoint."""

        # Check permission
        response = auth_utils_check_permission(auth, user_group_uuids, self.allowed_globus_groups, self.allowed_domains)
        if response.error_message:
            return CheckPermissionResponse(
                is_authorized=False,
                error_message=response.error_message,
                error_code=response.error_code
            )
        
        # Return permission check result
        return CheckPermissionResponse(is_authorized=response.is_authorized)
    
    # Mandatory definitions
    # ---------------------

    @abstractmethod
    async def get_jobs(self) -> GetJobsResponse:
        """Provides a status of the cluster as a whole, including which models are running."""
        pass

    # Read-only properties
    # --------------------

    @property
    def id(self):
        return self._id

    @property
    def cluster_name(self):
        return self._cluster_name
    
    @property
    def cluster_adapter(self):
        return self._cluster_adapter
    
    @property
    def openai_endpoints(self):
        return self._openai_endpoints

    @property
    def allowed_globus_groups(self):
        return self._allowed_globus_groups

    @property
    def allowed_domains(self):
        return self._allowed_domains