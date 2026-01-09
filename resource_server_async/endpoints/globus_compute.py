import json
import uuid
import time
import asyncio
from utils import globus_utils
from django.http import StreamingHttpResponse
from django.core.cache import cache
from pydantic import BaseModel, Field
from typing import Optional, Any, List
from resource_server_async.utils import (
    remove_endpoint_from_cache,
    prepare_streaming_task_data,
    process_streaming_completion_async,
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
    BaseModelWithError,
    SubmitTaskResponse,
    SubmitStreamingTaskResponse,
    SubmitBatchResponse,
    GetBatchStatusResponse,
)
from resource_server_async.models import BatchLog
from utils.pydantic_models.batch import BatchStatusEnum
import logging
log = logging.getLogger(__name__)


class GetEndpointStatusResponse(BaseModelWithError):
    status: Optional[Any] = None

class GlobusComputeEndpointConfig(BaseModel):
    api_port: int
    endpoint_uuid: str
    function_uuid: str
    batch_endpoint_uuid: Optional[str] = Field(default=None)
    batch_function_uuid: Optional[str] = Field(default=None)


# Globus Compute implementation of a BaseEndpoint
class GlobusComputeEndpoint(BaseEndpoint):
    """Globus Compute implementation of BaseEndpoint."""
    
    # Class initialization
    def __init__(self,
        id: str,
        endpoint_slug: str,
        cluster: str,
        framework: str,
        model: str,
        endpoint_adapter: str,
        allowed_globus_groups: List[str] = None,
        allowed_domains: List[str] = None,
        config: dict = None
    ):
        # Validate endpoint configuration
        self.__config = GlobusComputeEndpointConfig(**config)

        # Force to check managers for readiness with a single-user endpoint
        self.check_managers = True

        # Initialize the rest of the common attributes
        super().__init__(id, endpoint_slug, cluster, framework, model, endpoint_adapter, allowed_globus_groups, allowed_domains)


    # Get endpoint status
    async def get_endpoint_status(self, gcc=None, check_managers=False, for_batch=False) -> GetEndpointStatusResponse:
        """Return endpoint status or an error is the endpoint cannot receive requests."""
        
        # Get Globus Compute client
        if gcc is None:
            try:
                gcc = globus_utils.get_compute_client_from_globus_app()
            except Exception as e:
                return GetEndpointStatusResponse(
                    error_message=str(e),
                    error_code=500
                )
        
        # Query the status of the targetted Globus Compute endpoint
        # NOTE: Do not await here, cache the "first" request to avoid too-many-requests Globus error
        if for_batch:
            endpoint_status, error_message = globus_utils.get_endpoint_status(
                endpoint_uuid=self.config.batch_endpoint_uuid, client=gcc, endpoint_slug=self.endpoint_slug+"/batch"
            )
        else:
            endpoint_status, error_message = globus_utils.get_endpoint_status(
                endpoint_uuid=self.config.endpoint_uuid, client=gcc, endpoint_slug=self.endpoint_slug
            )
        if len(error_message) > 0:
            return GetEndpointStatusResponse(
                error_message=error_message,
                error_code=500
            )

        # Check if the endpoint is online
        if not endpoint_status["status"] == "online":
            return GetEndpointStatusResponse(
                error_message=f"Error: Endpoint {self.endpoint_slug} is offline.",
                error_code=503
            )
        
        # If managers should be checked ...
        # This is to prevent submitting requests to an endpoint that is not ready yet
        if check_managers:

            # Extract whether managers are deployed on the online endpoint
            try:
                resources_ready = int(endpoint_status["details"]["managers"]) > 0
            except Exception as e:
                return GetEndpointStatusResponse(
                    error_message=f"Error: Cannot parse endpoint status: {e}",
                    error_code=500
                )

            # If the compute resource is not ready (if node not acquired, worker_init not completed, or lost managers) ...
            if not resources_ready:

                # If a user already triggered the model (model currently loading) ...
                cache_key = f"endpoint_triggered:{self.endpoint_slug}"
                if is_cached(cache_key, create_empty=False):
                    
                    # Send an error to avoid overloading the Globus Compute endpoint
                    # This also reduces memory footprint on the API application
                    error_message = f"Error: Endpoint {self.endpoint_slug} online but not ready to receive tasks. "
                    error_message += "Please try again later."
                    return GetEndpointStatusResponse(
                        error_message=error_message,
                        error_code=503
                    )

        # Return endpoint status
        return GetEndpointStatusResponse(
            status=endpoint_status
        )


    # Submit task
    async def submit_task(self, data: dict) -> SubmitTaskResponse:
        """Submits a single interactive task to the compute resource."""

        # Get Globus Compute client and executor
        try:
            gcc = globus_utils.get_compute_client_from_globus_app()
            gce = globus_utils.get_compute_executor(client=gcc)
        except Exception as e:
            return SubmitTaskResponse(
                error_message=str(e),
                error_code=500
            )

        # Check endpoint status
        response = await self.get_endpoint_status(gcc=gcc, check_managers=self.check_managers)
        if response.error_message:
            return SubmitTaskResponse(
                error_code=response.error_code,
                error_message=response.error_message
            )

        # Add API port to the input data
        try:
            data["model_params"]["api_port"] = self.config.api_port
        except Exception as e:
            remove_endpoint_from_cache(self.endpoint_slug)
            return SubmitTaskResponse(
                error_message=f"Error: Could not process endpoint data for {self.endpoint_slug}: {e}",
                error_code=400
            )

        # Submit Globus Compute task and wait for the result
        result, task_id, error_message, error_code = await self._submit_and_get_result(gce, data)
        if len(error_message) > 0:
            return SubmitTaskResponse(
                error_message=error_message,
                error_code=error_code
            )

        # Return the successful result
        return SubmitTaskResponse(
            result=result,
            task_id=task_id
        )
    

    # Call to Globus Utils submit_and_get_result function
    # This is a self-contained function that can be re-defined by the Globus multi-user endpoint class
    async def _submit_and_get_result(self, gce, data):

        # Remove any residual multi-user endpoint configuration from cache
        gce.user_endpoint_config = {}

        # Submit task and wait for result
        return await globus_utils.submit_and_get_result(
            gce, self.config.endpoint_uuid, self.config.function_uuid, data=data, endpoint_slug=self.endpoint_slug
        )
    

    # Submit streaming task
    async def submit_streaming_task(self, data: dict, request_log_id: str) -> SubmitStreamingTaskResponse:
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
                error_message=f"Error: Could not process endpoint data for {self.endpoint_slug}: {e}",
                error_code=400
            )
        
        # Submit task to Globus Compute (same logic as non-streaming)
        try:

            # Assign endpoint UUID to the executor (same as submit_and_get_result)
            try:
                gcc = globus_utils.get_compute_client_from_globus_app()
                gce = globus_utils.get_compute_executor(client=gcc)
            except Exception as e:
                return SubmitStreamingTaskResponse(
                    error_message=str(e),
                    error_code=500
                )
            gce.endpoint_id = self.config.endpoint_uuid

            # Check endpoint status
            response = await self.get_endpoint_status(gcc=gcc, check_managers=self.check_managers)
            if response.error_message:
                return SubmitStreamingTaskResponse(
                    error_message=response.error_message,
                    error_code=response.error_code
                )
            
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
                error_message=f"Error: Could not submit streaming task: {e}",
                error_code=500
            )
        
        # Cache the endpoint slug to tell the application that a user already submitted a request to this endpoint
        cache_key = f"endpoint_triggered:{self.endpoint_slug}"
        ttl = 600 # 10 minutes
        try:
            cache.set(cache_key, True, ttl)
        except Exception as e:
            log.warning(f"Failed to cache endpoint_triggered:{self.endpoint_slug}: {e}")
        
        # Start background processing for metrics collection (fire and forget)
        asyncio.create_task(process_streaming_completion_async(
            task_uuid, stream_task_id, request_log_id, future, streaming_start_time,
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
        response = StreamingHttpResponse(streaming_content=sse_generator(), content_type='text/event-stream')
        
        # Set headers for SSE using utility function
        headers = create_streaming_response_headers()
        for key, value in headers.items():
            response[key] = value

        # Return response with StreamingHttpResponse object
        return SubmitStreamingTaskResponse(
            response=response,
            task_id=task_uuid
        )
    
    
    # Enable batch support
    def has_batch_enabled(self) -> bool:
        """Return True if batch can be used for this endpoint, False otherwise."""
        return (self.config.batch_endpoint_uuid is not None) and (self.config.batch_function_uuid is not None)
    

    # Submit batch
    async def submit_batch(self, batch_data: dict, username: str) -> SubmitBatchResponse:
        """Submits a batch job to the compute resource."""

        # Get Globus Compute client (using the endpoint identity)
        try:
            gcc = globus_utils.get_compute_client_from_globus_app()
        except Exception as e:
            return SubmitBatchResponse(
                error_message=f"Error: Could not get the Globus Compute client: {e}", 
                error_code=500
            )

        # Check endpoint status
        response = await self.get_endpoint_status(gcc=gcc, check_managers=self.check_managers, for_batch=True)
        if response.error_message:
            return SubmitBatchResponse(
                error_message=response.error_message,
                error_code=response.error_code
            )
        
        # Prepare input parameter for the compute tasks
        # NOTE: This is already in list format in case we submit multiple tasks per batch
        batch_id = str(uuid.uuid4())
        params_list = [
            {
                "model_params": {
                    "input_file": batch_data["input_file"],
                    "model": batch_data["model"]
                },
                "batch_id": batch_id,
                "username": username
            }
        ]
        if "output_folder_path" in batch_data:
            params_list[0]["model_params"]["output_folder_path"] = batch_data["output_folder_path"]

        # Prepare the batch job
        try:
            batch = gcc.create_batch()
            for params in params_list:
                batch.add(function_id=self.config.batch_function_uuid, args=[params])
        except Exception as e:
            return SubmitBatchResponse(
                error_message=f"Error: Could not create Globus Compute batch: {e}",
                error_code=500
            )

        # Submit batch to Globus Compute and update batch status if submission is successful
        try:
            batch_response = gcc.batch_run(endpoint_id=self.config.batch_endpoint_uuid, batch=batch)
        except Exception as e:
            return SubmitBatchResponse(
                error_message=f"Error: Could not submit the Globus Compute batch: {e}",
                error_code=500
            )
        
        # Extract the Globus batch UUID from submission
        # Temporary: globus_batch_uuid not used
        try:
            globus_batch_uuid = batch_response["request_id"]
        except Exception as e:
            return SubmitBatchResponse(
                batch_id=batch_id,
                error_message=f"Error: Batch submitted but no batch UUID recovered: {e}",
                error_code=500
            )

        # Extract the batch and task UUIDs from submission
        try:
            globus_task_uuids = ""
            for _, task_uuids in batch_response["tasks"].items():
                globus_task_uuids += ",".join(task_uuids) + ","
            globus_task_uuids = globus_task_uuids[:-1]
        except Exception as e:
            return SubmitBatchResponse(
                batch_id=batch_id,
                error_message=f"Error: Batch submitted but no task UUID recovered: {e}",
                error_code=500
            )
        
        # Return success response with batch ID
        return SubmitBatchResponse(
            batch_id=batch_id,
            task_ids=globus_task_uuids,
            status=BatchStatusEnum.pending.value
        )


    # Get batch status
    async def get_batch_status(self, batch: BatchLog) -> GetBatchStatusResponse:
        """Get the status and results of a batch job."""

        # Get the Globus batch status response
        status_response, error_message, error_code = globus_utils.get_batch_status(batch.task_ids)

        # If there is an error when recovering Globus tasks status/results ...
        if len(error_message) > 0:

            # Mark the batch as failed if the function execution failed
            if "TaskExecutionFailed" in error_message:
                return GetBatchStatusResponse(
                    status=BatchStatusEnum.failed.value,
                    result=error_message
                )
                
            # Return error message if something else occured
            return GetBatchStatusResponse(
                error_message=error_message, 
                error_code=error_code
            )
        
        # Parse Globus batch status response
        try:
            status_response_values = list(status_response.values())
            pending_list = [status["pending"] for status in status_response_values]
            status_list = [status["status"] for status in status_response_values]
        except Exception as e:
            return GetBatchStatusResponse(
                error_message=f"Error: Could not parse get_batch_status response for status: {e}",
                error_code=500
            )
        
        # Collect latest batch status
        try:
            if pending_list.count(True) > 0:
                latest_batch_status = BatchStatusEnum.pending.value
            elif status_list.count("success") == len(status_list):
                latest_batch_status = BatchStatusEnum.completed.value
            else:
                latest_batch_status = BatchStatusEnum.failed.value
        except Exception as e:
            return GetBatchStatusResponse(
                error_message=f"Error: Could not define batch status: {e}",
                error_code=500
            )
        
        # If batch result is available ...
        batch_result = None
        if latest_batch_status == BatchStatusEnum.completed.value:

            # Parse Globus batch status response to extract result
            try:
                result_list = [status["result"] for status in status_response_values]
                batch_result = ",".join(result_list) + ","
                batch_result = batch_result[:-1]
            except Exception as e:
                return GetBatchStatusResponse(
                    error_message=f"Error: Could not parse get_batch_status response for result: {e}",
                    error_code=500
                )

        # Return latest batch status and result
        return GetBatchStatusResponse(
            status=latest_batch_status,
            result=batch_result
        )


    # Read-only access to the configuration
    @property
    def config(self):
        return self.__config