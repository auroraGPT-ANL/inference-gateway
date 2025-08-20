from django.core.exceptions import ValidationError
from ninja import NinjaAPI, Router
from ninja.throttling import AnonRateThrottle, AuthRateThrottle
from ninja.errors import HttpError
from ninja.security import HttpBearer
from asgiref.sync import sync_to_async
from django.conf import settings
from utils.auth_utils import validate_access_token
from resource_server_async.models import User

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

# Global authorization check that applies to all API routes
class GlobalAuth(HttpBearer):
    async def authenticate(self, request, access_token):

        # Introspect the access token
        atv_response = validate_access_token(request)

        # Raise an error if the access token if not valid or if the user is not authorized
        if not atv_response.is_valid:
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
            raise HttpError(500, f"Error: Could not create or recover user entry in the database: {e}")
        
        # If the user already existed ...
        # Raise error if the new data is inconsistent with the database
        if not created:
            data_mismatches = []
            if not user.name == atv_response.user.name:
                data_mismatches.append(f"name: '{user.name}' vs '{atv_response.user.name}'")
            if not user.username == atv_response.user.username:
                data_mismatches.append(f"username: '{user.username}' vs '{atv_response.user.username}'")
            if not user.email == atv_response.user.email:
                data_mismatches.append(f"email: '{user.email}' vs '{atv_response.user.email}'")
            if not user.idp_id == atv_response.user.idp_id:
                data_mismatches.append(f"idp_id: '{user.idp_id}' vs '{atv_response.user.idp_id}'")
            if not user.idp_name == atv_response.user.idp_name:
                data_mismatches.append(f"idp_name: '{user.idp_name}' vs '{atv_response.user.idp_name}'")
            if not user.auth_service == atv_response.user.auth_service:
                data_mismatches.append(f"auth_service: '{user.auth_service}' vs '{atv_response.user.auth_service}'")
            if data_mismatches:
                raise HttpError(401, f"Error: Data mismatch for user {user.id}: {', '.join(data_mismatches)}")
                
        # Replace the user object with the database instance
        atv_response.user = user

        # Return the access token validation response to the API view 
        return atv_response

# Apply the authorization requirement to all routes
api.auth = GlobalAuth()

# -------------------------------------------
# ========== API router definition ==========
# -------------------------------------------

router = Router()