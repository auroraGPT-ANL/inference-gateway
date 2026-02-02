from resource_server_async.tests import ResourceServerTestCase

from resource_server_async.models import Endpoint
import resource_server_async.tests.mock_utils as mock_utils
import json
import logging

log = logging.getLogger(__name__)


class InferenceViewTestCase(ResourceServerTestCase):
    async def test_post_inference_view(self):
        """
        Test post_inference view (POST)
        """
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
                    await self.__verify_headers_failures(
                        url=url, method=self.client.post
                    )

                    # Make sure non-POST requests are not allowed
                    for method in [
                        self.client.get,
                        self.client.put,
                        self.client.delete,
                    ]:
                        response = await method(url)
                        self.assertEqual(response.status_code, 405)

                    # If the endpoint can be accessed by the mock access token ...
                    if endpoint.allowed_globus_groups in [
                        [],
                        [mock_utils.MOCK_GROUP_UUID],
                    ]:
                        headers = self.premium_headers

                        # For each valid set of input parameters ...
                        for valid_params in self.valid_params[openai_endpoint]:
                            # Overwrite the model to match the endpoint model (otherwise the view won't find the endpoint slug)
                            valid_params["model"] = endpoint.model

                            # Make sure the request is not streaming (this is tested in another function)
                            # "if" statement needed since not all openai endpoints support streaming
                            if "stream" in valid_params:
                                valid_params["stream"] = False

                            # Make sure POST requests succeed
                            response = await self.client.post(
                                url,
                                data=json.dumps(valid_params).encode("utf-8"),
                                headers=headers,
                                **self.kwargs,
                            )
                            self.assertEqual(response.status_code, 200)

                            # Check the response
                            response_data = self.__get_response_json(response)
                            self.assertEqual(response_data, mock_utils.MOCK_RESPONSE)

                        # Make sure POST requests fail when providing invalid inputs
                        for invalid_params in self.invalid_params[openai_endpoint]:
                            response = await self.client.post(
                                url,
                                data=json.dumps(invalid_params).encode("utf-8"),
                                headers=headers,
                                **self.kwargs,
                            )
                            self.assertEqual(response.status_code, 400)

                    # Make sure users can't access private endpoint if not in allowed groups
                    if endpoint.allowed_globus_groups == [mock_utils.MOCK_GROUP_UUID]:
                        response = await self.client.post(
                            url,
                            data=json.dumps(valid_params).encode("utf-8"),
                            headers=self.headers,
                            **self.kwargs,
                        )
                        self.assertEqual(response.status_code, 401)
