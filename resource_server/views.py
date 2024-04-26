from rest_framework.views import APIView
from rest_framework.response import Response
from django.conf import settings
import globus_sdk
import functools
import time

# Tool to log access requests
import logging
log = logging.getLogger(__name__)


# Get App Client
def get_app_client():
    """Create a Globus confidential client using the vLLM Client API credentials."""
    return globus_sdk.ConfidentialAppAuthClient(
        settings.SOCIAL_AUTH_GLOBUS_KEY, 
        settings.SOCIAL_AUTH_GLOBUS_SECRET
    )


# Globus Authenticated
def globus_authenticated(f):
    """
        Decorator that will validate request headers to make sure the user
        is authenticated and allowed to access the vLLM service API.
    """
    @functools.wraps(f)
    def check_bearer_token(self, request, *args, **kwargs):

        # Create vLLM service client
        client = get_app_client()

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
        log.debug(f"{introspection['name']} requesting {introspection['scope']}")

        # Make sure the token is not expired
        expires_in = introspection["exp"] - time.time()
        if expires_in <= 0:
            return Response({"Error": "Not Authorized. Access token expired"}, status=401)
        
        # TODO: Make sure we restrict access here whenever needed.

        return f(self, request, *args, **kwargs)        
    return check_bearer_token


# Inference VLLM view
class VLLM(APIView):
    """API view to reach vLLM checkpoint before accessing the protected inference service."""
    
    # Post request call
    @globus_authenticated
    def post(self, request, *args, **kwargs):
        name = kwargs["user"]["name"]
        username = kwargs["user"]["username"]
        return Response({"server_response": f"Hello {name} ({username})! You provided an authenticated post request."})
