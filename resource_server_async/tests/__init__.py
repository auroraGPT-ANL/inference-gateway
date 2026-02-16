from inspect import iscoroutinefunction
import re
from typing import override
from django.conf import settings
from django.core.management import call_command
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


# Create mock access tokens
ACTIVE_TOKEN = mock_utils.get_mock_access_token(
    active=True, expired=False, has_premium_access=False
)
ACTIVE_PREMIUM_TOKEN = mock_utils.get_mock_access_token(
    active=True, expired=False, has_premium_access=True
)
EXPIRED_TOKEN = mock_utils.get_mock_access_token(
    active=True, expired=True, has_premium_access=False
)
INVALID_TOKEN = mock_utils.get_mock_access_token(
    active=False, expired=False, has_premium_access=False
)

# Create headers with a valid access token
HEADERS = mock_utils.get_mock_headers(access_token=ACTIVE_TOKEN, bearer=True)
PREMIUM_HEADERS = mock_utils.get_mock_headers(
    access_token=ACTIVE_PREMIUM_TOKEN, bearer=True
)

# Create request Django Ninja test client instance
KWARGS = {"content_type": "application/json"}
CLIENT = TestAsyncClient(router)

# Load valid test input data (OpenAI format)
base_path = "utils/tests/json"
VALID_PARAMS = {}
with open(f"{base_path}/valid_completions.json") as json_file:
    VALID_PARAMS["completions"] = json.load(json_file)
with open(f"{base_path}/valid_chat_completions.json") as json_file:
    VALID_PARAMS["chat/completions"] = json.load(json_file)
with open(f"{base_path}/valid_embeddings.json") as json_file:
    VALID_PARAMS["embeddings"] = json.load(json_file)
with open(f"{base_path}/valid_batch.json") as json_file:
    VALID_PARAMS["batch"] = json.load(json_file)
VALID_PARAMS["health"] = {}
VALID_PARAMS["metrics"] = {}

# Extract streaming test cases from valid chat completions
STREAMING_TEST_CASES = copy.deepcopy(VALID_PARAMS["chat/completions"])
for i in range(len(STREAMING_TEST_CASES)):
    STREAMING_TEST_CASES[i]["stream"] = True

# Load invalid test input data (OpenAI format)
INVALID_PARAMS = {}
with open(f"{base_path}/invalid_completions.json") as json_file:
    INVALID_PARAMS["completions"] = json.load(json_file)
with open(f"{base_path}/invalid_chat_completions.json") as json_file:
    INVALID_PARAMS["chat/completions"] = json.load(json_file)
with open(f"{base_path}/invalid_embeddings.json") as json_file:
    INVALID_PARAMS["embeddings"] = json.load(json_file)
with open(f"{base_path}/invalid_batch.json") as json_file:
    INVALID_PARAMS["batch"] = json.load(json_file)
INVALID_PARAMS["health"] = {}
INVALID_PARAMS["metrics"] = {}

# Collect available clusters and endpoints from database
with open("fixtures/new_endpoints.json") as json_file:
    DB_ENDPOINTS = [e["fields"] for e in json.load(json_file)]
with open("fixtures/clusters.json") as json_file:
    DB_CLUSTERS = [c["fields"] for c in json.load(json_file)]

# Collect available information for each cluster
ALLOWED_CLUSTERS = []
ALLOWED_FRAMEWORKS = {}
ALLOWED_OPENAI_ENDPOINTS = {}
for cluster in DB_CLUSTERS:
    cluster_name = cluster["cluster_name"]

    ALLOWED_CLUSTERS.append(cluster_name)
    ALLOWED_FRAMEWORKS[cluster_name] = cluster["frameworks"]
    ALLOWED_OPENAI_ENDPOINTS[cluster_name] = [
        e for e in cluster["openai_endpoints"] if e not in ["health", "metrics"]
    ]

del base_path


def get_endpoint_urls(endpoint):
    """
    Get endpoint URLs from `ALLOWED_OPENAI_ENDPOINTS`.
    """
    return {
        openai_endpoint: f"/{endpoint['cluster']}/{endpoint['framework']}/v1/{openai_endpoint}/"
        for openai_endpoint in ALLOWED_OPENAI_ENDPOINTS[endpoint["cluster"]]
    }


def get_wrong_endpoint_urls():
    """
    Get list of URLS with unsupported cluster, framework, and openai endpoints.
    """
    # A valid cluster, framework, endpoint set
    cluster = ALLOWED_CLUSTERS[0]
    framework = ALLOWED_FRAMEWORKS[cluster][0]
    endpoint = ALLOWED_OPENAI_ENDPOINTS[cluster][0]

    return [
        f"/{c}/{f}/v1/{e}/"
        for c, f, e in (
            ("unsupported-cluster", framework, endpoint),
            (cluster, "unsupported-framework", endpoint),
            (cluster, framework, "unsupported-endpoint"),
        )
    ]


# Get wrong batch URLs
def get_wrong_batch_urls():
    """
    Get list of batch URLS with unsupported cluster and framework
    """
    # A valid cluster, framework set
    cluster = ALLOWED_CLUSTERS[0]
    framework = ALLOWED_FRAMEWORKS[cluster][0]

    return [
        f"/{c}/{f}/v1/batches"
        for c, f in (
            ("unsupported-cluster", framework),
            (cluster, "unsupported-framework"),
        )
    ]


# This is because Django Ninja client does not take content-type json for some reason...
def get_response_json(response):
    """
    Convert bytes response to dictionary.
    """
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


class ResourceServerTestCase(TestCase):
    @classmethod
    @override
    def setUpTestData(cls):
        """
        Initialization that will only happen once before running all tests.
        """

        # Fill Django test database
        call_command("loaddata", "fixtures/new_endpoints.json")
        call_command("loaddata", "fixtures/clusters.json")

        return super().setUpTestData()

    # Verify headers failures
    async def verify_headers_failures(self, url=None, method=None):
        """
        Make sure POST requests fail if something is wrong with the authentication.
        """

        # Should fail (not authenticated, missing token)
        headers = mock_utils.get_mock_headers(access_token="")
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 400)

        # Should fail (not a bearer token)
        headers = mock_utils.get_mock_headers(access_token=ACTIVE_TOKEN, bearer=False)
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 400)

        # Should fail (not a valid token)
        headers = mock_utils.get_mock_headers(access_token=INVALID_TOKEN, bearer=True)
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 401)

        # Should fail (expired token)
        headers = mock_utils.get_mock_headers(access_token=EXPIRED_TOKEN, bearer=True)
        response = await method(url, headers=headers)
        self.assertEqual(response.status_code, 401)

    @classmethod
    def template_test(cls, test_name, *args, **kwargs):
        """
        Templates a test given an argument list.
        """
        test = getattr(cls, test_name)
        to_alphanumeric = lambda x: re.sub(r"[^a-zA-Z0-9_]+", "", str(x))
        templated_name = (
            f"test_{test_name}_{to_alphanumeric(args)}{to_alphanumeric(kwargs)}"
        )

        if iscoroutinefunction(test):

            async def async_lambda(self):
                return await test(self, *args, **kwargs)

            setattr(
                cls,
                templated_name,
                async_lambda,
            )
        else:
            setattr(
                cls,
                templated_name,
                lambda self: test(self, *args, **kwargs),
            )
