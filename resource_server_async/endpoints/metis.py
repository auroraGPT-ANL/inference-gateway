import asyncio
import json
import time
from django.http import StreamingHttpResponse
from pydantic import BaseModel
from typing import Any
from resource_server_async.utils import create_streaming_response_headers
from resource_server_async.endpoints.endpoint import (
    BaseEndpoint,
    SubmitTaskResponse,
    SubmitStreamingTaskResponse,
    GetEndpointStatusResponse
)
from utils import metis_utils

# Logging tool
import logging
log = logging.getLogger(__name__)


# Model status data structure
class ModelStatus(BaseModel):
    model_info: Any
    endpoint_id: str


# Metis endpoint implementation of a BaseEndpoint
class MetisEndpoint(BaseEndpoint):
    """Metis endpoint implementation of BaseEndpoint."""
    
    # Class initialization
    def __init__(self,
        id: str = None,
        endpoint_slug: str = None,
        cluster: str = None,
        framework: str = None,
        model: str = None,
        endpoint_adapter: str = None,
        allowed_globus_groups: str = None,
        allowed_domains: str = None,
        config: dict = None
    ):
        # Validate endpoint configuration
        #self._config = DirectAPIConfig(**config)
        # TODO get URL from config
        # TODO get model-base key from env

        # Initialize the rest of the common attributes
        super().__init__(id, endpoint_slug, cluster, framework, model, endpoint_adapter, allowed_globus_groups, allowed_domains)


    # Get endpoint status
    async def get_endpoint_status(self) -> GetEndpointStatusResponse:
        """Return endpoint status or an error is the endpoint cannot receive requests."""
        
        # Check external API status to see if it can accept request
        metis_status, error_message = await metis_utils.fetch_metis_status(use_cache=True)
        if error_message:
            return GetEndpointStatusResponse(error_code=500, error_message=error_message)

        # Log requested model name
        log.info(f"Metis inference request for model: {self.model}")
        
        # Find matching model in Metis status (returns endpoint_id for API token lookup)
        model_info, endpoint_id, error_message = metis_utils.find_metis_model(metis_status, self.model)
        if error_message:
            return GetEndpointStatusResponse(error_code=404, error_message=error_message)
        
        # Check if the model is Live
        if model_info.get("status") != "Live":
            return GetEndpointStatusResponse(
                error_code=503,
                error_message=f"Error: '{self.model}' is not currently live on Metis. Status: {model_info.get('status')}"
            )
        
        # Create model status object
        try:
            model_status = ModelStatus(model_info=model_info, endpoint_id=endpoint_id)
        except Exception as e:
            return GetEndpointStatusResponse(
                error_code=500,
                error_message=f"Error: Could not generate model status for endtpoint {self.endpoint_slug}: {e}"
            )

        # Return endpoint status
        return GetEndpointStatusResponse(status=model_status)


    # Submit task
    async def submit_task(self, data) -> SubmitTaskResponse:
        """Submits a single interactive task to the compute resource."""

        # Check endpoint status
        response = await self.get_endpoint_status()
        if response.error_message:
            return SubmitTaskResponse(error_code=response.error_code, error_message=response.error_message)
        model_status = response.status
        
        # Use validated request data as-is (already in OpenAI format)
        # Only update the stream parameter to match the request
        api_request_data = {**data["model_params"]}
        api_request_data["stream"] = False
        # Remove internal field that shouldn't be sent to Metis
        api_request_data.pop("openai_endpoint", None)
        api_request_data.pop("api_port", None)
        
        # Log model and Metis endpoint ID
        log.info(f"Making Metis API call for model {self.model} (stream=False, endpoint={model_status.endpoint_id})")
        
        # Send request to Metis
        result, status_code, error_message = await metis_utils.call_metis_api(
            model_status.model_info,
            model_status.endpoint_id,
            api_request_data,
            stream=False
        )            
        if error_message:
            return SubmitTaskResponse(error_code=status_code, error_message=error_message)

        # Return Metis API results
        return SubmitTaskResponse(result=result)
            

    # Submit streaming task
    async def submit_streaming_task(self, data: dict, request_log_id: str) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""
        
        # Check endpoint status
        response = await self.get_endpoint_status()
        if response.error_message:
            return SubmitStreamingTaskResponse(error_code=response.error_code, error_message=response.error_message)
        model_status = response.status

        # Get requested model name
        requested_model = data["model_params"]["model"]
        log.info(f"Metis inference request for model: {requested_model}")
        
        # Use validated request data as-is (already in OpenAI format)
        # Only update the stream parameter to match the request
        api_request_data = {**data["model_params"]}
        api_request_data["stream"] = True
        # Remove internal field that shouldn't be sent to Metis
        api_request_data.pop("openai_endpoint", None)
        api_request_data.pop("api_port", None)
        
        log.info(f"Making Metis API call for model {requested_model} (stream=True, endpoint={model_status.endpoint_id})")

        # Shared state for tracking streaming (optimized - minimal memory)
        streaming_state = {
            'chunks': [],  # Limited to 100 chunks
            'total_chunks': 0,
            'completed': False,
            'error': None,
            'start_time': time.time()
        }
        
        # SSE generator
        async def metis_sse_generator():
            """Stream SSE chunks from Metis API"""
            try:
                async for chunk in metis_utils.stream_metis_api(model_status.model_info, model_status.endpoint_id, api_request_data):
                    if chunk:
                        streaming_state['total_chunks'] += 1
                        yield chunk  # Pass through SSE format
                        
                        # Collect limited chunks for logging (optimize memory)
                        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                            if len(streaming_state['chunks']) < 100:
                                try:
                                    streaming_state['chunks'].append(chunk[6:].strip())
                                except:
                                    pass
                
                streaming_state['completed'] = True
                        
            except Exception as e:
                error_str = str(e)
                log.error(f"Metis streaming error: {error_str}")
                streaming_state['error'] = error_str
                streaming_state['completed'] = True
                
                # Send error as OpenAI streaming chunk format (compatible with OpenAI clients)
                error_chunk = {
                    "id": f"chatcmpl-metis-error",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": requested_model,
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "content": f"\n\n[ERROR] {error_str}"
                        },
                        "finish_reason": "stop"
                    }]
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                yield "data: [DONE]\n\n"
        
        # Start background task to update log
        asyncio.create_task(metis_utils.update_metis_streaming_log(request_log_id, streaming_state, requested_model))
        
        # Create streaming response
        response = StreamingHttpResponse(streaming_content=metis_sse_generator(), content_type='text/event-stream')
        
        # Set SSE headers
        for key, value in create_streaming_response_headers().items():
            response[key] = value
        
        # Return response with StreamingHttpResponse object
        return SubmitStreamingTaskResponse(response=response)


    # Read-only access to the configuration
    #@property
    #def config(self):
    #    return self._config