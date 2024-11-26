from dataclasses import dataclass, field
from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
import functools
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
@dataclass
class atv_response:
    is_valid: bool
    name: str = ""
    username: str = ""
    user_group_uuids: list = field(default_factory=lambda: [])
    error_message: str = ""
    error_code: int = 0
    

# Get Globus SDK confidential client
def get_globus_client():
    return globus_sdk.ConfidentialAppAuthClient(
        settings.GLOBUS_APPLICATION_ID, 
        settings.GLOBUS_APPLICATION_SECRET
    )


# Credits to Nick Saint and Ryan Chard to help me out here on caching
@cached(cache=TTLCache(maxsize=1024, ttl=60*10))
def introspect_token(bearer_token: str) -> globus_sdk.GlobusHTTPResponse:
    """
        Introspect a token with policies, collect group memberships, and return the response.
        Here we return error messages instead of raising exception/errors because we need
        to cache the outcome so that we don't contact Globus all the time if a token is
        invalid and requests keep coming in a loop.
    """

    # Create Globus SDK confidential client
    try:
        client = get_globus_client()
    except Exception as e:
        return f"Could not create Globus confidential client. {e}", []

    # Include the access token and Globus policies (if needed) in the instrospection
    introspect_body = {"token": bearer_token}
    if settings.NUMBER_OF_GLOBUS_POLICIES > 0:
        introspect_body["authentication_policies"] = settings.GLOBUS_POLICIES

    # Introspect the token through the Globus Auth API (including policy evaluation)
    try: 
        introspection = client.post("/v2/oauth2/token/introspect", data=introspect_body, encoding="form")
    except Exception as e:
        return f"Could not introspect token with Globus /v2/oauth2/token/introspect. {e}", []
    
    # Error if the token is invalid
    if introspection["active"] is False:
        return "Token is either not active or invalid", []
    
    # If Globus Group membership needs to be checked ...
    user_groups = []
    if settings.NUMBER_OF_GLOBUS_GROUPS > 0:

        # Get dependent access token to view group membership
        try:
            dependent_tokens = client.oauth2_get_dependent_tokens(bearer_token)
            access_token = dependent_tokens.by_resource_server["groups.api.globus.org"]["access_token"]
        except Exception as e:
            return f"Could not recover dependent access token for groups.api.globus.org. {e}", []

        # Create a Globus Group Client using the access token sent by the user
        authorizer = globus_sdk.AccessTokenAuthorizer(access_token)
        groups_client = globus_sdk.GroupsClient(authorizer=authorizer)

        # Get the user's group memberships
        try:
            user_groups = groups_client.get_my_groups()
        except Exception as e:
            return f"Could not recover group memberships. {e}", []
        
    # Return the introspection data along with the group 
    return introspection, user_groups


# Check Globus Policies
def check_globus_policies(introspection):
    """
        Define whether an authenticated user respect the Globus policies.
        User should meet all Globus policies requirements.
    """

    # Return False if policies cannot be evaluated went wrong
    if not len(introspection["policy_evaluations"]) == settings.NUMBER_OF_GLOBUS_POLICIES:
        return False, "Some Globus policies could not be passed to the introspect API call."

    # Return False if the user failed to meet one of the policies 
    for policies in introspection["policy_evaluations"].values():
        if policies.get("evaluation",False) == False:
            return False, "One of the Globus policies blocked the access to the service."

    # Return True if the user met all of the policies requirements
    return True, ""


# User In Allowed Groups
def check_globus_groups(user_groups):
    """
        Define whether an authenticated user has the proper Globus memberships.
        User should be member of at least in one of the allowed Globus groups.
    """

    # Collect the list of Globus Groups that the user is a member of
    try:
        user_groups = [group["id"] for group in user_groups]
    except:
        return False, "Introspection object does not have the 'get_my_groups' key."
    
    # Grant access if the user is a member of at least one of the allowed Globus Groups
    if len(set(user_groups).intersection(settings.GLOBUS_GROUPS)) > 0:
        return True, ""
    
    # Deny access if authenticated user is not part of any of the allowed Globus Groups
    else:
        return False, f"User is not a member of an allowed Globus Group."


# Validate access token sent by user
def validate_access_token(request):
    """This function returns a atv_response data structure."""

    # Make sure the request is authenticated
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        error_message = "Error: Missing ('Authorization': 'Bearer <your-access-token>') in request headers."
        return atv_response(is_valid=False, error_message=error_message, error_code=400)

    # Make sure the bearer flag is mentioned
    try:
        ttype, bearer_token = auth_header.split()
        if ttype != "Bearer":
            error_message = "Error: Only Authorization: Bearer <token> is allowed."
            return atv_response(is_valid=False, error_message=error_message, error_code=400)
    except (AttributeError, ValueError):
        error_message = "Error: Auth only allows header type Authorization: Bearer <token>."
        return atv_response(is_valid=False, error_message=error_message, error_code=400)

    # Introspect the access token
    introspection, user_groups = introspect_token(bearer_token)

    # If there an error (introspection not a globus object), return the error stored in the introspection variable
    if isinstance(introspection, str):
        error_message = f"Error: Token introspection: {introspection}"
        log.error(error_message)
        return atv_response(is_valid=False, error_message=error_message, error_code=401)

    # Make sure the token is not expired
    expires_in = introspection["exp"] - time.time()
    if expires_in <= 0:
        error_message = "Error: User not Authorized. Access token expired."
        return atv_response(is_valid=False, error_message=error_message, error_code=401)

    # Make sure the authenticated user comes from an allowed domain
    if settings.NUMBER_OF_GLOBUS_POLICIES > 0:
        successful, error_message = check_globus_policies(introspection)
        if not successful:
            return atv_response(is_valid=False, error_message=error_message, error_code=401)

    # Make sure the authenticated user is at least in one of the allowed Globus Groups
    if settings.NUMBER_OF_GLOBUS_GROUPS > 0:
        successful, error_message = check_globus_groups(user_groups)
        if not successful:
            return atv_response(is_valid=False, error_message=error_message, error_code=401)

    # Return valid token response
    log.info(f"{introspection['name']} requesting {introspection['scope']}")
    return atv_response(
        is_valid=True, 
        name=introspection["name"], 
        username=introspection["username"], 
        user_group_uuids=[group["id"] for group in user_groups]
    )


# Globus Authenticated (for decorator, which works with Django Rest, but not with Django Ninja)
def globus_authenticated(f):
    """
        Decorator that will validate request headers to make sure the user
        is authenticated and allowed to access the vLLM service API.
    """

    @functools.wraps(f)
    def check_bearer_token(self, request, *args, **kwargs):
        try:

            # Record the time close to when the HTTP request was received by the server
            kwargs["timestamp_receive"] = timezone.now()

            # Validate access token
            response = validate_access_token(request)
            if not response.is_valid:
                return Response(response.error_message, status=response.error_code)

            # Prepare user details to be passed to the Django view
            kwargs["user"] = {"name": response.name, "username": response.username}

            return f(self, request, *args, **kwargs) 
        except Exception as e:
            log.error({"Error: check_bearer_token": e})
            return Response({"Error: ": e}, status=500)

    return check_bearer_token
