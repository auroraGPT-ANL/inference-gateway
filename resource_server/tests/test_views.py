from rest_framework.test import APITestCase, APIRequestFactory
from rest_framework import status
import json

# Overwrite utils.py functions to prevent contacting Globus services
#TODO: Overwrite Globus calls here


# Test views.py
class ResourceServerViewTestCase(APITestCase):

    # Data and client initialization
    @classmethod
    def setUp(self):
        """
            Initialization that will only happen once before running all tests.
        """

        # Create non-Globus test user who started runs
        self.factory = APIRequestFactory()

    # Test ListEndpoints (get) 
    def test_ListEndpoints_get_view(self):

        #TODO: Start test here
        self.assertEqual(1,1)
#        request = self.factory.get('/resource_server/list-endpoints/')
