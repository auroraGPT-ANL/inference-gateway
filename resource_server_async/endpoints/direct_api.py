import asyncio
import json
import time
import os
import httpx
from asgiref.sync import sync_to_async
from django.utils import timezone
from django.http import StreamingHttpResponse
from pydantic import BaseModel, Field
from typing import Any, Optional, Dict
from resource_server_async.utils import create_streaming_response_headers
from resource_server_async.models import RequestLog
from resource_server_async.endpoints.endpoint import BaseEndpoint, BaseModelWithError, SubmitTaskResponse, SubmitStreamingTaskResponse
import logging
log = logging.getLogger(__name__)


class DirectAPIEndpointConfig(BaseModel):
    api_url: str
    api_key_env_name: str
    api_request_timeout: Optional[int] = Field(default=120)


# DirectAPI endpoint implementation of a BaseEndpoint
class DirectAPIEndpoint(BaseEndpoint):
    """Direct API endpoint implementation of BaseEndpoint."""
    
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
        # Validate and assign endpoint configuration
        self.__config = DirectAPIEndpointConfig(**config)

        # Build request headers with API key from environment variable
        self.__headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get(self.__config.api_key_env_name, None)}"
        }

        # Initialize the rest of the common attributes
        super().__init__(id, endpoint_slug, cluster, framework, model, endpoint_adapter, allowed_globus_groups, allowed_domains)


    # Submit task
    async def submit_task(self, data: dict) -> SubmitTaskResponse:
        """Submits a single interactive task to the compute resource."""
        
        # Create an async HTTPx client
        try:
            async with httpx.AsyncClient(timeout=self.config.api_request_timeout) as client:
                
                # Make a call to the API with input data
                response = await client.post(self.config.api_url, json=data, headers=self.__headers)
                
                # Return error if something went wrong
                if response.status_code != 200:
                    return SubmitTaskResponse(
                        error_message=f"Error: Could not send API call to {self.config.api_url}: {response.text.strip()}",
                        error_code=response.status_code
                    )
                
                # Return result if API call worked
                return SubmitTaskResponse(
                    result=response.text
                )

        # Errors
        except httpx.TimeoutException:
            return SubmitTaskResponse(
                error_message=f"Error: Timeout calling API at {self.config.api_url} (timeout: {self.config.api_request_timeout})",
                error_code=504
            )
        except httpx.HTTPError as e:
            return SubmitTaskResponse(
                error_message=f"Error: HTTP error calling API at {self.config.api_url}: {e}",
                error_code=500
            )
        except Exception as e:
            return SubmitTaskResponse(
                error_message = f"Error: Unexpected error calling API: {e}",
                error_code=500
            )


    # Call stream API
    async def submit_streaming_task(self, data: dict, request_log_id: str) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""

        # Shared state for tracking streaming (optimized - minimal memory)
        streaming_state = {
            'chunks': [],  # Limited to 100 chunks
            'total_chunks': 0,
            'completed': False,
            'error': None,
            'start_time': time.time()
        }
        
        # SSE generator
        async def sse_generator():
            """Stream SSE chunks from API."""

            # For each streaming chunk ...
            try:
                async for chunk in self.__get_stream_chunks(data):
                    if chunk:

                        # Send chunk
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

            # Send error as OpenAI streaming chunk format (compatible with OpenAI clients)
            except Exception as e:
                error_str = str(e)
                streaming_state['error'] = error_str
                streaming_state['completed'] = True
                error_chunk = {
                    "id": f"chatcmpl-api-error",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": self.model,
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
        try:
            asyncio.create_task(self.__update_streaming_log(request_log_id, streaming_state))
        except Exception as e:
            return SubmitStreamingTaskResponse(
                error_message=f"Error: Could not create asyncio task: {e}",
                error_code=500
            )
        
        # Create streaming response
        response = StreamingHttpResponse(streaming_content=sse_generator(), content_type='text/event-stream')

        # Set SSE headers
        for key, value in create_streaming_response_headers().items():
            response[key] = value

        # Return streaming response
        return SubmitStreamingTaskResponse(response=response)
    

    # Get stream chunks
    async def __get_stream_chunks(self, data: Dict):
        """Make a direct API streaming call to the endpoint."""

        # Create an async HTTPx client
        try:
            async with httpx.AsyncClient(timeout=self.config.api_request_timeout) as client:

                # Create a streaming client
                async with client.stream("POST", self.config.api_url, json=data, headers=self.__headers) as response:

                    # Return error if something went wrong
                    if response.status_code != 200:
                        error_text = await response.aread()
                        raise ValueError(f"Error: Could not send stream API call to {self.config.api_url}: {error_text.decode().strip()}")
                    
                    # Stream the response
                    async for chunk in response.aiter_text():
                        if chunk:
                            yield chunk

        # Errors
        except httpx.TimeoutException:
            raise ValueError(f"Error: Timeout calling stream API at {self.config.api_url} (timeout: {self.config.api_request_timeout})")
        except httpx.HTTPError as e:
            raise ValueError(f"Error: HTTP error calling stream API at {self.config.api_url}: {e}")
        except Exception as e:
            raise ValueError(f"Error: Unexpected error calling stream API: {e}")


    # Update streaming log
    async def __update_streaming_log(self, request_log_id: str, streaming_state: dict):
        """Background task to update RequestLog after streaming completes."""
        try:

            # Wait for completion (efficient polling with timeout)
            max_wait = 600  # 10 minutes
            waited = 0
            poll_interval = 0.5  # 500ms
            while not streaming_state['completed'] and waited < max_wait:
                await asyncio.sleep(poll_interval)
                waited += poll_interval
            
            # Get metrics
            duration = time.time() - streaming_state['start_time']
            total_chunks = streaming_state['total_chunks']
            
            # Get database object from database
            db_log = await sync_to_async(RequestLog.objects.get)(id=request_log_id)
            
            # Log error if something went wrong
            if streaming_state['error']:
                db_log.result = f"error: {streaming_state['error']}"
                log.error(f"API streaming failed for {self.endpoint_slug}: {streaming_state['error']}")

            # Store limited chunks or completion marker
            else:
                db_log.result = "\n".join(streaming_state['chunks']) if streaming_state['chunks'] else "streaming_completed"
                log.info(f"Metis streaming completed for {self.endpoint_slug}: {total_chunks} chunks in {duration:.2f}s")
            
            # Update log entry in the database
            db_log.timestamp_compute_response = timezone.now()
            await sync_to_async(db_log.save, thread_sensitive=True)()
            
        # Log error if something went wrong
        except Exception as e:
            log.error(f"Error in update_streaming_log: {e}")


    # Read-only access to the configuration
    @property
    def config(self):
        return self.__config