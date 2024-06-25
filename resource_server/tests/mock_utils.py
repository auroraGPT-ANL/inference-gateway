# Mock utils.py to overwrite functions to prevent contacting Globus services

import time

# Constants flags within mock access tokens
ACTIVE = "-ACTIVE"
EXPIRED = "-EXPIRED"


# Get mock access token
def get_mock_access_token(active=True, expired=False):

    # Base-line access token
    mock_token = "this-is-a-mock-access-token"

    # Add flags to alter token introspections
    if active:
        mock_token += ACTIVE
    if expired:
        mock_token += EXPIRED

    # Return the mock access token
    return mock_token


# Get mock headers 
def get_mock_headers(access_token="", bearer=True):

    # Base-line headers
    headers = {"Content-Type": "application/json"}

    # Add authorization token if provided
    if len(access_token) > 0:
        if bearer:
            headers["Authorization"] = f"Bearer {access_token}"
        else:
            headers["Authorization"] = f"{access_token}"
    
    # Return the mock headers
    return headers


# Mock Globus SDK Client
class MockClient():

    # Mock token introspection
    def oauth2_token_introspect(self, bearer_token):

        # Base-line response for an active and valid token
        introspection = {
            "name": "mock_name",
            "username": "mock_username",
            "scope": "mock_scope",
            "active": ACTIVE in bearer_token,
            "exp": time.time() + 1000,
        }

        # Adjust the token expiration time
        if EXPIRED in bearer_token:
            introspection["exp"] -= 2000
        
        # Return the mock token  introspection
        return introspection


# Mock get_globus_client function
def get_globus_client():
    return MockClient()