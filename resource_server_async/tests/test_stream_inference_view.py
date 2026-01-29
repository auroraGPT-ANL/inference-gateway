from resource_server_async.tests import ResourceServerTestCase


class StreamInferenceViewTestCase(ResourceServerTestCase):
    async def test_post_streaming_inference_view(self):
        """
        Test streaming functionality (POST)
        This simply test streaming, most of the POST inference tests are done elsewhere.
        """
        
        # Skip if no streaming test cases are available
        if not self.streaming_test_cases:
            self.skipTest("No streaming test cases found in valid_chat_completions.json")
        
        # # For each endpoint in the database ...
        async for endpoint in Endpoint.objects.all():
            if "model-removed" not in endpoint.endpoint_slug:

                # If the endpoint's cluster supports chat/completions
                if "chat/completions" in self.ALLOWED_OPENAI_ENDPOINTS[endpoint.cluster]:
            
                    # Build the targeted Django URL for chat/completions
                    url = f"/{endpoint.cluster}/{endpoint.framework}/v1/chat/completions/"

                    # If the endpoint can be accessed by the mock access token ...
                    if endpoint.allowed_globus_groups in [[], [mock_utils.MOCK_GROUP_UUID]]:
                        headers = self.premium_headers
                        
                        # Test each streaming test case from the JSON data
                        for streaming_params in self.streaming_test_cases:

                            # Overwrite the model to match the endpoint model
                            streaming_params["model"] = endpoint.model
                            
                            # Test streaming request
                            response = await self.client.post(url, data=json.dumps(streaming_params).encode('utf-8'), headers=headers, **self.kwargs)
                            self.assertEqual(response.status_code, 200)
                            
                            # In a real streaming response, we'd get Server-Sent Events
                            # But in our mock implementation, we just verify the request is processed
                            # The response format might differ for streaming vs non-streaming
                            response_data = self.__get_response_json(response)
                            self.assertIsNotNone(response_data)  # Just verify we got some response