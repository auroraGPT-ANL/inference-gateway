from pydantic import BaseModel, Field
from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from resource_server_async.models import User
from utils.auth_utils import check_permission as auth_utils_check_permission
from utils.auth_utils import CheckPermissionResponse
from inference_gateway.settings import MAINTENANCE_ERROR_NOTICES

class BaseModelWithError(BaseModel):
    error_message: Optional[str] = Field(default=None)
    error_code: Optional[int] = Field(default=None)

class CheckMaintenanceResponse(BaseModelWithError):
    is_under_maintenance: bool

class JobInfo(BaseModel):
    Models:    str
    Framework: str
    Cluster:   str
    model_config = {"extra": "allow"} # Open dictionary that allow more fields

class Jobs(BaseModel):
    running: List[JobInfo] = Field(default_factory=list)
    queued: List[JobInfo] = Field(default_factory=list)
    stopped: List[JobInfo] = Field(default_factory=list)
    others: List[JobInfo] = Field(default_factory=list)
    private_batch_running: List[JobInfo] = Field(default_factory=list)
    private_batch_queued: List[JobInfo] = Field(default_factory=list)
    cluster_status: Dict = Field(default_factory=dict)

class GetJobsResponse(BaseModelWithError):
    jobs: Optional[Jobs] = None


class BaseCluster(ABC):
    """Generic abstract base class that enforces a common set of methods for compute clusters."""

    # Class initialization
    def __init__(self,
        id: str,
        cluster_name: str,
        cluster_adapter: str,
        frameworks: List[str],
        openai_endpoints: List[str],
        allowed_globus_groups: List[str] = [],
        allowed_domains:  List[str] = []
    ):
        # Assign common self variables
        self.__id = id
        self.__cluster_name = cluster_name
        self.__cluster_adapter = cluster_adapter
        self.__frameworks = frameworks
        self.__openai_endpoints = openai_endpoints
        self.__allowed_globus_groups = allowed_globus_groups
        self.__allowed_domains = allowed_domains

    # Check maintenance
    def check_maintenance(self) -> CheckMaintenanceResponse:
        """Verify is the cluster is currently under maintenance."""
        
        # Try to check for maintenance
        try:

            # If the cluster is under maintenance ...
            if self.cluster_name in MAINTENANCE_ERROR_NOTICES:
                return CheckMaintenanceResponse(
                    is_under_maintenance=True,
                    error_message=f"Error: {MAINTENANCE_ERROR_NOTICES[self.cluster_name]}",
                    error_code=503
                )

            # If the cluster is not under maintenance ...
            else:
                return CheckMaintenanceResponse(
                    is_under_maintenance=False
                )

        # Error if something went wrong
        except Exception as e:
            return CheckMaintenanceResponse(
                is_under_maintenance=False,
                error_message=f"Error: Could not check maintenance for {self.cluster_name}: {e}",
                error_code=500
            )

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
        return self.__id

    @property
    def cluster_name(self):
        return self.__cluster_name
    
    @property
    def cluster_adapter(self):
        return self.__cluster_adapter
    
    @property
    def frameworks(self):
        return self.__frameworks
    
    @property
    def openai_endpoints(self):
        return self.__openai_endpoints

    @property
    def allowed_globus_groups(self):
        return self.__allowed_globus_groups

    @property
    def allowed_domains(self):
        return self.__allowed_domains