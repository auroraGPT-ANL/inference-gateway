from django.conf import settings
from django.core.management import call_command
from resource_server_async.models import Cluster
import asyncio
import utils.auth_utils as auth_utils
import utils.globus_utils as globus_utils
import resource_server_async.tests.mock_utils as mock_utils
import httpx
from resource_server_async import api
from resource_server_async.endpoints import globus_compute, direct_api, metis
import json
import logging
import copy

# Tools to test with Django Ninja
from django.test import TestCase
from ninja.testing import TestAsyncClient
from resource_server_async.api import router

log = logging.getLogger(__name__)

# Overwrite log data initialization
api.GlobalAuth._GlobalAuth__initialize_access_log_data = (
    mock_utils.mock_initialize_access_log_data
)

# Overwrite Globus SDK classes and functions
auth_utils.get_globus_client = mock_utils.get_globus_client
globus_utils.get_compute_client_from_globus_app = (
    mock_utils.get_compute_client_from_globus_app
)
globus_utils.get_compute_executor = mock_utils.get_compute_executor
auth_utils.introspect_token = mock_utils.introspect_token

# Overwrite future
asyncio.wrap_future = mock_utils.wrap_future
asyncio.wait_for = mock_utils.wait_for

# Overwrite httpx client
httpx.AsyncClient = mock_utils.MockAsyncClient

# Overwrite streaming utilities
# Below does not work, you need to overwrite in the module that actually imports the StreamingHttpResponse
# django_http.StreamingHttpResponse = mock_utils.MockStreamingHttpResponse

# Overwrite StreamingHttpResponse in endpoint modules where it's actually imported
globus_compute.StreamingHttpResponse = mock_utils.MockStreamingHttpResponse
direct_api.StreamingHttpResponse = mock_utils.MockStreamingHttpResponse

# Overwrite metis fetch status call
# Need to overwrite in metis module where it's actually imported
metis.fetch_metis_status = mock_utils.mock_fetch_metis_status

# Overwrite settings variables
settings.MAX_BATCHES_PER_USER = 1000
settings.AUTHORIZED_IDP_DOMAINS = [mock_utils.MOCK_DOMAIN]
settings.NUMBER_OF_GLOBUS_POLICIES = 1
settings.GLOBUS_POLICIES = mock_utils.MOCK_POLICY_UUID


class ResourceServerTestCase(TestCase):
    # Data and client initialization
    @classmethod
    def setUp(self):
        """
        Initialization that will only happen once before running all tests.
        """

        # Fill Django test database
        call_command("loaddata", "fixtures/new_endpoints.json")
        call_command("loaddata", "fixtures/clusters.json")

        # Create mock access tokens
        self.active_token = mock_utils.get_mock_access_token(
            active=True, expired=False, has_premium_access=False
        )
        self.active_premium_token = mock_utils.get_mock_access_token(
            active=True, expired=False, has_premium_access=True
        )
        self.expired_token = mock_utils.get_mock_access_token(
            active=True, expired=True, has_premium_access=False
        )
        self.invalid_token = mock_utils.get_mock_access_token(
            active=False, expired=False, has_premium_access=False
        )

        # Create headers with a valid access token
        self.headers = mock_utils.get_mock_headers(
            access_token=self.active_token, bearer=True
        )
        self.premium_headers = mock_utils.get_mock_headers(
            access_token=self.active_premium_token, bearer=True
        )

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
        self.streaming_test_cases = copy.deepcopy(self.valid_params["chat/completions"])
        for i in range(len(self.streaming_test_cases)):
            self.streaming_test_cases[i]["stream"] = True

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

        # Collect available clusters from database
        db_clusters = Cluster.objects.all()
        self.ALLOWED_CLUSTERS = [c.cluster_name for c in db_clusters]

        # Collect available frameworks for each cluster
        self.ALLOWED_FRAMEWORKS = {}
        for cluster in db_clusters:
            self.ALLOWED_FRAMEWORKS[cluster.cluster_name] = cluster.frameworks

        # Collect available openAI endpoint for each cluster
        self.ALLOWED_OPENAI_ENDPOINTS = {}
        for cluster in db_clusters:
            self.ALLOWED_OPENAI_ENDPOINTS[cluster.cluster_name] = [
                e for e in cluster.openai_endpoints if e not in ["health", "metrics"]
            ]

    # Verify headers failures
    async def _verify_headers_failures(self, url=None, method=None):
        # Should fail (not authenticated, missing token)
        headers = mock_utils.get_mock_headers(access_token="")
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 400)

        # Should fail (not a bearer token)
        headers = mock_utils.get_mock_headers(
            access_token=self.active_token, bearer=False
        )
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 400)

        # Should fail (not a valid token)
        headers = mock_utils.get_mock_headers(
            access_token=self.invalid_token, bearer=True
        )
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 401)

        # Should fail (expired token)
        headers = mock_utils.get_mock_headers(
            access_token=self.expired_token, bearer=True
        )
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 401)

    # Convert bytes response to dictionary
    # This is because Django Ninja client does not take content-type json for some reason...
    def _get_response_json(self, response):
        # First check if this is a StreamingHttpResponse
        is_streaming = hasattr(response, "streaming_content")

        try:
            # Handle streaming responses
            if is_streaming:
                # For streaming responses, collect all chunks
                try:
                    streaming_content = response.streaming_content
                    if streaming_content is not None:
                        if hasattr(streaming_content, "__iter__"):
                            # If it's iterable, join the chunks
                            content = b"".join(streaming_content)
                        else:
                            # If it's not iterable, treat it as single content
                            content = streaming_content
                            if isinstance(content, str):
                                content = content.encode("utf-8")
                        return json.loads(content.decode("utf-8"))
                    else:
                        # streaming_content is None, return a default response
                        return "streaming response processed"
                except (TypeError, AttributeError, json.JSONDecodeError):
                    # If streaming parsing fails, return a generic response
                    return "streaming response processed"

            # Handle regular responses (non-streaming)
            if hasattr(response, "_container"):
                return json.loads(response._container[0].decode("utf-8"))
            elif hasattr(response, "content"):
                return json.loads(response.content.decode("utf-8"))
            else:
                return str(response)

        except json.JSONDecodeError:
            # If it's not JSON, return the raw content
            try:
                if is_streaming:
                    try:
                        streaming_content = response.streaming_content
                        if streaming_content is not None:
                            if hasattr(streaming_content, "__iter__"):
                                content = b"".join(streaming_content)
                            else:
                                content = streaming_content
                                if isinstance(content, str):
                                    content = content.encode("utf-8")
                            return content.decode("utf-8")
                        else:
                            return "streaming response"
                    except (TypeError, AttributeError):
                        return "streaming response"

                if hasattr(response, "_container"):
                    return response._container[0].decode("utf-8")
                elif hasattr(response, "content"):
                    return response.content.decode("utf-8")
                else:
                    return str(response)
            except:
                # Final fallback
                if is_streaming:
                    return "streaming response"
                return str(response)

    # Get endpoint URL
    def _get_endpoint_urls(self, endpoint):
        urls = {}
        for openai_endpoint in self.ALLOWED_OPENAI_ENDPOINTS[endpoint.cluster]:
            urls[openai_endpoint] = (
                f"/{endpoint.cluster}/{endpoint.framework}/v1/{openai_endpoint}/"
            )
        return urls

    # Get wrong endpoint URLs
    def _get_wrong_endpoint_urls(self):
        # Declare list of URLS with unsupported cluster, framework, and openai endpoint
        wrong_urls = []

        # Unsupported cluster
        cluster = "unsupported-cluster"
        framework = self.ALLOWED_FRAMEWORKS[self.ALLOWED_CLUSTERS[0]][0]
        endpoint = self.ALLOWED_OPENAI_ENDPOINTS[self.ALLOWED_CLUSTERS[0]][0]
        wrong_urls.append(
            f"/{cluster}/{framework}/v1/{endpoint}/",
        )

        # Unsupported framework
        cluster = self.ALLOWED_CLUSTERS[0]
        framework = "unsupported-framework"
        endpoint = self.ALLOWED_OPENAI_ENDPOINTS[self.ALLOWED_CLUSTERS[0]][0]
        wrong_urls.append(
            f"/{cluster}/{framework}/v1/{endpoint}/",
        )

        # Unsupported openai endpoint
        cluster = self.ALLOWED_CLUSTERS[0]
        framework = self.ALLOWED_FRAMEWORKS[self.ALLOWED_CLUSTERS[0]][0]
        endpoint = "unsupported-endpoint"
        wrong_urls.append(
            f"/{cluster}/{framework}/v1/{endpoint}/",
        )

        # Return list of unsupported URLs
        return wrong_urls

    # Get wrong batch URLs
    def _get_wrong_batch_urls(self):
        # Declare list of URLS with unsupported cluster and framework
        wrong_urls = []

        # Unsupported cluster
        cluster = "unsupported-cluster"
        framework = self.ALLOWED_FRAMEWORKS[self.ALLOWED_CLUSTERS[0]][0]
        wrong_urls.append(
            f"/{cluster}/{framework}/v1/batches",
        )

        # Unsupported framework
        cluster = self.ALLOWED_CLUSTERS[0]
        framework = "unsupported-framework"
        wrong_urls.append(
            f"/{cluster}/{framework}/v1/batches",
        )

        # Return list of unsupported URLs
        return wrong_urls
