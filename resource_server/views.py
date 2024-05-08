from rest_framework.views import APIView
from rest_framework.response import Response
from django.conf import settings
import globus_sdk
import functools
import time
import requests
import json

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


# Check Globus Policies
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
def user_in_allowed_groups(user_email):
    """
        Define whether an authenticated user has the proper Globus memberships.
        User should be member of at least in one of the allowed Globus groups.
    """
    return False # In dev


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
            return Response({"Error": "User not Authorized. Access token expired"}, status=401)
        
        # Make sure the authenticated user comes from an allowed domain
        if settings.NUMBER_OF_GLOBUS_POLICIES > 0:
            successful, error_message = check_globus_policies(client, bearer_token)
            if not successful:
                return Response({"Error": error_message}, status=401)

        return f(self, request, *args, **kwargs)        
    return check_bearer_token


# Inference VLLM view
class VLLM(APIView):
    """API view to reach vLLM checkpoint before accessing the protected inference service."""
    
    # Post request call
    @globus_authenticated
    def post(self, request, *args, **kwargs):
        """Public point of entry to make post requests to interneal inference services."""
    
        # Extract the identity of the authenticated user
        name = kwargs["user"]["name"]
        username = kwargs["user"]["username"]

        # Validate and build the inference request data
        data = self.__validate_request_body(request)
        if len(data) == 0:
            return Response({"Error": "Request data invalid."}, status=400)

        # Send the post request
        if len(settings.INFERENCE_SERVICE_URL) > 0:
            headers = {"Content-Type": "application/json"}
            response = requests.post(
                settings.INFERENCE_SERVICE_URL,
                data=data
            )
            response_json = response.json()
        else:
            response_json = "No Post request sent."

        return Response({"server_response": f"{name} ({username}) should have access. {response_json}"})


    # Validate request body
    def __validate_request_body(self, request):
        """Build data dictionary for inference request if user inputs are valid."""

        # Decode request body into a dictionary
        body = json.loads(request.body.decode("utf-8"))

        # Make main fields
        for key in body.keys():
            if not key in ["model", "messages"]:
                return ""
        if not isinstance(body["model"], str) or not isinstance(body["messages"], list):
            return "" 

        # Check sub fields
        for dictionary in body["messages"]:
            if not isinstance(dictionary, dict):
                return ""
            for key in dictionary.keys():
                if not key in ["role", "content"]:
                    return ""
            if not isinstance(dictionary["role"], str) or not isinstance(dictionary["content"], str):
                return ""
        
        # Build request data if nothing wrong was caught
        data = {
            "model": body["model"],
            "messages": body["messages"]
        }
        return json.dumps(data)
