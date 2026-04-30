import logging
from abc import ABC, abstractmethod
from typing import List

from django.core.cache import cache

from inference_gateway.settings import MAINTENANCE_ERROR_NOTICES
from resource_server_async.errors import Unauthorized
from resource_server_async.models import User
from resource_server_async.schemas.clusters import (
    CheckMaintenanceResult,
    ClusterStatus,
    JobsByStatus,
)
from utils.auth_utils import check_permission as auth_utils_check_permission

log = logging.getLogger(__name__)


class BaseCluster(ABC):
    """Generic abstract base class that enforces a common set of methods for compute clusters."""

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
    def check_maintenance(self) -> CheckMaintenanceResult:
        """Verify is the cluster is currently under maintenance."""

        # Check Redis cache for cluster status from ALCF facility API
        cluster_status: ClusterStatus | None
        cache_key = f"cluster_status:{self.cluster_name}"

        try:
            cluster_status = cache.get(cache_key)
        except:
            cluster_status = None
            log.warning(f"Cache error for {self.cluster_name!r} status", exc_info=True)

        if not isinstance(cluster_status, dict):
            cluster_status = {"status": "unknown", "message": ""}

        if cluster_status.get("status") == "down":
            msg = cluster_status.get(
                "message", f"Cluster {self.cluster_name} is currently down."
            )
            return CheckMaintenanceResult(is_under_maintenance=True, message=msg)

        if cluster_status.get("status") == "error":
            log.warning(
                f"Cluster status check error for {self.cluster_name}: {cluster_status}"
            )

        if notice := MAINTENANCE_ERROR_NOTICES.get(self.cluster_name):
            return CheckMaintenanceResult(
                is_under_maintenance=True,
                message=notice,
            )

        return CheckMaintenanceResult(is_under_maintenance=False, message="")

    # Check permission
    def check_permission(
        self, auth: User, user_group_uuids: List[str], *, raise_exc: bool = True
    ) -> bool:
        """
        Verify is the user is permitted to access this endpoint.
        If raise_exc is True, raises Unauthorized.
        Otherwise, returns authorization status as boolean.
        """

        # Check permission
        try:
            auth_utils_check_permission(
                auth, user_group_uuids, self.allowed_globus_groups, self.allowed_domains
            )
        except Unauthorized:
            if raise_exc:
                raise
            return False

        return True

    # Mandatory definitions
    # ---------------------

    @abstractmethod
    async def get_jobs(self, auth: User) -> JobsByStatus:
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
