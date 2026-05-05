import logging
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import NinjaAPI
from ninja.errors import HttpError
from ninja.security import HttpBearer
from ninja.throttling import AnonRateThrottle, AuthRateThrottle, BaseThrottle

from resource_server_async.auth import validate_access_token
from resource_server_async.models import User
from resource_server_async.schemas.db_models import (
    AccessLogPydantic,
)

from .errors import BaseError, TaskPending
from .views import router

logger = logging.getLogger(__name__)


# -------------------------------------
# ========== API declaration ==========
# -------------------------------------

# Ninja API
api = NinjaAPI(
    title="ALCF Inference Service", urls_namespace="resource_server_async_api"
)

# -------------------------------------
# ========== API rate limits ==========
# -------------------------------------

# Define rate limits
throttle: list[BaseThrottle] = [
    AnonRateThrottle("10/s"),  # Per anonymous user, if request.user is not defined
    AuthRateThrottle(
        f"{settings.RATE_LIMIT_PER_SEC_PER_USER}/s"
    ),  # Per user, as defined by the request.user object
]

# Apply limits to the API
if not settings.RUNNING_AUTOMATED_TEST_SUITE:
    api.throttle = throttle

# ---------------------------------------------
# ========== API authorization layer ==========
# ---------------------------------------------


# Global authorization check that applies to all API routes
class GlobalAuth(HttpBearer):
    # Django User class to populate request.user
    RequestLightWeigthUser = get_user_model()

    # Custom error message if Authorization headers is missing
    async def __call__(self, request: HttpRequest) -> User | None:
        auth = request.headers.get("Authorization")
        if not auth:
            raise HttpError(
                401,
                "Error: Missing ('Authorization': 'Bearer <your-access-token>') in request headers.",
            )
        return await self.authenticate(
            request, None
        )  # Request is the object being used by the validate_access_token function

    # Auth check
    async def authenticate(self, request: HttpRequest, token: str | None) -> User:
        # Initialize the access log data for the database entry
        access_log_data = self.__initialize_access_log_data(request)
        request.access_log_data = access_log_data  # type: ignore[attr-defined]

        # Introspect and validate the access token
        # Raises Unauthorized (HTTP 401) if authentication fails:
        atv_response = validate_access_token(request)

        # Add whether the access token got granted because of a special Globus Groups membership
        access_log_data.authorized_groups = atv_response.idp_group_overlap_str

        # Create a new database entry for the user (or get existing entry if already exist)
        try:
            user, created = await sync_to_async(
                User.objects.get_or_create, thread_sensitive=True
            )(
                id=atv_response.user.id,
                defaults={
                    "name": atv_response.user.name,
                    "username": atv_response.user.username,
                    "idp_id": atv_response.user.idp_id,
                    "idp_name": atv_response.user.idp_name,
                    "auth_service": atv_response.user.auth_service,
                },
            )
        except Exception as e:
            error_message = (
                f"Error: Could not create or recover user entry in the database: {e}"
            )
            raise HttpError(500, error_message)

        # Add user database object to the access log pydantic data
        access_log_data.user = user

        # Add info to the request object
        request.user_group_uuids = atv_response.user_group_uuids  # type: ignore[attr-defined]

        # Add User object to request so that Ninja throttle can be applied per authenticated user (AuthRateThrottle)
        request.user = self.RequestLightWeigthUser(
            id=atv_response.user.id,
            username=atv_response.user.username,
            is_superuser=False,
        )

        # Make the user database object accessible through the request.auth attribute
        return user

    # Initialize access log data
    def __initialize_access_log_data(self, request: HttpRequest) -> AccessLogPydantic:
        """Return initial state of an AccessLogPydantic entry"""

        # Extract the origin IP address
        origin_ip = request.META.get("HTTP_X_FORWARDED_FOR")
        if origin_ip is None:
            origin_ip = request.META.get("REMOTE_ADDR")

        # Remove duplicate if any
        if origin_ip:
            ip_list = [ip.strip() for ip in origin_ip.split(",")]
            origin_ip = ", ".join(set(ip_list))

        # Return data initialization (without a user)
        return AccessLogPydantic(
            id=str(uuid.uuid4()),
            user=None,
            timestamp_request=timezone.now(),
            api_route=request.path_info,
            origin_ip=origin_ip,
        )


# Apply the authorization requirement to all routes
api.auth = [GlobalAuth()]


@api.exception_handler(BaseError)
def handle_app_error(request: HttpRequest, exc: BaseError) -> HttpResponse:
    return api.create_response(
        request,
        {"error": {"code": exc.code, "message": str(exc), "info": exc.info}},
        status=exc.status_code,
    )


@api.exception_handler(TaskPending)
def handle_pending(request: HttpRequest, exc: TaskPending) -> HttpResponse:
    response = api.create_response(
        request,
        {"status": exc.code, "task_id": exc.task_id},
        status=exc.status_code,
    )
    response["Retry-After"] = str(exc.retry_after)
    return response


@api.exception_handler(Exception)
def handle_uncaught_error(request: HttpRequest, exc: Exception) -> HttpResponse:
    error_id = uuid.uuid4().hex
    logger.exception(
        f"Uncaught Exception {error_id=} in API View {request.path!r}", exc_info=exc
    )

    return api.create_response(
        request,
        {
            "error": {
                "code": "internal_error",
                "message": "Internal Server Error",
                "error_id": error_id,
            }
        },
        status=500,
    )


api.add_router("/", router)
