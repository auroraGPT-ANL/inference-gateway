from django.conf import settings
from rest_framework.response import Response
import functools
import globus_sdk
import time

# Tool to log access requests
import logging
log = logging.getLogger(__name__)

def check_globus_policies(client, bearer_token):
    """
        Define whether an authenticated user respect the Globus policies.
        User should meet all Globus policies requirements.
    """

    # Build the body that will be sent to the introspect Globus API
    introspect_body = {
        "token": bearer_token,
        "authentication_policies": settings.GLOBUS_POLICIES
    }

    # Post call to the introspect API
    try: 
        response = client.post("/v2/oauth2/token/introspect", data=introspect_body, encoding="form")
    except:
        return False, "Something went wrong in the Globus introspect API call."

    # Return False if policies cannot be evaluated went wrong
    if not len(response["policy_evaluations"]) == settings.NUMBER_OF_GLOBUS_POLICIES:
        return False, "Some Globus policies could not be passed to the introspect API call."

    # Return False if the user failed to meet one of the policies 
    for policies in response["policy_evaluations"].values():
        if policies.get("evaluation",False) == False:
            return False, "One of the Globus policies blocked the access to the service."

    # Return True if the user met all of the policies requirements
    return True, ""


# User In Allowed Groups
def check_globus_groups(username):
    """
        Define whether an authenticated user has the proper Globus memberships.
        User should be member of at least in one of the allowed Globus groups.
    """

    # Error if no Globus Group manager was provided
    if settings.GLOBUS_GROUP_MANAGER_ID == "" or settings.GLOBUS_GROUP_MANAGER_SECRET == "":
        False, "No GLOBUS_GROUP_MANAGER credentials available to validate Globus Group memberships."

    # Create the Globus client with the Group manager credentials
    confidential_client = globus_sdk.ConfidentialAppAuthClient(
        settings.GLOBUS_GROUP_MANAGER_ID, settings.GLOBUS_GROUP_MANAGER_SECRET
    )

    # Get access token to manage groups
    token_response = confidential_client.oauth2_client_credentials_tokens(
        requested_scopes=[
            globus_sdk.scopes.GroupsScopes.all
        ]
    )
    access_token = token_response.by_resource_server["groups.api.globus.org"]['access_token']

    # Group client using access token from confidential client 
    groups_client = globus_sdk.GroupsClient(authorizer=globus_sdk.AccessTokenAuthorizer(access_token))

    # For each Globus Group ...
    for group_uuid in settings.GLOBUS_GROUPS:

        # Extract list of active member usernames
        active_usernames = []
        response = groups_client.get_group(group_uuid, include="memberships")
        for member in response.data["memberships"]:
            if member["status"] == "active":
                active_usernames.append(member["username"])

        # Grant access if authenticated user is in one of the allowed Globus Group
        if username in active_usernames:
            return True, ""

    # Deny access if authenticated user is not part of any of the allowed Globus Groups
    return False, f"{username} is not a member of an allowed Globus Group."


# Globus Authenticated
def globus_authenticated(f):
    """
        Decorator that will validate request headers to make sure the user
        is authenticated and allowed to access the vLLM service API.
    """

    @functools.wraps(f)
    def check_bearer_token(self, request, *args, **kwargs):
        try:
            # Create vLLM service client
            client =  globus_sdk.ConfidentialAppAuthClient(
                settings.GLOBUS_APPLICATION_ID, 
                settings.GLOBUS_APPLICATION_SECRET
            )

            # Make sure the request is authenticated
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                return Response(
                    {"Error": "Missing ('Authorization': 'Bearer <your-access-token>') in request headers."},status=400
                )
            
            # Make sure the bearer flag is mentioned
            try:
                ttype, bearer_token = auth_header.split()
                if ttype != "Bearer":
                    return Response({"Error": "Only Authorization: Bearer <token> is allowed."}, status=400)
            except (AttributeError, ValueError):
                return Response({"Error": "Auth only allows header type Authorization: Bearer <token>"}, status=400)
            
            # Introspect the access token
            introspection = client.oauth2_token_introspect(bearer_token)

            # Make sure the access token is active and filled with user information
            if introspection["active"] is False:
                return Response({"Error": "Token is either not active or invalid"}, status=401)

            # Prepare user details to be passed to the Django view
            kwargs["user"] = {
                "name": introspection["name"],
                "username": introspection["username"]
            }

            # Log access request
            log.info(f"{introspection['name']} requesting {introspection['scope']}")

            # Make sure the token is not expired
            expires_in = introspection["exp"] - time.time()
            if expires_in <= 0:
                return Response({"Error": "User not Authorized. Access token expired"}, status=401)
            
            # Make sure the authenticated user comes from an allowed domain
            if settings.NUMBER_OF_GLOBUS_POLICIES > 0:
                successful, error_message = check_globus_policies(client, bearer_token)
                if not successful:
                    return Response({"Error": error_message}, status=401)
                
            # Make sure the authenticated user is at least in one of the allowed Globus Groups
            if settings.NUMBER_OF_GLOBUS_GROUPS > 0:
                successful, error_message = check_globus_groups(introspection["username"])
                if not successful:
                    return Response({"Error": error_message}, status=401)

            return f(self, request, *args, **kwargs) 
        except Exception as e:
            return Response({"Error Here": str(e)}, status=500)

    return check_bearer_token
