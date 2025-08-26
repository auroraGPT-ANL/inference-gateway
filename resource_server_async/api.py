import uuid
from django.core.exceptions import ValidationError
from django.utils import timezone
from ninja import NinjaAPI, Router
from ninja.throttling import AnonRateThrottle, AuthRateThrottle
from ninja.errors import HttpError
from ninja.security import HttpBearer
from asgiref.sync import sync_to_async
from django.conf import settings
from resource_server_async.models import User, AccessLog
from resource_server_async.utils import create_access_log
from utils.auth_utils import validate_access_token
from utils.pydantic_models.db_models import AccessLogPydantic

# -------------------------------------
# ========== API declaration ==========
# -------------------------------------

# Ninja API
api = NinjaAPI(urls_namespace='resource_server_async_api')

# -------------------------------------
# ========== API rate limits ==========
# -------------------------------------

# Define rate limits
throttle = [
    AnonRateThrottle('50/s'),
    AuthRateThrottle('50/s'),
]

# Apply limits to the API
if not settings.RUNNING_AUTOMATED_TEST_SUITE:
    api.throttle = throttle

# ---------------------------------------------
# ========== API authorization layer ==========
# ---------------------------------------------

ALLOWED_STREAMING_ROUTES = [
    "/api/streaming/data/",
    "/api/streaming/error/",
    "/api/streaming/done/"
]

# Global authorization check that applies to all API routes
class GlobalAuth(HttpBearer):
    async def authenticate(self, request, access_token):

        # Simple internal secret check for remote functions if this is an "internal" streaming call
        if request.path_info in ALLOWED_STREAMING_ROUTES:
            internal_secret = request.headers.get('X-Internal-Secret', '')
            if internal_secret != getattr(settings, 'INTERNAL_STREAMING_SECRET', 'default-secret-change-me'):
                raise HttpError(401, "Unauthorized")

        # Initialize the access log data for the database entry
        access_log_data = self.__initialize_access_log_data(request)

        # Introspect the access token
        atv_response = validate_access_token(request)

        # Raise an error if the access token if not valid or if the user is not authorized
        if not atv_response.is_valid:
            _ = await create_access_log(access_log_data, atv_response.error_message, atv_response.error_code)
            raise HttpError(atv_response.error_code, atv_response.error_message)
        
        # Create a new database entry for the user (or get existing entry if already exist)
        try:
            user, created = await sync_to_async(User.objects.get_or_create, thread_sensitive=True)(
                id=atv_response.user.id,
                defaults={
                    'name': atv_response.user.name,
                    'username': atv_response.user.username,
                    'email': atv_response.user.email,
                    'idp_id': atv_response.user.idp_id,
                    'idp_name': atv_response.user.idp_name,
                    'auth_service': atv_response.user.auth_service,
                }
            )
        except Exception as e:
            error_message = f"Error: Could not create or recover user entry in the database: {e}"
            _ = await create_access_log(access_log_data, error_message, 500)
            raise HttpError(500, error_message)

        # Add user database object to the access log pydantic data
        access_log_data.user = user

        # Add info to the request object
        request.access_log_data = access_log_data
        request.user_group_uuids = atv_response.user_group_uuids

        # Make the user database object accessible through the request.auth attribute
        return user
    
    # Initialize access log data
    def __initialize_access_log_data(self, request):
        """Return initial state of an AccessLog database entry"""

        # Extract the origin IP address
        origin_ip = request.META.get("HTTP_X_FORWARDED_FOR")
        if origin_ip is None:
            origin_ip = request.META.get("REMOTE_ADDR")

        # Return data initialization (without a user)
        return AccessLogPydantic(
            id=str(uuid.uuid4()),
            user=None,
            timestamp_request=timezone.now(),
            api_route=request.path_info,
            origin_ip=origin_ip,
        )

# Apply the authorization requirement to all routes
api.auth = GlobalAuth()

# -------------------------------------------
# ========== API router definition ==========
# -------------------------------------------

router = Router()