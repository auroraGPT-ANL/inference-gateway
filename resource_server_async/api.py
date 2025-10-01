import uuid
from django.conf import settings
from django.utils import timezone
from django.core.cache import cache
from django.contrib.auth import get_user_model
from ninja import NinjaAPI, Router
from ninja.throttling import AnonRateThrottle, AuthRateThrottle
from ninja.errors import HttpError
from ninja.security import HttpBearer
from asgiref.sync import sync_to_async
from resource_server_async.models import User
from resource_server_async.utils import create_access_log, is_cached
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
    AnonRateThrottle('10/s'), # Per anonymous user, if request.user is not defined
    AuthRateThrottle(f"{settings.RATE_LIMIT_PER_SEC_PER_USER}/s"), # Per user, as defined by the request.user object
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
    async def __call__(self, request):
        auth = request.headers.get("Authorization")
        if not auth:
            raise HttpError(400, "Error: Missing ('Authorization': 'Bearer <your-access-token>') in request headers.")
        return await self.authenticate(request, None) # Request is the object being used by the validate_access_token function

    # Auth check 
    async def authenticate(self, request, access_token):

        # Initialize the access log data for the database entry
        access_log_data = self.__initialize_access_log_data(request)

        # Introspect the access token
        atv_response = validate_access_token(request)

        # Add whether the access token got granted because of a special Globus Groups membership
        access_log_data.authorized_groups = atv_response.idp_group_overlap_str

        # Raise an error if the access token if not valid or if the user is not authorized
        if not atv_response.is_valid:
            cache_key = access_log_data.origin_ip + atv_response.error_message + str(atv_response.error_code)
            if not is_cached(cache_key, create_empty=True):
                _ = await create_access_log(access_log_data, atv_response.error_message, atv_response.error_code)
            raise HttpError(atv_response.error_code, atv_response.error_message)
        
        # Create a new database entry for the user (or get existing entry if already exist)
        try:
            user, created = await sync_to_async(User.objects.get_or_create, thread_sensitive=True)(
                id=atv_response.user.id,
                defaults={
                    'name': atv_response.user.name,
                    'username': atv_response.user.username,
                    'idp_id': atv_response.user.idp_id,
                    'idp_name': atv_response.user.idp_name,
                    'auth_service': atv_response.user.auth_service,
                }
            )
        except Exception as e:
            error_message = f"Error: Could not create or recover user entry in the database: {e}"
            status_code = 500
            cache_key = access_log_data.origin_ip + error_message + str(status_code)
            if not is_cached(cache_key, create_empty=True):
                _ = await create_access_log(access_log_data, error_message, status_code)
            raise HttpError(status_code, error_message)

        # Add user database object to the access log pydantic data
        access_log_data.user = user

        # Add info to the request object
        request.access_log_data = access_log_data
        request.user_group_uuids = atv_response.user_group_uuids

        # Add User object to request so that Ninja throttle can be applied per authenticated user (AuthRateThrottle)
        request.user = self.RequestLightWeigthUser(
            id=atv_response.user.id,
            username=atv_response.user.username, 
            is_superuser=False
        )

        # Make the user database object accessible through the request.auth attribute
        return user
    
    # Initialize access log data
    def __initialize_access_log_data(self, request):
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
api.auth = GlobalAuth()

# -------------------------------------------
# ========== API router definition ==========
# -------------------------------------------

router = Router()