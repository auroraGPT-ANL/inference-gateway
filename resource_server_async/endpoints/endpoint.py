from abc import ABC, abstractmethod
from typing import Any, List

from resource_server_async.auth import check_permission as auth_utils_check_permission
from resource_server_async.cache import get_redis_client
from resource_server_async.errors import BatchUnavailable, Unauthorized
from resource_server_async.models import BatchLog, User
from resource_server_async.rate_limiters import TokenLimiterCheck, TokenRateLimiter
from resource_server_async.schemas.batch import BatchSubmit
from resource_server_async.schemas.endpoints import (
    BatchStatusResult,
    SubmitBatchResult,
    SubmitStreamingTaskResponse,
    SubmitTaskResult,
)


class BaseEndpoint(ABC):
    """Generic abstract base class that enforces a common set of methods for inference endpoints."""

    # Class initialization
    def __init__(
        self,
        id: str,
        endpoint_slug: str,
        cluster: str,
        framework: str,
        model: str,
        endpoint_adapter: str,
        tpm_model: int,
        tpm_user: int,
        allowed_globus_groups: list[str] | None = None,
        allowed_domains: list[str] | None = None,
    ):
        # Assign common self variables
        self.__id = id
        self.__endpoint_slug = endpoint_slug
        self.__cluster = cluster
        self.__framework = framework
        self.__model = model
        self.__endpoint_adapter = endpoint_adapter
        self.__allowed_globus_groups = allowed_globus_groups
        self.__allowed_domains = allowed_domains
        self.__token_limiter = BaseEndpoint.build_token_limiter(
            cluster, framework, model, tpm_model, tpm_user
        )

    # Check permission
    def check_permission(
        self, auth: User, user_group_uuids: List[str], *, raise_exc: bool = True
    ) -> bool:
        """
        Verify is the user is permitted to access this endpoint.
        If raise_exc is True, raises Unauthorized.
        Otherwise, returns authorization status as boolean.
        """

        try:
            auth_utils_check_permission(
                auth, user_group_uuids, self.allowed_globus_groups, self.allowed_domains
            )
        except Unauthorized:
            if raise_exc:
                raise
            return False

        return True

    def check_token_rate_limit(self, auth: User) -> TokenLimiterCheck:
        if self.__token_limiter is None:
            return TokenLimiterCheck(True, 0, 0)
        return self.__token_limiter.check(auth.id)

    def record_token_usage(self, auth: User | None, tokens: int) -> None:
        if self.__token_limiter is None:
            return

        user_id = auth.id if auth is not None else None
        self.__token_limiter.record(user_id, tokens)

    # Mandatory definitions
    # ---------------------

    @abstractmethod
    async def submit_task(self, data: dict[str, Any]) -> SubmitTaskResult:
        """Submits a single interactive task to the compute resource."""
        pass

    @abstractmethod
    async def submit_streaming_task(
        self, data: dict[str, Any], request_log_id: str
    ) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""
        pass

    # Optional batch support (deactivated by default)
    # -----------------------------------------------

    # Redefine in the child class if needed
    def has_batch_enabled(self) -> bool:
        """Return True if batch can be used for this endpoint, False otherwise."""
        return False

    # Redefine in the child class if needed
    async def submit_batch(
        self, batch_data: BatchSubmit, username: str
    ) -> SubmitBatchResult:
        """Submits a batch job to the compute resource."""
        raise BatchUnavailable(
            f"submit_batch unavailable for endpoint {self.endpoint_slug}",
            status_code=501,
        )

    # Redefine in the child class if needed
    async def get_batch_status(self, batch: BatchLog) -> BatchStatusResult:
        """Get the status and results of a batch job."""
        raise BatchUnavailable(
            f"get_batch_status unavailable for endpoint {self.endpoint_slug}",
            status_code=501,
        )

    # Read-only properties
    # --------------------

    @property
    def id(self):
        return self.__id

    @property
    def endpoint_slug(self):
        return self.__endpoint_slug

    @property
    def cluster(self):
        return self.__cluster

    @property
    def framework(self):
        return self.__framework

    @property
    def model(self):
        return self.__model

    @property
    def endpoint_adapter(self):
        return self.__endpoint_adapter

    @property
    def allowed_globus_groups(self):
        return self.__allowed_globus_groups

    @property
    def allowed_domains(self):
        return self.__allowed_domains

    @staticmethod
    def build_token_limiter(
        cluster: str, framework: str, model: str, tpm_model: int, tpm_user: int
    ) -> TokenRateLimiter | None:
        """
        Builds a TokenRateLimiter; returns None if Redis client is not available
        """
        redis = get_redis_client()
        if redis is None:
            return None

        return TokenRateLimiter(
            redis,
            f"{cluster}:{framework}:{model}",
            tpm_model=tpm_model,
            tpm_user=tpm_user,
        )
