from django.core.management import call_command
from rest_framework.test import APITestCase, APIRequestFactory
from rest_framework import status
from resource_server import views
from resource_server.models import Endpoint

# Overwrite utils functions to prevent contacting Globus services
import utils.auth_utils as auth_utils
import resource_server.tests.mock_utils as mock_utils
auth_utils.get_globus_client = mock_utils.get_globus_client

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

        # Create non-Globus test user who started runs
        self.factory = APIRequestFactory()


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
        db_endpoints = Endpoint.objects.all()
        self.assertEqual(len(db_endpoints), len(response.data))

        # For each endpoint in the Django database ...
        for endpoint in db_endpoints:

            # Build dictionary entry to compare with the request response
            entry = {
                "endpoint_url": f"/resource_server/{endpoint.cluster}/{endpoint.framework}/completions/",
                "model_name": endpoint.model
            }

            # Make sure the entry is in the the request response
            self.assertIn(entry, response.data)


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
        
