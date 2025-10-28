import uuid
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum
from abc import ABC, abstractmethod
from django.http import StreamingHttpResponse
from typing import List, Optional
from resource_server_async.models import User
from utils.pydantic_models.db_models import AccessLogPydantic, RequestLogPydantic


class BatchStatusEnum(Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"

class CheckPermissionResponse(BaseModel):
    is_authorized: bool
    error_message: Optional[str] = Field(default=None)
    error_code: Optional[int] = Field(default=None)

class SubmitTaskResponse(BaseModel):
    result: Optional[str] = Field(default=None)
    task_id: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    error_code: Optional[int] = Field(default=None)

class SubmitStreamingTaskResponse(BaseModel):
    response: Optional[StreamingHttpResponse] = Field(default=None)
    task_id: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    error_code: Optional[int] = Field(default=None)
    model_config = ConfigDict(arbitrary_types_allowed=True) # Allow non-serializable StreamingHttpResponse

class SubmitBatchResponse(BaseModel):
    batch_id: str
    input_file: str
    status: str

class GetBatchStatusResponse(BaseModel):
    batch_id: str
    cluster: str
    created_at: str
    framework: str
    input_file: str
    status: BatchStatusEnum

class BatchResultMetrics(BaseModel):
    response_time: float
    throughput_tokens_per_second: float
    total_tokens: int
    num_responses: int
    lines_processed: int

class GetBatchResultResponse(BaseModel):
    results_file: str
    progress_file: str
    metrics: BatchResultMetrics


class BaseEndpoint(ABC):
    """Generic abstract base class that enforces a common set of methods for compute resources."""

    # Class initialization
    def __init__(self,
        id: str = None,
        endpoint_slug: str = None,
        cluster: str = None,
        framework: str = None,
        model: str = None,
        endpoint_type: str = None,
        allowed_globus_groups: str = None,
        allowed_domains: str = None
    ):
        # Assign common self variables 
        self._id = id
        self._endpoint_slug = endpoint_slug
        self._cluster = cluster
        self._framework = framework
        self._model = model
        self._endpoint_type = endpoint_type
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
    def check_permission(self, auth: User, user_group_uuids: List[str] ) -> CheckPermissionResponse: # <-- Needs arguments here ...
        """Verify is the user is permitted to access this endpoint."""
        
        # Look at Globus Group permissions
        if self.allowed_globus_groups:
            if len(set(user_group_uuids) & set(self.allowed_globus_groups)) == 0:
                return CheckPermissionResponse(
                    is_authorized=False,
                    error_message=f"Error: Permission denied to endpoint {self.endpoint_slug} due to Globus Group restrictions.",
                    error_code=401
                )
        
        # Extract user's domain from the IdP used during authentication
        try:
            user_domain = auth.username.split("@")[1]
        except Exception:
            return CheckPermissionResponse(
                is_authorized=False,
                error_message=f"Error: Could not extract domain from user {auth.username}.",
                error_code=500
            )
        
        # Look at domain (policy) permissions
        if self.allowed_domains:
            if user_domain not in self.allowed_domains:
                return CheckPermissionResponse(
                    is_authorized=False,
                    error_message=f"Error: Permission denied to endpoint {self.endpoint_slug} due to IdP domain restrictions.",
                    error_code=401
                )

        # Grant access if nothing wrong was detected
        return CheckPermissionResponse(
            is_authorized=True
        )

    @abstractmethod
    async def submit_task(self, data) -> SubmitTaskResponse:
        """Submits a single interactive task to the compute resource."""
        pass

    @abstractmethod
    async def submit_streaming_task(self,
        data, 
        access_log_data: AccessLogPydantic = None,
        request_log_data: RequestLogPydantic = None
        ) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""
        pass

    @abstractmethod
    async def submit_batch(self) -> SubmitBatchResponse: # <-- Needs arguments here ...
        """Submits a batch job to the compute resource."""
        pass

    @abstractmethod
    async def get_batch_status(self) -> GetBatchStatusResponse: # <-- Needs arguments here ...
        """Get the status of a batch job."""
        pass

    @abstractmethod
    async def get_batch_list(self) -> List[GetBatchStatusResponse]: # <-- Needs arguments here ...
        """Get the list of a all batch jobs and their statuses."""
        pass

    @abstractmethod
    async def get_batch_result(self) -> GetBatchResultResponse: # <-- Needs arguments here ...
        """Get the result of a completed batch job."""
        pass

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
    def endpoint_type(self):
        return self._endpoint_type

    @property
    def allowed_globus_groups(self):
        return self._allowed_globus_groups

    @property
    def allowed_domains(self):
        return self._allowed_domains