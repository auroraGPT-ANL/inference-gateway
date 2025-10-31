import json
import uuid
import time
import asyncio
from asgiref.sync import sync_to_async
from django.utils import timezone
from utils import globus_utils
from django.http import StreamingHttpResponse
from django.core.cache import cache
from pydantic import BaseModel, Field
from typing import List, Optional
from utils.pydantic_models.db_models import AccessLogPydantic, RequestLogPydantic
from resource_server_async.models import RequestLog
from resource_server_async.utils import (
    remove_endpoint_from_cache,
    prepare_streaming_task_data,
    process_streaming_completion_async,
    create_access_log,
    extract_prompt,
    set_streaming_status,
    set_streaming_error,
    get_streaming_metadata,
    get_streaming_data_and_status_batch,
    format_streaming_error_for_openai,
    create_streaming_response_headers,
    is_cached
)
from resource_server_async.endpoints.endpoint import (
    BaseEndpoint,
    SubmitTaskResponse,
    SubmitStreamingTaskResponse,
    SubmitBatchResponse,
    GetBatchResultResponse,
    GetBatchStatusResponse
)

# Tool to log access requests
import logging
log = logging.getLogger(__name__)


# Configuration data structure
class EndpointConfig(BaseModel):
    api_port: int
    endpoint_uuid: str
    function_uuid: str
    batch_endpoint_uuid: Optional[str] = Field(default=None)
    batch_function_uuid: Optional[str] = Field(default=None)


# Globus Compute implementation of a BaseEndpoint
class GlobusCompute(BaseEndpoint):
    """Globus Compute implementation of BaseEndpoint."""
    
    # Class initialization
    def __init__(self,
        id: str = None,
        endpoint_slug: str = None,
        cluster: str = None,
        framework: str = None,
        model: str = None,
        endpoint_type: str = None,
        allowed_globus_groups: str = None,
        allowed_domains: str = None,
        config: dict = None
    ):
        # Validate endpoint configuration
        self._config = EndpointConfig(**config)

        # Initialize the rest of the common attributes
        super().__init__(id, endpoint_slug, cluster, framework, model, endpoint_type, allowed_globus_groups, allowed_domains)


    # Submit task
    async def submit_task(self, data) -> SubmitTaskResponse:
        """Submits a single interactive task to the compute resource."""

        # Get Globus Compute client and executor
        try:
            gcc = globus_utils.get_compute_client_from_globus_app()
            gce = globus_utils.get_compute_executor(client=gcc)
        except Exception as e:
            return SubmitTaskResponse(
                error_code=500,
                error_message=str(e)
            )

        # Query the status of the targetted Globus Compute endpoint
        # NOTE: Do not await here, cache the "first" request to avoid too-many-requests Globus error
        endpoint_status, error_message = globus_utils.get_endpoint_status(
            endpoint_uuid=self.config.endpoint_uuid, client=gcc, endpoint_slug=self.endpoint_slug
        )
        if len(error_message) > 0:
            return SubmitTaskResponse(
                error_code=500,
                error_message=error_message
            )

        # Check if the endpoint is running and whether the compute resources are ready (managers deployed)
        if not endpoint_status["status"] == "online":
            return SubmitTaskResponse(
                error_code=503,
                error_message=f"Error: Endpoint {self.endpoint_slug} is offline."
            )
        resources_ready = int(endpoint_status["details"]["managers"]) > 0

        # If the compute resource is not ready (if node not acquired or worker_init phase not completed)
        if not resources_ready:

            # If a user already triggered the model (model currently loading) ...
            cache_key = f"endpoint_triggered:{self.endpoint_slug}"
            if is_cached(cache_key, create_empty=False):
                
                # Send an error to avoid overloading the Globus Compute endpoint
                # This also reduces memory footprint on the API application
                error_message = f"Error: Endpoint {self.endpoint_slug} online but not ready to receive tasks. "
                error_message += "Please try again later."
                return SubmitTaskResponse(
                    error_code=503,
                    error_message=error_message
                )

        # Add API port to the input data
        try:
            data["model_params"]["api_port"] = self.config.api_port
        except Exception as e:
            remove_endpoint_from_cache(self.endpoint_slug)
            return SubmitTaskResponse(
                error_code=400,
                error_message=f"Error: Could not process endpoint data for {self.endpoint_slug}: {e}"
            )

        # Submit Globus Compute task and wait for the result
        result, task_id, error_message, error_code = await globus_utils.submit_and_get_result(
            gce, self.config.endpoint_uuid, self.config.function_uuid, resources_ready, data=data, endpoint_slug=self.endpoint_slug
        )
        if len(error_message) > 0:
            return SubmitTaskResponse(
                error_code=error_code,
                error_message=error_message
            )

        # Return the successful result
        return SubmitTaskResponse(
            result=result,
            task_id=task_id
        )
    

    # Submit streaming task
    async def submit_streaming_task(self,
        data, 
        access_log_data: AccessLogPydantic = None,
        request_log_data: RequestLogPydantic = None,
        ) -> SubmitStreamingTaskResponse:
        """Submits a single interactive task to the compute resource with streaming enabled."""
        
        # Generate unique task ID for streaming
        stream_task_id = str(uuid.uuid4())
        streaming_start_time = time.time()
        
        # Prepare streaming data payload using utility function
        data = prepare_streaming_task_data(data, stream_task_id)

        # Add API port to the input data
        try:
            data["model_params"]["api_port"] = self.config.api_port
        except Exception as e:
            remove_endpoint_from_cache(self.endpoint_slug)
            return SubmitStreamingTaskResponse(
                error_code=400,
                error_message=f"Error: Could not process endpoint data for {self.endpoint_slug}: {e}"
            )
        
        # Submit task to Globus Compute (same logic as non-streaming)
        try:

            # Assign endpoint UUID to the executor (same as submit_and_get_result)
            try:
                gcc = globus_utils.get_compute_client_from_globus_app()
                gce = globus_utils.get_compute_executor(client=gcc)
            except Exception as e:
                return SubmitStreamingTaskResponse(
                    error_code=500,
                    error_message=str(e)
                )
            gce.endpoint_id = self.config.endpoint_uuid
            
            # Submit Globus Compute task and collect the future object (same as submit_and_get_result)
            future = gce.submit_to_registered_function(self.config.function_uuid, args=[data])
            
            # Wait briefly for task to be registered with Globus (like submit_and_get_result does)
            # This allows the task_uuid to be populated without waiting for full completion
            try:
                asyncio_future = asyncio.wrap_future(future)
                # Wait just long enough for task registration (not full completion)
                await asyncio.wait_for(asyncio.shield(asyncio_future), timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # Timeout/cancellation is expected - we just want task registration, not completion
                pass
            except Exception:
                # Other exceptions don't prevent us from getting task_uuid
                pass
            
            # Get task_id from the future (should be available after brief wait)
            task_uuid = globus_utils.get_task_uuid(future)
            
        except Exception as e:
            return SubmitStreamingTaskResponse(
                error_code=500,
                error_message=f"Error: Could not submit streaming task: {e}"
            )
        
        # Cache the endpoint slug to tell the application that a user already submitted a request to this endpoint
        if self.endpoint_slug:
            cache_key = f"endpoint_triggered:{self.endpoint_slug}"
            ttl = 600 # 10 minutes
            try:
                cache.set(cache_key, True, ttl)
            except Exception as e:
                log.warning(f"Failed to cache endpoint_triggered:{self.endpoint_slug}: {e}")
        
        # Create initial log entry and get the ID for later updating
        request_log_data.result = "streaming_response_in_progress"
        request_log_data.timestamp_compute_response = timezone.now()
        
        # Set task_uuid in database data
        if task_uuid:
            request_log_data.task_uuid = str(task_uuid)
            log.info(f"Streaming request task UUID: {task_uuid}")
        else:
            log.warning("No task UUID captured for streaming request")
            request_log_data.task_uuid = None

        # Create AccessLog database entry
        access_log_data.status_code = 200
        try:
            access_log = await create_access_log(access_log_data, "", 200)
        except Exception as e:
            return SubmitStreamingTaskResponse(
                task_id=task_uuid,
                error_code=500,
                error_message=f"Error: Could not create AccessLog entry: {e}"
            )
        
        # Create initial log entry
        try:
            request_log_data.access_log = access_log
            db_log = RequestLog(**request_log_data.model_dump())
            await sync_to_async(db_log.save, thread_sensitive=True)()
            log_id = db_log.id
            log.info(f"Created initial streaming log entry {log_id} for task {task_uuid}")
        except Exception as e:
            log.error(f"Error creating initial streaming log entry: {e}")
            log_id = None
        
        # Start background processing for metrics collection (fire and forget)
        if log_id:
            asyncio.create_task(process_streaming_completion_async(
                task_uuid, stream_task_id, log_id, future, streaming_start_time,
                extract_prompt(data["model_params"]) if data.get("model_params") else None
            ))
        
        # Create simple SSE streaming response  
        async def sse_generator():
            """Simple SSE generator with fast Redis polling - P0 OPTIMIZED with pipeline batching"""
            try:
                max_wait_time = 300  # 5 minutes total timeout
                start_time = time.time()
                last_chunk_index = 0
                first_data_timeout = 30  # 30 seconds to receive first chunk or status
                first_data_received = False
                last_chunk_time = None  # Track when we last received a chunk
                no_new_data_timeout = 5  # 5 seconds with no new chunks = assume completion (fallback if /done not called)
                
                while time.time() - start_time < max_wait_time:
                    # P0 OPTIMIZATION: Get status, chunks, and error in a single Redis round-trip
                    chunks, status, error_message = get_streaming_data_and_status_batch(stream_task_id)
                    
                    # Check if we've received any data (chunks or status)
                    if (chunks and len(chunks) > 0) or status:
                        first_data_received = True
                    
                    # PRIORITY 1: Fast auth failure check (immediate break)
                    auth_failure = get_streaming_metadata(stream_task_id, "auth_failure")
                    if auth_failure:
                        error_msg = {
                            "object": "error",
                            "message": "Streaming authentication failed: Remote compute endpoint could not authenticate with streaming API. Check INTERNAL_STREAMING_SECRET configuration.",
                            "type": "AuthenticationError",
                            "param": None,
                            "code": 401
                        }
                        log.error(f"Streaming task {stream_task_id} - authentication failure detected")
                        set_streaming_status(stream_task_id, "error")
                        set_streaming_error(stream_task_id, error_msg.get("message"))
                        yield f"data: {json.dumps(error_msg)}\n\n"
                        yield "data: [DONE]\n\n"
                        break
                    
                    # PRIORITY 2: Early timeout check (no data after 30s)
                    elapsed_time = time.time() - start_time
                    if not first_data_received and elapsed_time > first_data_timeout:
                        error_msg = {
                            "object": "error",
                            "message": f"Streaming task timed out: No data received from compute endpoint after {first_data_timeout} seconds. This may indicate network or endpoint configuration issues.",
                            "type": "StreamingTimeoutError",
                            "param": None,
                            "code": 504
                        }
                        log.error(f"Streaming task {stream_task_id} timed out - no data received after {first_data_timeout}s")
                        set_streaming_status(stream_task_id, "error")
                        set_streaming_error(stream_task_id, error_msg.get("message"))
                        yield f"data: {json.dumps(error_msg)}\n\n"
                        yield "data: [DONE]\n\n"
                        break
                    
                    # PRIORITY 3: Handle error status (send error then break)
                    if status == "error":
                        if error_message:
                            # Format and send the error in OpenAI streaming format
                            formatted_error = format_streaming_error_for_openai(error_message)
                            yield formatted_error
                        # Send [DONE] after error to properly terminate the stream
                        yield "data: [DONE]\n\n"
                        break
                    
                    # PRIORITY 4: Process ALL pending chunks FIRST (drain the queue)
                    # This ensures we don't miss chunks that arrived just before /done
                    if chunks and len(chunks) > last_chunk_index:
                        # Send all new chunks at once
                        for i in range(last_chunk_index, len(chunks)):
                            chunk = chunks[i]
                            # Only send actual vLLM content chunks (skip our custom control messages)
                            if chunk.startswith('data: '):
                                # Send the vLLM chunk as-is
                                yield f"{chunk}\n\n"
                            
                            last_chunk_index = i + 1
                        
                        # Update last chunk time
                        last_chunk_time = time.time()
                    
                    # PRIORITY 5: Check completion status AFTER processing chunks
                    # This prevents race condition where /done arrives before final chunks
                    if status == "completed":
                        # One final check for any remaining chunks that arrived during processing
                        final_chunks, _, _ = get_streaming_data_and_status_batch(stream_task_id)
                        if final_chunks and len(final_chunks) > last_chunk_index:
                            for i in range(last_chunk_index, len(final_chunks)):
                                chunk = final_chunks[i]
                                if chunk.startswith('data: '):
                                    yield f"{chunk}\n\n"
                        
                        log.info(f"Streaming task {stream_task_id} - status is completed, sending [DONE]")
                        yield "data: [DONE]\n\n"
                        break
                    
                    # PRIORITY 6 (FALLBACK): No new data timeout
                    # This handles cases where remote function sent all data but didn't call /done endpoint
                    # Only check this if we haven't seen a "completed" status
                    if last_chunk_time is not None and (time.time() - last_chunk_time) > no_new_data_timeout:
                        log.warning(f"Streaming task {stream_task_id} - no new chunks for {no_new_data_timeout}s, assuming completion (done signal was not received)")
                        yield "data: [DONE]\n\n"
                        # Set completed status for cleanup
                        set_streaming_status(stream_task_id, "completed")
                        break
                    # Fast polling - 25ms
                    await asyncio.sleep(0.025)
                    
            except Exception as e:
                # For exceptions, just end without error message to maintain OpenAI compatibility
                log.error(f"Exception in SSE generator for task {stream_task_id}: {e}")
        
        # Create streaming response
        response = StreamingHttpResponse(
            streaming_content=sse_generator(),
            content_type='text/event-stream'
        )
        
        # Set headers for SSE using utility function
        headers = create_streaming_response_headers()
        for key, value in headers.items():
            response[key] = value

        # Return response with StreamingHttpResponse object
        return SubmitStreamingTaskResponse(
            response=response,
            task_id=task_uuid
        ) 
      

    async def submit_batch(self) -> SubmitBatchResponse: # <-- Needs arguments here ...
        """Submits a batch job to the compute resource."""
        pass

    async def get_batch_list(self) -> List[SubmitBatchResponse]: # <-- Needs arguments here ...
        """Get the list of a all batch jobs and their statuses."""
        pass

    async def get_batch_status(self) -> GetBatchStatusResponse: # <-- Needs arguments here ...
        """Get the status of a batch job."""
        pass

    async def get_batch_result(self) -> GetBatchResultResponse: # <-- Needs arguments here ...
        """Get the result of a completed batch job."""
        pass

    # Read-only access to the configuration
    @property
    def config(self):
        return self._config