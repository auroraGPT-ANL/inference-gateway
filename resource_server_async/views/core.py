import logging

from django.http import HttpRequest
from ninja import Router

from ..clusters import BaseCluster
from ..schemas import (
    ListEndpointsResponse,
)
from ..schemas.auth import AuthedRequest
from ..schemas.clusters import JobInfo, JobsByStatus
from ..schemas.db_models import (
    UserPydantic,
)
from ..services import (
    filter_jobs_for_user,
    get_list_endpoints_data,
)

router = Router()
log = logging.getLogger(__name__)


# Health Check (GET) - No authentication required
# Lightweight endpoint for Kubernetes/load balancer health checks
@router.get("/health", auth=None)
async def health_check(request: HttpRequest) -> dict[str, str]:
    """Lightweight health check endpoint - returns OK if API is responding."""
    return {"status": "ok"}


# Whoami (GET)
@router.get("/whoami", response=UserPydantic)
async def whoami(request: AuthedRequest) -> UserPydantic:
    """GET basic user information from access token, or error message otherwise."""
    return UserPydantic(
        id=request.auth.id,
        name=request.auth.name,
        username=request.auth.username,
        user_group_uuids=request.user_group_uuids,
        idp_id=request.auth.idp_id,
        idp_name=request.auth.idp_name,
        auth_service=request.auth.auth_service,
    )


# List Endpoints (GET)
@router.get("/list-endpoints", response=ListEndpointsResponse)
async def get_list_endpoints(request: AuthedRequest) -> ListEndpointsResponse:
    """GET request to list the available frameworks and models."""
    return await get_list_endpoints_data(request.auth, request.user_group_uuids)


# List running and queue models (GET)
@router.get("/{cluster_name}/jobs", response=JobsByStatus)
async def get_jobs(request: AuthedRequest, cluster_name: str) -> JobsByStatus:
    """GET request to list the available frameworks and models."""

    cluster = await BaseCluster.load_adapter(cluster_name)

    # Make sure the user is authorized to see this cluster
    cluster.check_permission(request.auth, request.user_group_uuids)

    # If the cluster is under maintenance, report all jobs stopped:
    if cluster.check_maintenance().is_under_maintenance:
        all_endpoints = await get_list_endpoints_data(
            request.auth, request.user_group_uuids
        )
        cluster_info = all_endpoints.clusters.get(cluster.cluster_name)
        frameworks = cluster_info.frameworks if cluster_info else {}

        return JobsByStatus(
            stopped=[
                JobInfo(Models=model, Framework=framework, Cluster=cluster.cluster_name)
                for framework, fw_info in frameworks.items()
                for model in fw_info.models
            ]
        )
    else:
        return await filter_jobs_for_user(
            cluster, request.auth, request.user_group_uuids
        )
