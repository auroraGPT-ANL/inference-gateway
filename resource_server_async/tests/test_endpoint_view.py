from resource_server_async.tests import ResourceServerTestCase

from resource_server_async.models import Endpoint
import resource_server_async.tests.mock_utils as mock_utils
import logging

log = logging.getLogger(__name__)


class EndpointsViewTestCase(ResourceServerTestCase):
    # Define the targeted Django URL
    url = "/list-endpoints"

    async def test_bad_auth(self):
        """
        Make sure GET requests fail if something is wrong with the authentication
        """
        await self._verify_headers_failures(url=self.url, method=self.client.get)

    async def test_non_get(self):
        """
        Make sure non-GET requests are not allowed
        """
        for method in [self.client.post, self.client.put, self.client.delete]:
            response = await method(self.url)
            self.assertEqual(response.status_code, 405)

    async def test_get_list_endpoints(self):
        """
        Test get_list_endpoints (GET)
        """
        (
            db_endpoints_public,
            db_endpoints_premium,
        ) = await self.__get_endpoint_object_counts()

        # For valid tokens with and without premium access ...
        # TODO: Factor these tests out as individual units (setattr?)
        for headers in [self.headers, self.premium_headers]:
            # Make sure GET requests succeed when providing a valid access token
            response = await self.client.get(self.url, headers=headers)
            response_data = self._get_response_json(response)
            self.assertEqual(response.status_code, 200)

            # Define the total number of expected endpoints
            nb_endpoints_expected = db_endpoints_public
            if headers == self.premium_headers:
                nb_endpoints_expected += db_endpoints_premium

            # Make sure the GET request returns the correct number of endpoints
            nb_endpoints = 0
            for cluster in response_data["clusters"]:
                for framework in response_data["clusters"][cluster]["frameworks"]:
                    nb_endpoints += len(
                        response_data["clusters"][cluster]["frameworks"][framework][
                            "models"
                        ]
                    )
            self.assertEqual(nb_endpoints_expected, nb_endpoints)

    async def __get_endpoint_object_counts(self):
        """
        Extract number of public and premium Globus Compute endpoint objects from the database
        """
        # TODO: Re work this to test number of models with clusters that have direct API access
        db_endpoints_public = 0
        async for _ in Endpoint.objects.filter(allowed_globus_groups=[]):
            db_endpoints_public += 1
        db_endpoints_premium = 0
        async for _ in Endpoint.objects.filter(
            allowed_globus_groups=[mock_utils.MOCK_GROUP_UUID]
        ):
            db_endpoints_premium += 1

        return db_endpoints_public, db_endpoints_premium
