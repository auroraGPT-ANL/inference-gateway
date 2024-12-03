from django.core.management import call_command
from resource_server.models import Endpoint
import json

# Tools to test with Django Ninja
from django.test import TestCase
from ninja.testing import TestClient
from resource_server_async.views import router

# Overwrite utils functions to prevent contacting Globus services
import asyncio
import utils.auth_utils as auth_utils
import resource_server.utils as resource_utils
import resource_server_async.tests.mock_utils as mock_utils
auth_utils.get_globus_client = mock_utils.get_globus_client
auth_utils.check_globus_policies = mock_utils.check_globus_policies
auth_utils.check_globus_groups = mock_utils.check_globus_groups
auth_utils.introspect_token = mock_utils.introspect_token
resource_utils.get_compute_client_from_globus_app = mock_utils.get_compute_client_from_globus_app
resource_utils.get_compute_executor = mock_utils.get_compute_executor
asyncio.wrap_future = mock_utils.wrap_future
asyncio.wait_for = mock_utils.wait_for

# Constants
from resource_server_async.utils import ALLOWED_CLUSTERS, ALLOWED_FRAMEWORKS, ALLOWED_OPENAI_ENDPOINTS


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
        self.client = TestClient(router)

        # Load valid test input data (OpenAI format)
        base_path = "utils/tests/json"
        self.valid_params = {}
        with open(f"{base_path}/valid_completions.json") as json_file:
            self.valid_params["completions"] = json.load(json_file)
        with open(f"{base_path}/valid_chat_completions.json") as json_file:
            self.valid_params["chat/completions"] = json.load(json_file)
        with open(f"{base_path}/valid_embeddings.json") as json_file:
            self.valid_params["embeddings"] = json.load(json_file)

        # Load invalid test input data (OpenAI format)
        self.invalid_params = {}
        with open(f"{base_path}/invalid_completions.json") as json_file:
            self.invalid_params["completions"] = json.load(json_file)
        with open(f"{base_path}/invalid_chat_completions.json") as json_file:
            self.invalid_params["chat/completions"] = json.load(json_file)
        with open(f"{base_path}/invalid_embeddings.json") as json_file:
            self.invalid_params["embeddings"] = json.load(json_file)


    # Test get_list_endpoints (GET) 
    def test_get_list_endpoints_view(self):

        # Define the targeted Django URL
        url = "/resource_server/list-endpoints"

        # Make sure GET requests fail if something is wrong with the authentication
        self.__verify_headers_failures(url=url, method=self.client.get)

        # Make sure non-GET requests are not allowed
        for method in [self.client.post, self.client.put, self.client.delete]:
            response = method(url)
            self.assertEqual(response.status_code, 405)

        # Extract number of public and premium endpoint objects from the database
        db_endpoints_public = len(Endpoint.objects.filter(allowed_globus_groups=""))
        db_endpoints_premium = len(Endpoint.objects.filter(allowed_globus_groups=mock_utils.MOCK_ALLOWED_GROUP))

        # For valid tokens with and without premium access ...
        for headers in [self.headers, self.premium_headers]:

            # Make sure GET requests succeed when providing a valid access token
            response = self.client.get(url, headers=headers)
            response_data = self.__get_response_json(response)
            self.assertEqual(response.status_code, 200)

            # Define the total number of expected endpoints
            nb_endpoints_expected = db_endpoints_public
            if headers == self.premium_headers:
                nb_endpoints_expected += db_endpoints_premium

            # Make sure the GET request returns the correct number of endpoints
            nb_endpoints = 0
            for cluster in response_data["clusters"]:
                for framework in response_data["clusters"][cluster]["frameworks"]:
                    nb_endpoints += len(response_data["clusters"][cluster]["frameworks"][framework]["models"])
            self.assertEqual(nb_endpoints_expected, nb_endpoints)


    # Test post_inference view (POST)
    def test_post_inference_view(self):

        # NOTE check along the way if that is private or not or MOCK private or not ....

        # Make sure POST requests fail when targetting an unsupported cluster, framewor, or openai endpoint
        for wrong_url in self.__get_wrong_endpoint_urls():
            response = self.client.post(wrong_url, headers=self.headers)
            self.assertEqual(response.status_code, 400)

        # For each supported endpoint in the database ...
        for endpoint in Endpoint.objects.all():
            
            # Build the targeted Django URLs
            url_dict = self.__get_endpoint_urls(endpoint)

            # For each URL (openai endpoint) ...
            for openai_endpoint, url in url_dict.items():

                # Make sure POST requests fail if something is wrong with the authentication
                self.__verify_headers_failures(url=url, method=self.client.post)

                # Make sure non-POST requests are not allowed
                for method in [self.client.get, self.client.put, self.client.delete]:
                    response = method(url)
                    self.assertEqual(response.status_code, 405)

                # If the endpoint can be accessed by the mock access token ...
                if endpoint.allowed_globus_groups in ["", mock_utils.MOCK_ALLOWED_GROUP]:
                    headers = self.premium_headers

                    # For each valid set of input parameters ...
                    for valid_params in self.valid_params[openai_endpoint]:

                        # Overwrite the model to match the endpoint model (otherwise the view won't find the endpoint slug)
                        valid_params["model"] = endpoint.model

                        # Make sure POST requests succeed
                        response = self.client.post(url, data=json.dumps(valid_params), headers=headers, **self.kwargs)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(self.__get_response_json(response), mock_utils.MOCK_RESPONSE)

                    # Make sure POST requests fail when providing invalid inputs
                    for invalid_params in self.invalid_params[openai_endpoint]:
                        response = self.client.post(url, data=json.dumps(invalid_params), headers=headers, **self.kwargs)
                        self.assertEqual(response.status_code, 400)

                # Make sure users can't access private endpoint if not in allowed groups
                if endpoint.allowed_globus_groups == mock_utils.MOCK_ALLOWED_GROUP:
                    response = self.client.post(url, data=json.dumps(valid_params), headers=self.headers, **self.kwargs)
                    self.assertEqual(response.status_code, 401)


    # Verify headers failures
    def __verify_headers_failures(self, url=None, method=None):

        # Should fail (not authenticated)
        headers = mock_utils.get_mock_headers(access_token="")
        response = method(url, headers=headers)
        self.assertEqual(response.status_code, 400)

        # Should fail (not a bearer token)
        headers = mock_utils.get_mock_headers(access_token=self.active_token, bearer=False)
        response = method(url, headers=headers)
        self.assertEqual(response.status_code, 400)

        # Should fail (not a valid token)
        headers = mock_utils.get_mock_headers(access_token=self.invalid_token, bearer=True)
        response = method(url, headers=headers)
        self.assertEqual(response.status_code, 401)

        # Should fail (expired token)
        headers = mock_utils.get_mock_headers(access_token=self.expired_token, bearer=True)
        response = method(url, headers=headers)
        self.assertEqual(response.status_code, 401)
        

    # Convert bytes response to dictionary
    # This is because Django Ninja client does not take content-type json for some reason...
    def __get_response_json(self, response):
        try:
            return json.loads(response._container[0].decode('utf-8'))   
        except:
            return response._container[0].decode('utf-8')


    # Get endpoint URL
    def __get_endpoint_urls(self, endpoint):
        urls = {}
        for openai_endpoint in ALLOWED_OPENAI_ENDPOINTS[endpoint.cluster]:
             urls[openai_endpoint] = f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/{openai_endpoint}/"
        return urls


    # Get wrong endpoint URLs
    def __get_wrong_endpoint_urls(self):

        # Declare list of URLS with unsupported cluster, framework, and openai endpoint
        wrong_urls = []

        # Unsupported cluster
        cluster = "unsupported-cluster"
        framework = ALLOWED_FRAMEWORKS[ALLOWED_CLUSTERS[0]][0]
        endpoint = ALLOWED_OPENAI_ENDPOINTS[ALLOWED_CLUSTERS[0]][0]
        wrong_urls.append(f"/resource_server/{cluster}/{framework}/v1/{endpoint}/",)

        # Unsupported framework
        cluster = ALLOWED_CLUSTERS[0]
        framework = "unsupported-framework"
        endpoint = ALLOWED_OPENAI_ENDPOINTS[ALLOWED_CLUSTERS[0]][0]
        wrong_urls.append(f"/resource_server/{cluster}/{framework}/v1/{endpoint}/",)

        # Unsupported openai endpoint
        cluster = ALLOWED_CLUSTERS[0]
        framework = ALLOWED_FRAMEWORKS[ALLOWED_CLUSTERS[0]][0]
        endpoint = "unsupported-endpoint"
        wrong_urls.append(f"/resource_server/{cluster}/{framework}/v1/{endpoint}/",)

        # Return list of unsupported URLs
        return wrong_urls
        