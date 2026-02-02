from resource_server_async.tests import ResourceServerTestCase

from resource_server_async.models import Endpoint
import resource_server_async.tests.mock_utils as mock_utils
import ast
import json
import uuid
import logging

log = logging.getLogger(__name__)


class BatchInferenceViewTestCase(ResourceServerTestCase):
    async def test_post_batch_inference_view(self):
        """
        Test post_batch_inference view (POST)
        """
        # Make sure POST requests fail when targetting an unsupported cluster or framework
        for wrong_url in self.__get_wrong_batch_urls():
            response = await self.client.post(wrong_url, headers=self.headers)
            self.assertEqual(response.status_code, 400)

        # For each endpoint that support batch in the database ...
        async for endpoint in Endpoint.objects.all():
            if "model-removed" not in endpoint.endpoint_slug:
                if (
                    len(
                        ast.literal_eval(endpoint.config).get("batch_endpoint_uuid", "")
                    )
                    > 0
                ):
                    # Build the targeted Django URL
                    url = f"/{endpoint.cluster}/{endpoint.framework}/v1/batches"

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
                        for valid_params in self.valid_params["batch"]:
                            # Overwrite the model to match the endpoint model (otherwise the view won't find the endpoint slug)
                            valid_params["model"] = endpoint.model

                            # Overwrite the input file to make it unique (otherwise will encounter "already used" error)
                            valid_params["input_file"] = f"/path/{str(uuid.uuid4())}"

                            # Make sure POST requests succeed
                            response = await self.client.post(
                                url,
                                data=json.dumps(valid_params).encode("utf-8"),
                                headers=headers,
                                **self.kwargs,
                            )
                            self.assertEqual(response.status_code, 200)

                            # Check whether the response makes sense (do not check batch_id, it's randomly generated in the view)
                            response_json = self.__get_response_json(response)
                            self.assertEqual(
                                response_json["input_file"], valid_params["input_file"]
                            )

                        # Make sure POST requests fail when providing invalid inputs
                        for invalid_params in self.invalid_params["batch"]:
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
