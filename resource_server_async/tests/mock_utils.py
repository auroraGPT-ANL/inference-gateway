# Mock utils.py to overwrite functions to prevent contacting Globus services

import time
import uuid
from django.utils import timezone
from concurrent.futures import Future
from utils.pydantic_models.db_models import UserPydantic, AccessLogPydantic
from resource_server_async.models import AuthService
from django.http import StreamingHttpResponse


# Constants flags within mock access tokens
ACTIVE = "-ACTIVE"
EXPIRED = "-EXPIRED"
HAS_PREMIUM_ACCESS = "-HAS-PREMIUM-ACCESS"

# Constants related to API responses
MOCK_RESPONSE = "mock response"

# Globus Group UUID for premium access test
MOCK_GROUP = "MockGroup"
MOCK_GROUP_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MOCK_ALLOWED_GROUP = f"{MOCK_GROUP}:{MOCK_GROUP_UUID}"

# Get mock access token
def get_mock_access_token(active=True, expired=False, has_premium_access=False):

    # Base-line access token
    mock_token = "this-is-a-mock-access-token"

    # Add flags to alter token introspections
    if active:
        mock_token += ACTIVE
    if expired:
        mock_token += EXPIRED
    if has_premium_access:
        mock_token += HAS_PREMIUM_ACCESS

    # Return the mock access token
    return mock_token


# Get mock token introspection
def introspect_token(access_token):

    # Emulate an error in the introspection call
    if (ACTIVE not in access_token) or (EXPIRED in access_token):
        return None, [], "mock error message"

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

    # Determine if user should have access to endpoint polaris-vllm-this-is-for-test-suite
    if HAS_PREMIUM_ACCESS in access_token:
        user_groups = [MOCK_GROUP_UUID]
    else:
        user_groups = []

    # Return the mock token introspection and the Globus group details (here []])
    return introspection, user_groups, ""


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
        return {
            "status": "online",
            "details": {
                "managers": 1
            }
        }
    
        # Mock run (needs to be random distinct uuids to avoid UNIQUE database errors)
    def run(self, data, endpoint_id=None, function_id=None):
     return uuid.uuid4()
    
    # Mock task status
    def get_task(self, task_uuid):
        return {"pending": False}
    
    # Mock task result
    def get_result(self, task_uuid):
        return MOCK_RESPONSE
    
    # Mock create batch
    def create_batch(self):
        return MockBatch()
    
    # Mock batch run
    def batch_run(self, endpoint_id=None, batch=None):
        return {
            "request_id": str(uuid.uuid4()),
            "tasks": {
                "1": [str(uuid.uuid4()), str(uuid.uuid4())]
            }
        }
    

# Mock Globus batch object
class MockBatch():
    def add(self, function_id=None, args=None):
        pass


# Mock Globus SDK Executor
class MockExecutor():
    def submit_to_registered_function(self, function_uuid, args=None):
        return MockFuture()


# Mock asyncio wrap_future function
def wrap_future(future):
    return MockFuture()


# Mock asyncio wait_for function
async def wait_for(future, timeout=None):
    return MOCK_RESPONSE


# Mock Globus SDK Executor Future object
class MockFuture(Future):
    def __init__(self):
        super().__init__()
        self.task_id = str(uuid.uuid4())
    def result(self, timeout=None):
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


# Mock check_session_info function
# .env requirement --> AUTHORIZED_IDPS='{"mock_domain.com": "mock_idp_id"}'
def check_session_info(introspection, user_groups):
    user = UserPydantic(
        id="mock_id",
        name="mock_name",
        username="mock_username@mock_domain.com",
        user_group_uuids=user_groups,
        email="mock_email",
        idp_id="mock_idp_id",
        idp_name="mock_idp_name",
        auth_service=AuthService.GLOBUS.value
    )
    return True, user, ""

# Mock handle_streaming_inference function
async def handle_streaming_inference(gce, endpoint, data, resources_ready, request):
    return StreamingHttpResponse(
        streaming_content=[b'chunk1', b'chunk2', b'chunk3'],
        content_type='text/event-stream'
    )

# Mock __initialize_access_log_data function
def mock_initialize_access_log_data(self, request):
    return AccessLogPydantic(
        id=str(uuid.uuid4()),
        user=None,
        timestamp_request=timezone.now(),
        api_route="/mock/route",
        origin_ip="127.0.0.1",
    )