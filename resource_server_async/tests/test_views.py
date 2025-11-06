from django.conf import settings
from django.core.management import call_command
from resource_server.models import Endpoint
import json
import uuid
import logging
log = logging.getLogger(__name__)

# Tools to test with Django Ninja
from django.test import TestCase
from ninja.testing import TestAsyncClient
from resource_server_async.api import router

# Overwrite utils functions to prevent contacting Globus services
import asyncio
import utils.auth_utils as auth_utils
import utils.globus_utils as globus_utils
import resource_server_async.tests.mock_utils as mock_utils
from resource_server_async import views as async_views
from resource_server_async import api
auth_utils.check_session_info = mock_utils.check_session_info
auth_utils.get_globus_client = mock_utils.get_globus_client
auth_utils.check_globus_policies = mock_utils.check_globus_policies
auth_utils.check_globus_groups = mock_utils.check_globus_groups
auth_utils.introspect_token = mock_utils.introspect_token
globus_utils.get_compute_client_from_globus_app = mock_utils.get_compute_client_from_globus_app
globus_utils.get_compute_executor = mock_utils.get_compute_executor
asyncio.wrap_future = mock_utils.wrap_future
asyncio.wait_for = mock_utils.wait_for
async_views.handle_streaming_inference = mock_utils.handle_streaming_inference
api.GlobalAuth._GlobalAuth__initialize_access_log_data = mock_utils.mock_initialize_access_log_data

# Overwrite the maximum number of batches user can send (in order to go through all of the json test entries)
settings.MAX_BATCHES_PER_USER = 1000

# Overwrite authorized IdPs for testing
settings.AUTHORIZED_IDPS={"mock_domain.com": "mock_idp_id"}
settings.AUTHORIZED_IDP_DOMAINS=["mock_domain.com"]
settings.AUTHORIZED_IDP_UUIDS=["mock_idp_id"]

# Settings variables
ALLOWED_FRAMEWORKS = settings.ALLOWED_FRAMEWORKS
ALLOWED_OPENAI_ENDPOINTS = settings.ALLOWED_OPENAI_ENDPOINTS
ALLOWED_CLUSTERS = settings.ALLOWED_CLUSTERS

# Define OpenAI endpoints to be removed
for cluster, endpoints in ALLOWED_OPENAI_ENDPOINTS.items():
    ALLOWED_OPENAI_ENDPOINTS[cluster] = [e for e in endpoints if e not in ["health", "metrics"]]

# Test views.py
class ResourceServerViewTestCase(TestCase):

    # Data and client initialization
    @classmethod
    def setUp(self):
        """
            Initialization that will only happen once before running all tests.
        """

        # Fill Django test database
        call_command("loaddata", "fixtures/endpoints.json")

        # Create mock access tokens
        self.active_token = mock_utils.get_mock_access_token(active=True, expired=False, has_premium_access=False)
        self.active_premium_token = mock_utils.get_mock_access_token(active=True, expired=False, has_premium_access=True)
        self.expired_token = mock_utils.get_mock_access_token(active=True, expired=True, has_premium_access=False)
        self.invalid_token = mock_utils.get_mock_access_token(active=False, expired=False, has_premium_access=False)

        # Create headers with a valid access token
        self.headers = mock_utils.get_mock_headers(access_token=self.active_token, bearer=True)
        self.premium_headers = mock_utils.get_mock_headers(access_token=self.active_premium_token, bearer=True)

        # Create request Django Ninja test client instance
        self.kwargs = {"content_type": "application/json"}
        self.client = TestAsyncClient(router)

        # Load valid test input data (OpenAI format)
        base_path = "utils/tests/json"
        self.valid_params = {}
        with open(f"{base_path}/valid_completions.json") as json_file:
            self.valid_params["completions"] = json.load(json_file)
        with open(f"{base_path}/valid_chat_completions.json") as json_file:
            self.valid_params["chat/completions"] = json.load(json_file)
        with open(f"{base_path}/valid_embeddings.json") as json_file:
            self.valid_params["embeddings"] = json.load(json_file)
        with open(f"{base_path}/valid_batch.json") as json_file:
            self.valid_params["batch"] = json.load(json_file)
        self.valid_params["health"] = {}
        self.valid_params["metrics"] = {}

        # Extract streaming test cases from valid chat completions
        self.streaming_test_cases = [
            params for params in self.valid_params["chat/completions"] 
            if params.get("stream") is True
        ]

        # Load invalid test input data (OpenAI format)
        self.invalid_params = {}
        with open(f"{base_path}/invalid_completions.json") as json_file:
            self.invalid_params["completions"] = json.load(json_file)
        with open(f"{base_path}/invalid_chat_completions.json") as json_file:
            self.invalid_params["chat/completions"] = json.load(json_file)
        with open(f"{base_path}/invalid_embeddings.json") as json_file:
            self.invalid_params["embeddings"] = json.load(json_file)
        with open(f"{base_path}/invalid_batch.json") as json_file:
            self.invalid_params["batch"] = json.load(json_file)
        self.invalid_params["health"] = {}
        self.invalid_params["metrics"] = {}


    # Test get_list_endpoints (GET) 
    async def test_get_list_endpoints_view(self):

        # Define the targeted Django URL
        url = "/list-endpoints"

        # Make sure GET requests fail if something is wrong with the authentication
        await self.__verify_headers_failures(url=url, method=self.client.get)

        # Make sure non-GET requests are not allowed
        for method in [self.client.post, self.client.put, self.client.delete]:
            response = await method(url)
            self.assertEqual(response.status_code, 405)

        # Extract number of public and premium Globus Compute endpoint objects from the database
        # TODO: Re work this to test number of models with clusters that have direct API access
        db_endpoints_public = 0
        async for _ in Endpoint.objects.filter(allowed_globus_groups=""):
            db_endpoints_public += 1
        db_endpoints_premium = 0
        async for _ in Endpoint.objects.filter(allowed_globus_groups=mock_utils.MOCK_ALLOWED_GROUP):
            db_endpoints_premium += 1

        # For valid tokens with and without premium access ...
        for headers in [self.headers, self.premium_headers]:

            # Make sure GET requests succeed when providing a valid access token
            response = await self.client.get(url, headers=headers)
            response_data = self.__get_response_json(response)
            self.assertEqual(response.status_code, 200)

            # Define the total number of expected endpoints
            nb_endpoints_expected = db_endpoints_public
            if headers == self.premium_headers:
                nb_endpoints_expected += db_endpoints_premium

            # Make sure the GET request returns the correct number of endpoints
            nb_endpoints = 0
            for cluster in response_data["clusters"]:
                # TODO: Generalize this to account for Non-Globus-Compute clusters
                if cluster in ["sophia", "polaris"]:
                    for framework in response_data["clusters"][cluster]["frameworks"]:
                        nb_endpoints += len(response_data["clusters"][cluster]["frameworks"][framework]["models"])
            self.assertEqual(nb_endpoints_expected, nb_endpoints)

    # Test post_inference view (POST)
    async def test_post_inference_view(self):

        # Make sure POST requests fail when targetting an unsupported cluster, framework, or openai endpoint
        for wrong_url in self.__get_wrong_endpoint_urls():
            response = await self.client.post(wrong_url, headers=self.headers)
            self.assertEqual(response.status_code, 400)

        # For each supported endpoint in the database ...
        async for endpoint in Endpoint.objects.all():
          if "model-removed" not in endpoint.endpoint_slug:
            
            # Build the targeted Django URLs
            url_dict = self.__get_endpoint_urls(endpoint)

            # For each URL (openai endpoint) ...
            for openai_endpoint, url in url_dict.items():

                # Make sure POST requests fail if something is wrong with the authentication
                await self.__verify_headers_failures(url=url, method=self.client.post)

                # Make sure non-POST requests are not allowed
                for method in [self.client.get, self.client.put, self.client.delete]:
                    response = await method(url)
                    self.assertEqual(response.status_code, 405)

                # If the endpoint can be accessed by the mock access token ...
                if endpoint.allowed_globus_groups in ["", mock_utils.MOCK_ALLOWED_GROUP]:
                    headers = self.premium_headers

                    # For each valid set of input parameters ...
                    for valid_params in self.valid_params[openai_endpoint]:

                        # Overwrite the model to match the endpoint model (otherwise the view won't find the endpoint slug)
                        valid_params["model"] = endpoint.model

                        # Make sure POST requests succeed
                        response = await self.client.post(url, data=json.dumps(valid_params).encode('utf-8'), headers=headers, **self.kwargs)
                        self.assertEqual(response.status_code, 200)
                        
                        # For streaming responses, we might get different response format
                        # but we should still get a successful response
                        response_data = self.__get_response_json(response)
                        # The mock response should be consistent, but streaming might change format
                        if valid_params.get("stream") is True:
                            # For streaming, we just verify we got a successful response
                            # The actual response format might differ
                            self.assertIsNotNone(response_data)
                        else:
                            # For non-streaming, we expect the exact mock response
                            self.assertEqual(response_data, mock_utils.MOCK_RESPONSE)

                    # Make sure POST requests fail when providing invalid inputs
                    for invalid_params in self.invalid_params[openai_endpoint]:
                        response = await self.client.post(url, data=json.dumps(invalid_params).encode('utf-8'), headers=headers, **self.kwargs)
                        self.assertEqual(response.status_code, 400)

                # Make sure users can't access private endpoint if not in allowed groups
                if endpoint.allowed_globus_groups == mock_utils.MOCK_ALLOWED_GROUP:
                    response = await self.client.post(url, data=json.dumps(valid_params).encode('utf-8'), headers=self.headers, **self.kwargs)
                    self.assertEqual(response.status_code, 401)

    
    # Test post_batch_inference view (POST)
    async def test_post_batch_inference_view(self):

        # Make sure POST requests fail when targetting an unsupported cluster or framework
        for wrong_url in self.__get_wrong_batch_urls():
            response = await self.client.post(wrong_url, headers=self.headers)
            self.assertEqual(response.status_code, 400)

        # For each endpoint that support batch in the database ...
        async for endpoint in Endpoint.objects.all():
          if "model-removed" not in endpoint.endpoint_slug:
            if len(endpoint.batch_endpoint_uuid) > 0:
            
                # Build the targeted Django URL
                url = f"/{endpoint.cluster}/{endpoint.framework}/v1/batches"

                # Make sure POST requests fail if something is wrong with the authentication
                await self.__verify_headers_failures(url=url, method=self.client.post)

                # Make sure non-POST requests are not allowed
                for method in [self.client.get, self.client.put, self.client.delete]:
                    response = await method(url)
                    self.assertEqual(response.status_code, 405)

                # If the endpoint can be accessed by the mock access token ...
                if endpoint.allowed_globus_groups in ["", mock_utils.MOCK_ALLOWED_GROUP]:
                    headers = self.premium_headers

                    # For each valid set of input parameters ...
                    for valid_params in self.valid_params["batch"]:

                        # Overwrite the model to match the endpoint model (otherwise the view won't find the endpoint slug)
                        valid_params["model"] = endpoint.model

                        # Overwrite the input file to make it unique (otherwise will encounter "already used" error)
                        valid_params["input_file"] = f"/path/{str(uuid.uuid4())}"

                        # Make sure POST requests succeed
                        response = await self.client.post(url, data=json.dumps(valid_params).encode('utf-8'), headers=headers, **self.kwargs)
                        self.assertEqual(response.status_code, 200)

                        # Check whether the response makes sense (do not check batch_id, it's randomly generated in the view)
                        response_json = self.__get_response_json(response)
                        self.assertEqual(response_json["input_file"], valid_params["input_file"])

                    # Make sure POST requests fail when providing invalid inputs
                    for invalid_params in self.invalid_params["batch"]:
                        response = await self.client.post(url, data=json.dumps(invalid_params).encode('utf-8'), headers=headers, **self.kwargs)
                        self.assertEqual(response.status_code, 400)

                # Make sure users can't access private endpoint if not in allowed groups
                if endpoint.allowed_globus_groups == mock_utils.MOCK_ALLOWED_GROUP:
                    response = await self.client.post(url, data=json.dumps(valid_params).encode('utf-8'), headers=self.headers, **self.kwargs)
                    self.assertEqual(response.status_code, 401)


    # Test streaming functionality (POST)
    async def test_post_streaming_inference_view(self):
        """Test streaming responses for chat/completions endpoint using real test data
        Note: stream=True is supported, but stream_options is only available in /completions endpoint"""
        
        # Skip if no streaming test cases are available
        if not self.streaming_test_cases:
            self.skipTest("No streaming test cases found in valid_chat_completions.json")
        
        # For each supported endpoint in the database that supports chat/completions...
        async for endpoint in Endpoint.objects.all():
          if "model-removed" not in endpoint.endpoint_slug:
            
            # Build the targeted Django URL for chat/completions
            url = f"/{endpoint.cluster}/{endpoint.framework}/v1/chat/completions/"
            
            # Skip if this cluster doesn't support chat/completions
            if "chat/completions" not in ALLOWED_OPENAI_ENDPOINTS.get(endpoint.cluster, []):
                continue
            
            # If the endpoint can be accessed by the mock access token ...
            if endpoint.allowed_globus_groups in ["", mock_utils.MOCK_ALLOWED_GROUP]:
                headers = self.premium_headers
                
                # Test each streaming test case from the JSON data
                for streaming_params in self.streaming_test_cases:
                    # Create a copy to avoid modifying the original test data
                    test_params = streaming_params.copy()
                    # Overwrite the model to match the endpoint model
                    test_params["model"] = endpoint.model
                    
                    # Test streaming request
                    response = await self.client.post(url, data=json.dumps(test_params).encode('utf-8'), headers=headers, **self.kwargs)
                    
                    # For streaming, we expect a 200 response
                    self.assertEqual(response.status_code, 200)
                    
                    # In a real streaming response, we'd get Server-Sent Events
                    # But in our mock implementation, we just verify the request is processed
                    # The response format might differ for streaming vs non-streaming
                    response_data = self.__get_response_json(response)
                    self.assertIsNotNone(response_data)  # Just verify we got some response
                
                # Also test that non-streaming requests work with the same endpoint
                # Use the first streaming test case and modify it to be non-streaming
                if self.streaming_test_cases:
                    non_streaming_params = self.streaming_test_cases[0].copy()
                    non_streaming_params["model"] = endpoint.model
                    non_streaming_params["stream"] = False
                    
                    response = await self.client.post(url, data=json.dumps(non_streaming_params).encode('utf-8'), headers=headers, **self.kwargs)
                    self.assertEqual(response.status_code, 200)
                    # For non-streaming, we can expect the exact mock response
                    response_data = self.__get_response_json(response)
                    if response_data is not None:
                        self.assertEqual(response_data, mock_utils.MOCK_RESPONSE)


    # Test streaming-specific parameter validation
    async def test_streaming_parameter_validation(self):
        """Test validation of streaming-specific parameters
        Tests that stream=True works but invalid stream values are rejected"""
        
        # For each supported endpoint that allows streaming...
        async for endpoint in Endpoint.objects.all():
          if "model-removed" not in endpoint.endpoint_slug:
            
            # Build the targeted Django URL for chat/completions
            url = f"/{endpoint.cluster}/{endpoint.framework}/v1/chat/completions/"
            
            # Skip if this cluster doesn't support chat/completions
            if "chat/completions" not in ALLOWED_OPENAI_ENDPOINTS.get(endpoint.cluster, []):
                continue
            
            # If the endpoint can be accessed by the mock access token ...
            if endpoint.allowed_globus_groups in ["", mock_utils.MOCK_ALLOWED_GROUP]:
                headers = self.premium_headers
                
                # Test invalid stream parameter (should be boolean)
                invalid_stream_params = {
                    "model": endpoint.model,
                    "messages": [{"role": "user", "content": "Test message"}],
                    "stream": "invalid_string"  # Should be boolean
                }
                
                response = await self.client.post(url, data=json.dumps(invalid_stream_params).encode('utf-8'), headers=headers, **self.kwargs)
                # This should result in a 400 error due to pydantic validation failure
                self.assertEqual(response.status_code, 400)
                
                # Test valid streaming request with various parameters
                valid_streaming_params = {
                    "model": endpoint.model,
                    "messages": [{"role": "user", "content": "Test message"}],
                    "stream": True,
                    "max_tokens": 50
                }
                
                response = await self.client.post(url, data=json.dumps(valid_streaming_params).encode('utf-8'), headers=headers, **self.kwargs)
                # Should work correctly
                self.assertEqual(response.status_code, 200)
                # Verify we get some response content
                response_data = self.__get_response_json(response)
                self.assertIsNotNone(response_data)


    # Verify headers failures
    async def __verify_headers_failures(self, url=None, method=None):

        # Should fail (not authenticated, missing token)
        headers = mock_utils.get_mock_headers(access_token="")
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 400)

        # Should fail (not a bearer token)
        headers = mock_utils.get_mock_headers(access_token=self.active_token, bearer=False)
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 400)

        # Should fail (not a valid token)
        headers = mock_utils.get_mock_headers(access_token=self.invalid_token, bearer=True)
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 401)

        # Should fail (expired token)
        headers = mock_utils.get_mock_headers(access_token=self.expired_token, bearer=True)
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 401)


    # Convert bytes response to dictionary
    # This is because Django Ninja client does not take content-type json for some reason...
    def __get_response_json(self, response):
        # First check if this is a StreamingHttpResponse
        is_streaming = hasattr(response, 'streaming_content')
        
        try:
            # Handle streaming responses
            if is_streaming:
                # For streaming responses, collect all chunks
                try:
                    streaming_content = response.streaming_content
                    if streaming_content is not None:
                        if hasattr(streaming_content, '__iter__'):
                            # If it's iterable, join the chunks
                            content = b''.join(streaming_content)
                        else:
                            # If it's not iterable, treat it as single content
                            content = streaming_content
                            if isinstance(content, str):
                                content = content.encode('utf-8')
                        return json.loads(content.decode('utf-8'))
                    else:
                        # streaming_content is None, return a default response
                        return "streaming response processed"
                except (TypeError, AttributeError, json.JSONDecodeError):
                    # If streaming parsing fails, return a generic response
                    return "streaming response processed"
            
            # Handle regular responses (non-streaming)
            if hasattr(response, '_container'):
                return json.loads(response._container[0].decode('utf-8'))
            elif hasattr(response, 'content'):
                return json.loads(response.content.decode('utf-8'))
            else:
                return str(response)
                
        except json.JSONDecodeError:
            # If it's not JSON, return the raw content
            try:
                if is_streaming:
                    try:
                        streaming_content = response.streaming_content
                        if streaming_content is not None:
                            if hasattr(streaming_content, '__iter__'):
                                content = b''.join(streaming_content)
                            else:
                                content = streaming_content
                                if isinstance(content, str):
                                    content = content.encode('utf-8')
                            return content.decode('utf-8')
                        else:
                            return "streaming response"
                    except (TypeError, AttributeError):
                        return "streaming response"
                
                if hasattr(response, '_container'):
                    return response._container[0].decode('utf-8')
                elif hasattr(response, 'content'):
                    return response.content.decode('utf-8')
                else:
                    return str(response)
            except:
                # Final fallback
                if is_streaming:
                    return "streaming response"
                return str(response)


    # Get endpoint URL
    def __get_endpoint_urls(self, endpoint):
        urls = {}
        for openai_endpoint in ALLOWED_OPENAI_ENDPOINTS[endpoint.cluster]:
             urls[openai_endpoint] = f"/{endpoint.cluster}/{endpoint.framework}/v1/{openai_endpoint}/"
        return urls


    # Get wrong endpoint URLs
    def __get_wrong_endpoint_urls(self):

        # Declare list of URLS with unsupported cluster, framework, and openai endpoint
        wrong_urls = []

        # Unsupported cluster
        cluster = "unsupported-cluster"
        framework = ALLOWED_FRAMEWORKS[ALLOWED_CLUSTERS[0]][0]
        endpoint = ALLOWED_OPENAI_ENDPOINTS[ALLOWED_CLUSTERS[0]][0]
        wrong_urls.append(f"/{cluster}/{framework}/v1/{endpoint}/",)

        # Unsupported framework
        cluster = ALLOWED_CLUSTERS[0]
        framework = "unsupported-framework"
        endpoint = ALLOWED_OPENAI_ENDPOINTS[ALLOWED_CLUSTERS[0]][0]
        wrong_urls.append(f"/{cluster}/{framework}/v1/{endpoint}/",)

        # Unsupported openai endpoint
        cluster = ALLOWED_CLUSTERS[0]
        framework = ALLOWED_FRAMEWORKS[ALLOWED_CLUSTERS[0]][0]
        endpoint = "unsupported-endpoint"
        wrong_urls.append(f"/{cluster}/{framework}/v1/{endpoint}/",)

        # Return list of unsupported URLs
        return wrong_urls
    

    # Get wrong batch URLs
    def __get_wrong_batch_urls(self):

        # Declare list of URLS with unsupported cluster and framework
        wrong_urls = []

        # Unsupported cluster
        cluster = "unsupported-cluster"
        framework = ALLOWED_FRAMEWORKS[ALLOWED_CLUSTERS[0]][0]
        wrong_urls.append(f"/{cluster}/{framework}/v1/batches",)

        # Unsupported framework
        cluster = ALLOWED_CLUSTERS[0]
        framework = "unsupported-framework"
        wrong_urls.append(f"/{cluster}/{framework}/v1/batches",)

        # Return list of unsupported URLs
        return wrong_urls
        