import json
from resource_server_async.models import Endpoint
from resource_server_async.tests import (
    HEADERS,
    INVALID_PARAMS,
    KWARGS,
    PREMIUM_HEADERS,
    VALID_PARAMS,
    ResourceServerTestCase,
    CLIENT,
    get_endpoint_urls,
    get_response_json,
    get_wrong_endpoint_urls,
    mock_utils,
)


class InferenceViewTestCase(ResourceServerTestCase):
    async def unsupported_post_request(self, endpoint):
        """
        Make sure POST requests fail when targetting an unsupported cluster, framework, or openai endpoint.
        """
        response = await CLIENT.post(endpoint, headers=HEADERS)
        self.assertEqual(response.status_code, 400)

    async def good_post_request(self, endpoint, valid_params, headers):
        """
        Make sure valid POST requests succeed.
        """
        response = await CLIENT.post(
            endpoint,
            data=json.dumps(valid_params).encode("utf-8"),
            headers=headers,
            **KWARGS,
        )
        self.assertEqual(response.status_code, 200)

        # Check the response
        response_data = get_response_json(response)
        self.assertEqual(response_data, mock_utils.MOCK_RESPONSE)

    async def invalid_post_request(self, endpoint, invalid_params, headers):
        """
        Make sure POST requests fail when providing invalid inputs.
        """
        response = await CLIENT.post(
            endpoint,
            data=json.dumps(invalid_params).encode("utf-8"),
            headers=headers,
            **KWARGS,
        )
        self.assertEqual(response.status_code, 400)

    async def inaccessible_post_request(self, endpoint, valid_params):
        """
        Make sure users can't access private endpoint if not in allowed groups.
        """
        response = await CLIENT.post(
            endpoint,
            data=json.dumps(valid_params).encode("utf-8"),
            headers=HEADERS,
            **KWARGS,
        )
        self.assertEqual(response.status_code, 401)

    async def non_post_request(self, endpoint):
        """
        Make sure non-POST requests are not allowed.
        """
        for method in [
            CLIENT.get,
            CLIENT.put,
            CLIENT.delete,
        ]:
            with self.subTest(method=method):
                response = await method(endpoint)
                self.assertEqual(response.status_code, 405)


# Template tests
for endpoint in get_wrong_endpoint_urls():
    InferenceViewTestCase.template_test("unsupported_post_request", endpoint)

for endpoint in Endpoint.objects.all():
    if "model-removed" in endpoint.endpoint_slug:
        continue

    # Build the targeted Django URLs
    url_dict = get_endpoint_urls(endpoint)

    # For each URL (openai endpoint) ...
    for openai_endpoint, url in url_dict.items():
        InferenceViewTestCase.template_test(
            "verify_headers_failures", url=url, method=CLIENT.post
        )
        InferenceViewTestCase.template_test(
            "non_post_request",
            url,
        )

        if endpoint.allowed_globus_groups not in [
            [],
            [mock_utils.MOCK_GROUP_UUID],
        ]:
            continue

        # If the endpoint can be accessed by the mock access token ...
        headers = PREMIUM_HEADERS

        # For each valid set of input parameters ...
        for valid_params in VALID_PARAMS[openai_endpoint]:
            # Overwrite the model to match the endpoint model (otherwise the view won't find the endpoint slug)
            valid_params["model"] = endpoint.model

            # Make sure the request is not streaming (this is tested in another function)
            # "if" statement needed since not all openai endpoints support streaming
            if "stream" in valid_params:
                valid_params["stream"] = False

            InferenceViewTestCase.template_test(
                "good_post_request", url, valid_params, headers
            )

            if endpoint.allowed_globus_groups == [mock_utils.MOCK_GROUP_UUID]:
                InferenceViewTestCase.template_test(
                    "inaccessible_post_request",
                    url,
                    valid_params,
                )

        for invalid_params in INVALID_PARAMS[openai_endpoint]:
            InferenceViewTestCase.template_test(
                "invalid_post_request",
                url,
                invalid_params,
                headers,
            )
