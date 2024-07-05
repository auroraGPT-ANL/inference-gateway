from django.core.management import call_command
from rest_framework.test import APITestCase, APIRequestFactory
from rest_framework import status
from resource_server import views
from resource_server.models import Endpoint
import json

# Overwrite utils functions to prevent contacting Globus services
import utils.auth_utils as auth_utils
import resource_server.utils as resource_utils
import resource_server.tests.mock_utils as mock_utils
auth_utils.get_globus_client = mock_utils.get_globus_client
resource_utils.get_compute_client_from_globus_app = mock_utils.get_compute_client_from_globus_app

# Constants
COMPLETIONS = "completions/"
CHAT_COMPLETIONS = "chat/completions/"
from resource_server.views import SERVER_RESPONSE

# Test views.py
class ResourceServerViewTestCase(APITestCase):

    # Data and client initialization
    @classmethod
    def setUp(self):
        """
            Initialization that will only happen once before running all tests.
        """

        # Fill Django test database
        call_command("loaddata", "fixtures/endpoints.json")

        # Extract all endpoint objects from the database
        self.db_endpoints = Endpoint.objects.all()
        self.len_db_endpoints = len(self.db_endpoints)

        # Create mock access tokens
        self.active_token = mock_utils.get_mock_access_token(active=True, expired=False)
        self.expired_token = mock_utils.get_mock_access_token(active=True, expired=True)
        self.invalid_token = mock_utils.get_mock_access_token(active=False, expired=False)

        # Create request factory instance
        self.kwargs = {"content_type": "application/json"}
        self.factory = APIRequestFactory()

        # Load test input data (OpenAI format)
        base_path = "utils/tests/json"
        self.valid_params = {}
        with open(f"{base_path}/valid_legacy_openai.json") as json_file:
            self.valid_params[COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/valid_chat_openai.json") as json_file:
            self.valid_params[CHAT_COMPLETIONS] = json.load(json_file)
        self.invalid_params = {}
        with open(f"{base_path}/invalid_legacy_openai.json") as json_file:
            self.invalid_params[COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/invalid_chat_openai.json") as json_file:
            self.invalid_params[CHAT_COMPLETIONS] = json.load(json_file)


    # Test ListEndpoints (GET) 
    def test_ListEndpoints_view(self):

        # Define the targeted Django URL
        url = "/resource_server/list-endpoints/"

        # Select the targeted Django view
        view = views.ListEndpoints.as_view()

        # Make sure GET requests fail if something is wrong with the authentication
        self.__verify_headers_failures(url=url, view=view, method=self.factory.get)

        # Make sure non-GET requests are not allowed
        for method in [self.factory.post, self.factory.put, self.factory.delete]:
            response = view(method(url))
            self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

        # Make sure GET requests succeed when providing a valid access token
        headers = mock_utils.get_mock_headers(access_token=self.active_token, bearer=True)
        response = view(self.factory.get(url, headers=headers))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Make sure GET requests return the correct number of endpoints
        self.assertEqual(self.len_db_endpoints, len(response.data))

        # For each endpoint in the Django database ...
        for endpoint in self.db_endpoints:

            # Build dictionary entry to compare with the request response
            urls = self.__get_endpoint_url(endpoint)
            entry = {
                "completion_endpoint_url": urls[COMPLETIONS],
                "chat_endpoint_url": urls[CHAT_COMPLETIONS],
                "model_name": endpoint.model
            }

            # Make sure the entry is in the the request response
            self.assertIn(entry, response.data)

    
    # Test Polaris (POST)
    def test_polaris_view(self):

        # Select the targeted Django view
        view = views.Polaris.as_view()

        # Create headers with a valid access token
        headers = mock_utils.get_mock_headers(access_token=self.active_token, bearer=True)

        # For each endpoint ...
        for endpoint in self.db_endpoints:
            
            # Build the targeted Django URLs
            url_dict = self.__get_endpoint_url(endpoint)
            
            # For each URL (openai endpoint) ...
            for openai_endpoint, url in url_dict.items():

                # Make sure POST requests fail if something is wrong with the authentication
                self.__verify_headers_failures(url=url, view=view, method=self.factory.post)

                # Make sure non-POST requests are not allowed
                for method in [self.factory.get, self.factory.put, self.factory.delete]:
                    response = view(method(url))
                    self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

                # Make sure POST requests succeed when providing valid inputs
                for valid_params in self.valid_params[openai_endpoint]:
                    valid_params["model"] = endpoint.model
                    request = self.factory.post(url, json.dumps(valid_params), headers=headers, **self.kwargs)
                    response = view(request, endpoint.framework, openai_endpoint[:-1])
                    self.assertEqual(response.status_code, status.HTTP_200_OK)
                    self.assertEqual(response.data[SERVER_RESPONSE], mock_utils.MOCK_RESPONSE)

                # Make sure POST requests fail when providing invalid inputs
                for invalid_params in self.invalid_params[openai_endpoint]:
                    request = self.factory.post(url, json.dumps(invalid_params), headers=headers, **self.kwargs)
                    response = view(request, endpoint.framework, openai_endpoint[:-1])
                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Make sure POST requests fail with unknown frameworks
        request = self.factory.post(url, headers=headers, **self.kwargs)
        response = view(request, "not-a-framework", openai_endpoint[:-1])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Make sure POST requests fail with unknown openAI endpoints
        response = view(request, endpoint.framework, "not-an-openai-endpoint")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


    # Verify headers failures
    def __verify_headers_failures(self, url=None, view=None, method=None):

        # Should fail (not authenticated)
        headers = mock_utils.get_mock_headers(access_token="")
        response = view(method(url, headers=headers))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Should fail (not a bearer token)
        headers = mock_utils.get_mock_headers(access_token=self.active_token, bearer=False)
        response = view(method(url, headers=headers))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Should fail (not a valid token)
        headers = mock_utils.get_mock_headers(access_token=self.invalid_token, bearer=True)
        response = view(method(url, headers=headers))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Should fail (expired token)
        headers = mock_utils.get_mock_headers(access_token=self.expired_token, bearer=True)
        response = view(method(url, headers=headers))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        

    # Get endpoint URL
    def __get_endpoint_url(self, endpoint):
        return {
            COMPLETIONS: f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/{COMPLETIONS}",
            CHAT_COMPLETIONS: f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/{CHAT_COMPLETIONS}"
        }