from django.core.management import call_command
from rest_framework.test import APITestCase, APIRequestFactory
from rest_framework import status
from resource_server import views
from resource_server.models import Endpoint
import json

# Overwrite utils functions to prevent contacting Globus services
import utils.auth_utils as auth_utils
import utils.globus_utils as globus_utils
import resource_server.tests.mock_utils as mock_utils
auth_utils.get_globus_client = mock_utils.get_globus_client
auth_utils.check_globus_policies = mock_utils.check_globus_policies
auth_utils.check_globus_groups = mock_utils.check_globus_groups
auth_utils.introspect_token = mock_utils.introspect_token
globus_utils.get_compute_client_from_globus_app = mock_utils.get_compute_client_from_globus_app
globus_utils.get_compute_executor = mock_utils.get_compute_executor

# Constants
COMPLETIONS = "completions/"
CHAT_COMPLETIONS = "chat/completions/"
EMBEDDINGS = "embeddings/"
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

        # Create mock access tokens
        self.active_token = mock_utils.get_mock_access_token(active=True, expired=False)
        self.expired_token = mock_utils.get_mock_access_token(active=True, expired=True)
        self.invalid_token = mock_utils.get_mock_access_token(active=False, expired=False)

        # Create headers with a valid access token
        self.headers = mock_utils.get_mock_headers(access_token=self.active_token, bearer=True)

        # Create request factory instance
        self.kwargs = {"content_type": "application/json"}
        self.factory = APIRequestFactory()

        # Load test input data (OpenAI format)
        base_path = "utils/tests/json"
        self.valid_params = {}
        with open(f"{base_path}/valid_completions.json") as json_file:
            self.valid_params[COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/valid_chat_completions.json") as json_file:
            self.valid_params[CHAT_COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/valid_embeddings.json") as json_file:
            self.valid_params[EMBEDDINGS] = json.load(json_file)
        self.invalid_params = {}
        with open(f"{base_path}/invalid_completions.json") as json_file:
            self.invalid_params[COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/invalid_chat_completions.json") as json_file:
            self.invalid_params[CHAT_COMPLETIONS] = json.load(json_file)
        with open(f"{base_path}/invalid_embeddings.json") as json_file:
            self.invalid_params[EMBEDDINGS] = json.load(json_file)


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
        response = view(self.factory.get(url, headers=self.headers))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Extract all endpoint objects from the database
        db_endpoints = Endpoint.objects.all()
        len_db_endpoints = len(db_endpoints)

        # Make sure GET requests return the correct number of endpoints
        self.assertEqual(len_db_endpoints, len(response.data))

        # For each endpoint in the Django database ...
        for endpoint in db_endpoints:

            # Build dictionary entry to compare with the request response
            urls = self.__get_endpoint_url(endpoint)
            entry = {
                "completion_endpoint_url": urls[COMPLETIONS],
                "chat_endpoint_url": urls[CHAT_COMPLETIONS],
                "embedding_endpoint_url": urls[EMBEDDINGS],
                "model_name": endpoint.model
            }

            # Make sure the entry is in the the request response
            self.assertIn(entry, response.data)

    
    # Test Polaris (POST)
    def test_polaris_view(self):
        self.__generic_test_cluster_view(
            views.Polaris.as_view(),
            Endpoint.objects.all().filter(cluster="polaris")
        )


    # Test Sophia (POST)
    def test_sophia_view(self):
        self.__generic_test_cluster_view(
            views.Sophia.as_view(),
            Endpoint.objects.all().filter(cluster="sophia")
        )


    # Test generic view (POST)
    def __generic_test_cluster_view(self, view, db_endpoints):

        # For each endpoint ...
        for endpoint in db_endpoints:
            
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

                # For each valid set of input parameters ...
                for valid_params in self.valid_params[openai_endpoint]:

                    # Overwrite the model to match the endpoint model (otherwise the view won't find the endpoint slug)
                    valid_params["model"] = endpoint.model

                    # Make sure POST requests succeed
                    request = self.factory.post(url, json.dumps(valid_params), headers=self.headers, **self.kwargs)
                    response = view(request, endpoint.framework, openai_endpoint[:-1])
                    if not response.status_code == status.HTTP_200_OK:
                        _ = response.render()
                        print(response.content)
                    self.assertEqual(response.status_code, status.HTTP_200_OK)
                    self.assertEqual(response.data[SERVER_RESPONSE], mock_utils.MOCK_RESPONSE)

                # Make sure POST requests fail when providing invalid inputs
                for invalid_params in self.invalid_params[openai_endpoint]:
                    request = self.factory.post(url, json.dumps(invalid_params), headers=self.headers, **self.kwargs)
                    response = view(request, endpoint.framework, openai_endpoint[:-1])
                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Make sure POST requests fail with unknown frameworks
        request = self.factory.post(url, headers=self.headers, **self.kwargs)
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
            CHAT_COMPLETIONS: f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/{CHAT_COMPLETIONS}",
            EMBEDDINGS: f"/resource_server/{endpoint.cluster}/{endpoint.framework}/v1/{EMBEDDINGS}"
        }
