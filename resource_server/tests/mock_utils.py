# Mock utils.py to overwrite functions to prevent contacting Globus services

import time
import uuid

# Constants flags within mock access tokens
ACTIVE = "-ACTIVE"
EXPIRED = "-EXPIRED"
MOCK_RESPONSE = "mock response"

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


# Get mock token introspection
def introspect_token(access_token):

    # Emulate an error in the introspection call
    if (ACTIVE not in access_token) or (EXPIRED in access_token):
        return "mock error message", None

    # Base-line response for an active and valid token
    introspection = {
        "name": "mock_name",
        "username": "mock_username",
        "scope": "mock_scope",
        "active": ACTIVE in access_token,
        "exp": time.time() + 1000,
    }

    # Adjust the token expiration time
    if EXPIRED in access_token:
        introspection["exp"] -= 2000
        
    # Return the mock token introspection and the Globus group details (here None)
    return introspection, None


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
    def post(self, url, data=None, encoding=None):
        return introspect_token(data["token"])[0]
    
    # Mock endpoint status
    def get_endpoint_status(self, endpoint_uuid):
        return {"status": "online"}
    
        # Mock run (needs to be random distinct uuids to avoid UNIQUE database errors)
    def run(self, data, endpoint_id=None, function_id=None):
     return uuid.uuid4()
    
    # Mock task status
    def get_task(self, task_uuid):
        return {"pending": False}
    
    # Mock task result
    def get_result(self, task_uuid):
        return MOCK_RESPONSE
    

# Mock Globus SDK Executor
class MockExecutor():
    def submit_to_registered_function(self, function_uuid, args=None):
        return MockFuture()


# Mock Globus SDK Executor Future object
class MockFuture():
    def __init__(self):
        self.task_id = str(uuid.uuid4())
    def result(self):
        return MOCK_RESPONSE


# Mock get_globus_client function
def get_globus_client():
    return MockClient()


# Mock get_compute_client_from_globus_app function
def get_compute_client_from_globus_app():
    return MockClient()


# Mock get_compute_executor function
def get_compute_executor(endpoint_id=None, client=None, amqp_port=None):
    return MockExecutor()


# Mock check_globus_policies function
def check_globus_policies(introspection):
    return True, ""


# Mock check_globus_groups function
def check_globus_groups(my_groups):
    return True, ""