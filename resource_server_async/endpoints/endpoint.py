import uuid
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum
from abc import ABC, abstractmethod
from django.http import StreamingHttpResponse
from typing import List, Optional, Any
from resource_server_async.models import User
from utils.auth_utils import check_permission as auth_utils_check_permission
from utils.auth_utils import CheckPermissionResponse

class BatchStatusEnum(Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"

class BaseModelWithError(BaseModel):
    error_message: Optional[str] = Field(default=None)
    error_code: Optional[int] = Field(default=None)

class GetEndpointStatusResponse(BaseModelWithError):
    status: Optional[Any] = None

class SubmitTaskResponse(BaseModelWithError):
    result: Optional[str] = Field(default=None)
    task_id: Optional[str] = Field(default=None)

class SubmitStreamingTaskResponse(BaseModelWithError):
    response: Optional[StreamingHttpResponse] = Field(default=None)
    task_id: Optional[str] = Field(default=None)
    model_config = ConfigDict(arbitrary_types_allowed=True) # Allow non-serializable StreamingHttpResponse

class SubmitBatchResponse(BaseModelWithError):
    batch_id: Optional[str] = None
    input_file: Optional[str] = None
    status: Optional[str] = None

class GetBatchStatusResponse(BaseModelWithError):
    batch_id: Optional[str] = None
    cluster: Optional[str] = None
    created_at: Optional[str] = None
    framework: Optional[str] = None
    input_file: Optional[str] = None
    status: BatchStatusEnum

class GetBatchListResponse(BaseModelWithError):
    batch_list: Optional[List[GetBatchStatusResponse]] = None

class BatchResultMetrics(BaseModel):
    response_time: float
    throughput_tokens_per_second: float
    total_tokens: int
    num_responses: int
    lines_processed: int

class GetBatchResultResponse(BaseModelWithError):
    results_file: Optional[str] = None
    progress_file: Optional[str] = None
    metrics: Optional[BatchResultMetrics] = None


class BaseEndpoint(ABC):
    """Generic abstract base class that enforces a common set of methods for inference endpoints."""

    # Class initialization
    def __init__(self,
        id: str,
        endpoint_slug: str,
        cluster: str,
        framework: str,
        model: str,
        endpoint_adapter: str,
        allowed_globus_groups: str = None,
        allowed_domains: str = None
    ):
        # Assign common self variables 
        self._id = id
        self._endpoint_slug = endpoint_slug
        self._cluster = cluster
        self._framework = framework
        self._model = model
        self._endpoint_adapter = endpoint_adapter
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


    # Has permission (common function)
    def check_permission(self, auth: User, user_group_uuids: List[str] ) -> CheckPermissionResponse:
        """Verify is the user is permitted to access this endpoint."""
        return auth_utils_check_permission(auth, user_group_uuids, self.allowed_globus_groups, self.allowed_domains)
    
    # Mandatory definitions
    # ---------------------

    @abstractmethod
    async def get_endpoint_status(self) -> GetEndpointStatusResponse:
        """Return endpoint status or an error is the endpoint cannot receive requests."""
        pass

    @abstractmethod
    async def submit_task(self, data: dict) -> SubmitTaskResponse:
        """Submits a single interactive task to the compute resource."""
        pass

    @abstractmethod
    async def submit_streaming_task(self, data: dict, request_log_id: str) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""
        pass

    # Optional batch support
    # ----------------------

    # Redefine in the child class if needed
    def has_batch_enabled(self) -> bool:
        """Return True if batch can be used for this endpoint, False otherwise."""
        return False

    # Redefine in the child class if needed
    async def submit_batch(self) -> SubmitBatchResponse:
        """Submits a batch job to the compute resource."""
        return SubmitBatchResponse(error_message=f"Error: submit_batch disabled for endpoint {self.endpoint_slug}", error_code=501)

    # Redefine in the child class if needed
    async def get_batch_status(self) -> GetBatchStatusResponse: # <-- Needs arguments here ...
        """Get the status of a batch job."""
        return GetBatchStatusResponse(error_message=f"Error: submit_batch disabled for endpoint {self.endpoint_slug}", error_code=501)

    # Redefine in the child class if needed
    async def get_batch_list(self) -> GetBatchListResponse: # <-- Needs arguments here ...
        """Get the list of a all batch jobs and their statuses."""
        return GetBatchListResponse(error_message=f"Error: submit_batch disabled for endpoint {self.endpoint_slug}", error_code=501)

    # Redefine in the child class if needed
    async def get_batch_result(self) -> GetBatchResultResponse: # <-- Needs arguments here ...
        """Get the result of a completed batch job."""
        return GetBatchResultResponse(error_message=f"Error: submit_batch disabled for endpoint {self.endpoint_slug}", error_code=501)

    # Read-only properties
    # --------------------

    @property
    def endpoint_slug(self):
        return self._endpoint_slug

    @property
    def cluster(self):
        return self._cluster

    @property
    def framework(self):
        return self._framework

    @property
    def model(self):
        return self._model

    @property
    def endpoint_adapter(self):
        return self._endpoint_adapter

    @property
    def allowed_globus_groups(self):
        return self._allowed_globus_groups

    @property
    def allowed_domains(self):
        return self._allowed_domains