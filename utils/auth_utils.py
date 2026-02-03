from pydantic import BaseModel, Field
from typing import Optional, List
from dataclasses import dataclass, field
from resource_server_async.models import AuthService, User
from utils.pydantic_models.db_models import UserPydantic
from django.conf import settings
import globus_sdk
import time

# Cache tools to limits how many calls are made to Globus servers
from cachetools import TTLCache, cached

# Tool to log access requests
import logging

log = logging.getLogger(__name__)


# Exception to raise in case of errors
class AuthUtilsError(Exception):
    pass


# Data structure returned by the access token validation function
class ATVResponse(BaseModel):
    is_valid: bool
    user: Optional[UserPydantic] = None
    user_group_uuids: List[str] = field(default_factory=lambda: [])
    idp_group_overlap_str: Optional[str] = None
    error_message: str = ""
    error_code: int = 0


# Get Globus SDK confidential client
def get_globus_client():
    return globus_sdk.ConfidentialAppAuthClient(
        settings.GLOBUS_APPLICATION_ID, settings.GLOBUS_APPLICATION_SECRET
    )


# Redis-compatible token introspection with fallback to in-memory cache
def introspect_token(bearer_token: str):
    """
    Introspect a token with policies, collect group memberships, and return the response.
    Uses Redis cache for multi-worker support with fallback to in-memory cache.

    Returns serializable data instead of Globus SDK objects.
    """
    from django.core.cache import cache
    import hashlib

    # Create cache key from token hash (don't store raw tokens in cache keys)
    # Store the entire hash to avoid collisions where different users would have the same last hash digits
    token_hash = hashlib.sha256(bearer_token.encode()).hexdigest()
    cache_key = f"token_introspect:{token_hash}"

    # Try to get from Redis cache first
    try:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result
    except Exception as e:
        log.warning(f"Redis cache error for token introspection: {e}")
        # Fall back to in-memory cache
        return _introspect_token_memory_cache(bearer_token)

    # If not in cache, perform introspection
    result = _perform_token_introspection(bearer_token)

    # If the introspection triggered an error ...
    if result[0] is None:
        # Set cache time (shorter for errors)
        ttl = 60

    # If the introspection was successful ...
    else:
        # Calculate time until token expiration (Unix timestamp difference)
        try:
            introspection_exp = result[0]["exp"]
            seconds_until_expiration = introspection_exp - int(time.time())
        except Exception as e:
            log.warning(f"Failed to extract introspection result[0]['exp']: {e}")
            seconds_until_expiration = 0

        # Set cache time and make sure it is not shorter than the time until token expiration
        ttl = min(600, seconds_until_expiration)

    # Cache the result (successful or error)
    try:
        cache.set(cache_key, result, ttl)
    except Exception as e:
        log.warning(f"Failed to cache token introspection: {e}")
        # Still return the result even if caching fails

    return result


# In-memory cache fallback for token introspection
@cached(cache=TTLCache(maxsize=1024, ttl=60 * 10))
def _introspect_token_memory_cache(bearer_token: str):
    """Fallback in-memory cache for token introspection"""
    return _perform_token_introspection(bearer_token)


def _perform_token_introspection(bearer_token: str):
    """
    Perform the actual token introspection and return serializable data.
    """
    # Create Globus SDK confidential client
    try:
        client = get_globus_client()
    except Exception as e:
        return None, [], f"Error: Could not create Globus confidential client. {e}"

    # Include the access token and Globus policies (if needed) in the instrospection
    introspect_body = {"token": bearer_token}
    if settings.NUMBER_OF_GLOBUS_POLICIES > 0:
        introspect_body["authentication_policies"] = settings.GLOBUS_POLICIES
    introspect_body["include"] = "session_info,identity_set_detail"

    # Introspect the token through the Globus Auth API (including policy evaluation)
    try:
        introspection = client.post(
            "/v2/oauth2/token/introspect", data=introspect_body, encoding="form"
        )
        # Convert to serializable dict
        introspection_data = (
            dict(introspection.data)
            if hasattr(introspection, "data")
            else dict(introspection)
        )
    except Exception as e:
        return (
            None,
            [],
            f"Error: Could not introspect token with Globus /v2/oauth2/token/introspect. {e}",
        )

    # Error if the token is invalid
    if introspection_data["active"] is False:
        return None, [], "Error: Token is either not active or invalid"

    # Get dependent access token to view group membership
    try:
        dependent_tokens = client.oauth2_get_dependent_tokens(bearer_token)
        access_token = dependent_tokens.by_resource_server["groups.api.globus.org"][
            "access_token"
        ]
    except Exception as e:
        return (
            None,
            [],
            f"Error: Could not recover dependent access token for groups.api.globus.org. {e}",
        )

    # Create a Globus Group Client using the access token sent by the user
    try:
        authorizer = globus_sdk.AccessTokenAuthorizer(access_token)
        groups_client = globus_sdk.GroupsClient(authorizer=authorizer)
    except Exception as e:
        return None, [], f"Error: Could not create GroupsClient. {e}"

    # Get the list of user's group memberships
    try:
        user_groups_response = groups_client.get_my_groups()
        user_groups = [group["id"] for group in user_groups_response]
    except Exception as e:
        return None, [], f"Error: Could not recover user group memberships. {e}"

    # Return the introspection data along with the group (with empty error message)
    return introspection_data, user_groups, ""


# Check Globus Policies
def check_globus_policies(introspection):
    """
    Define whether an authenticated user respect the Globus policies.
    User should meet all Globus policies requirements.
    """

    # Return False if policies cannot be evaluated went wrong
    if (
        not len(introspection["policy_evaluations"])
        == settings.NUMBER_OF_GLOBUS_POLICIES
    ):
        return (
            False,
            "Error: Some Globus policies could not be passed to the introspect API call.",
        )

    # Return False if the user failed to meet one of the policies
    for policies in introspection["policy_evaluations"].values():
        if policies.get("evaluation", False) == False:
            error_message = "Error: Permission denied from internal policies. "
            error_message += "This is likely due to a high-assurance timeout. "
            error_message += "Please logout by visiting https://app.globus.org/logout, "
            error_message += "and re-authenticate with the following command: "
            error_message += "'python3 inference_auth_token.py authenticate --force'. "
            error_message += (
                "Make sure you authenticate with an authorized identity provider: "
            )
            error_message += f"{settings.AUTHORIZED_IDP_DOMAINS_STRING}."
            return False, error_message

    # Return True if the user met all of the policies requirements
    return True, ""


# User In Allowed Groups
def check_globus_groups(user_groups):
    """
    Define whether an authenticated user has the proper Globus memberships.
    User should be member of at least in one of the allowed Globus groups.
    """

    # Grant access if the user is a member of at least one of the allowed Globus Groups
    if len(set(user_groups).intersection(settings.GLOBUS_GROUPS)) > 0:
        return True, ""

    # Deny access if authenticated user is not part of any of the allowed Globus Groups
    else:
        return False, f"Error: User is not a member of an allowed Globus Group."


# Check Session Info
def check_session_info(introspection, user_groups):
    """
    Look into the session_info field of the token introspection
    and check whether the authentication was made through one
    of the authorized identity providers. Collect and return the
    User details if possible
    """

    # Try to check if an authentication came from authorized provider
    try:
        # For each active authentication session ...
        session_info_identities = []
        for session_idp in [
            auth["idp"]
            for auth in introspection["session_info"]["authentications"].values()
        ]:
            # Recover the domain (e.g. anl.gov) tied to the active session
            identity = next(
                (
                    i
                    for i in introspection["identity_set_detail"]
                    if i["identity_provider"] == session_idp
                )
            )
            session_domain = identity["username"].split("@")[1]
            session_info_identities.append(identity)

            # If the domain is authorized by the service ...
            if session_domain in settings.AUTHORIZED_IDP_DOMAINS:
                # Create the User object from the Globus introspection
                try:
                    user = UserPydantic(
                        id=identity["sub"],
                        name=identity["name"]
                        if isinstance(identity["name"], str)
                        else "",
                        username=identity["username"],
                        user_group_uuids=user_groups,
                        idp_id=identity["identity_provider"],
                        idp_name=identity["identity_provider_display_name"],
                        auth_service=AuthService.GLOBUS.value,
                    )
                except Exception as e:
                    return False, None, f"Error: Could not create User object: {e}"

                # Return successful check along with user details
                return True, user, ""

    # Revoke access if something went wrong during the check
    except Exception as e:
        return False, None, f"Error: Could not inspect session info: {e}"

    # If user not authorized, extract user details for error message
    try:
        user_str = []
        for identity in session_info_identities:
            user_str.append(f"{identity['name']} ({identity['username']})")
        user_str = ", ".join(user_str)
        if len(user_str) == 0:
            user_str = "Unknown (no active session found)"
    except Exception as e:
        user_str = "could not recover user identity"

    # Revoke access if authentication did not come from authorized provider
    error_message = ""
    error_message += f"Error: Permission denied. Must authenticate with {settings.AUTHORIZED_IDP_DOMAINS_STRING}. "
    error_message += f"Currently authenticated as {user_str}. "
    error_message += "If you are passing an access token directly to this API, "
    error_message += (
        "please logout from Globus by visiting https://app.globus.org/logout "
    )
    error_message += "and re-authenticate with the following command: "
    error_message += "'python3 inference_auth_token.py authenticate --force'."
    return False, None, error_message


# Check Session Info
def check_groups_per_idp(user: UserPydantic, user_groups: List[str]):
    """
    Make sure the user is part of an authorized Globus Group (if any)
    associated with a given identity provider.

    Returns: True/False if granted or not, error_message, group_overlap
    """

    # Extract the user's IdP domain
    try:
        idp_domain = user.username.split("@")[1]
    except:
        return (
            False,
            "Error: Could not extract IdP domain from user.username.split('@')[1].",
            None,
        )

    # If there is a Globus Group check tied to this identity provider ...
    if idp_domain in settings.AUTHORIZED_GROUPS_PER_IDP:
        # Error if the user is a member of any authorized Globus Groups
        group_overlap = set(user_groups) & set(
            settings.AUTHORIZED_GROUPS_PER_IDP[idp_domain]
        )
        if len(group_overlap) == 0:
            return (
                False,
                f"Error: Permission denied. User ({user.name} - {user.username}) not part of the Globus Groups applied for {user.idp_name}.",
                None,
            )

        # Grant request if user is part of at least one authorized Globus Groups
        else:
            group_overlap = ", ".join(list(group_overlap))
            return True, "", group_overlap

    # Grant request if no group restriction was found
    return True, "", None


# Extract service account client
def extract_service_account_client(introspection: str, client_groups: List[str]):
    """Extract and return the user object if identity is an authorized Globus client."""

    # Extract the client ID and full username
    client_id = introspection.get("client_id", "")
    username = introspection.get("username", "")
    domain = username.split("@")[1]
    name = introspection.get("name", "")
    iss = introspection.get("iss", "")

    # Skip client recognition if not enough details
    if len(client_id) == 0 or len(username) == 0 or len(domain) == 0 or len(name) == 0 or len(iss) == 0:
        return None

    # If this is an authorized Globus service account client ...
    if username in settings.AUTHORIZED_GLOBUS_SERVICE_USERNAMES:
        
        # Create and return the User object
        return UserPydantic(
            id=client_id,
            name=name,
            username=username,
            user_group_uuids=client_groups,
            idp_id=domain,
            idp_name=iss,
            auth_service=AuthService.GLOBUS.value,
        )
    
    # Return nothing if this is not an authorized Globus client
    else:
        return None

# Validate access token sent by user
def validate_access_token(request):
    """This function returns an instance of the ATVResponse pydantic data structure."""

    # Make sure the request is authenticated
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        error_message = "Error: Missing ('Authorization': 'Bearer <your-access-token>') in request headers."
        return ATVResponse(is_valid=False, error_message=error_message, error_code=400)

    # Make sure the bearer flag is mentioned
    try:
        ttype, bearer_token = auth_header.split()
        if ttype != "Bearer":
            return ATVResponse(
                is_valid=False,
                error_message="Error: Authorization type should be Bearer.",
                error_code=400,
            )
    except (AttributeError, ValueError):
        error_message = (
            "Error: Auth only allows header type Authorization: Bearer <token>."
        )
        return ATVResponse(is_valid=False, error_message=error_message, error_code=400)
    except Exception as e:
        error_message = f"Error: Something went wrong while reading headers. {e}"
        return ATVResponse(is_valid=False, error_message=error_message, error_code=400)

    # Introspect the access token
    introspection, user_groups, error_message = introspect_token(bearer_token)
    if len(error_message) > 0:
        return ATVResponse(
            is_valid=False,
            error_message=f"Error: Token introspection: {error_message}",
            error_code=401,
        )

    # Make sure the token is not expired
    expires_in = introspection["exp"] - time.time()
    if expires_in <= 0:
        return ATVResponse(
            is_valid=False, error_message="Error: Access token expired.", error_code=401
        )
    
    # Try to identify an authorized Globus service account client
    try:
        user = extract_service_account_client(introspection, user_groups)
    except Exception as e:
        log.warning(f"Globus introspection extract_service_account_client error: {e}")
        user = None

    # If the token is NOT from an authorized Globus client ...
    if user is None:

        # Make sure the authentication was made by an authorized identity provider
        successful, user, error_message = check_session_info(introspection, user_groups)
        if not successful:
            return ATVResponse(
                is_valid=False, error_message=error_message, error_code=403
            )

        # Make sure the authenticated user comes from an allowed domain
        # Those must be a high-assurance policies
        if settings.NUMBER_OF_GLOBUS_POLICIES > 0:
            successful, error_message = check_globus_policies(introspection)
            if not successful:
                return ATVResponse(
                    is_valid=False, error_message=error_message, error_code=403
                )

    # Make sure the user is part of a per-IdP authorized group (if any)
    successful, error_message, idp_group_overlap_str = check_groups_per_idp(
        user, user_groups
    )
    if not successful:
        return ATVResponse(is_valid=False, error_message=error_message, error_code=403)

    # Make sure the authenticated user is at least in one of the allowed Globus Groups
    if settings.NUMBER_OF_GLOBUS_GROUPS > 0:
        successful, error_message = check_globus_groups(user_groups)
        if not successful:
            return ATVResponse(
                is_valid=False, error_message=error_message, error_code=403
            )

    # Make sure the user's identity can be recorded
    if len(user.username) == 0:
        return ATVResponse(
            is_valid=False,
            error_message="Error: Username could not be recovered.",
            error_code=403,
        )
    
    # Make sure the user's identity is valid
    # TODO: Add more checks here
    if "<" in user.username or ">" in user.username:
        return ATVResponse(
            is_valid=False,
            error_message=f"Error: Username {user.username} includes non-authorized characters.",
            error_code=403,
        )

    # Return valid token response
    log.info(f"{user.name} requesting {introspection['scope']}")
    return ATVResponse(
        is_valid=True,
        user=user,
        user_group_uuids=user_groups,
        idp_group_overlap_str=idp_group_overlap_str,
    )


class CheckPermissionResponse(BaseModel):
    is_authorized: bool = False
    error_message: Optional[str] = Field(default=None)
    error_code: Optional[int] = Field(default=None)


# Check permission
def check_permission(
    auth: User,
    user_group_uuids: List[str],
    allowed_globus_groups: List[str],
    allowed_domains: List[str],
) -> CheckPermissionResponse:
    """Verify is the user is permitted to access or view a resource based on group and policy restrictions."""

    # Look at Globus Group permissions
    if allowed_globus_groups:
        if len(set(user_group_uuids) & set(allowed_globus_groups)) == 0:
            return CheckPermissionResponse(
                is_authorized=False,
                error_message=f"Error: Permission denied due to Globus Group restrictions.",
                error_code=401,
            )

    # Extract user's domain from the IdP used during authentication
    try:
        user_domain = auth.username.split("@")[1]
    except Exception:
        return CheckPermissionResponse(
            is_authorized=False,
            error_message=f"Error: Could not extract domain from user {auth.username}.",
            error_code=500,
        )

    # Look at domain (policy) permissions
    if allowed_domains:
        if user_domain not in allowed_domains:
            return CheckPermissionResponse(
                is_authorized=False,
                error_message=f"Error: Permission denied due to IdP domain restrictions.",
                error_code=401,
            )

    # Grant access if nothing wrong was detected
    return CheckPermissionResponse(is_authorized=True)
