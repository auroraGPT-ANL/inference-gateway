import globus_compute_sdk
import requests

def vllm_inference_function(parameters):
    import socket
    import os
    import time
    import requests
    import json
    from requests.exceptions import RequestException

    def handle_non_streaming_request(url, headers, payload, start_time):
        """Handle non-streaming requests (original logic)"""
        # Make the POST request
        response = requests.post(url, headers=headers, json=payload, verify=False)

        end_time = time.time()
        response_time = end_time - start_time

        # Initialize metrics
        metrics = {
            'response_time': response_time,
            'throughput_tokens_per_second': 0
        }

        # Handle different response scenarios
        if response.status_code == 200:
            try:
                # Try to parse JSON response
                completion = response.json()
                
                # Extract usage information if available
                usage = completion.get('usage', {})
                total_num_tokens = usage.get('total_tokens', 0)
                metrics['throughput_tokens_per_second'] = total_num_tokens / response_time if response_time > 0 else 0
                
                # Return the response even if empty
                output = {**completion, **metrics}
                return json.dumps(output, indent=4)
            except json.JSONDecodeError:
                # If response is not JSON but status is 200, return the raw text
                return json.dumps({
                    'completion': response.text,
                    **metrics
                }, indent=4)
        else:
            # For non-200 responses, raise an exception with detailed error information
            error_msg = f"API request failed with status code: {response.status_code}\n"
            error_msg += f"Response text: {response.text}\n"
            error_msg += f"Response headers: {dict(response.headers)}"
            raise Exception(error_msg)

    def send_data_to_streaming_server(host, port, protocol, task_id, data):
        """Send streaming data to streaming server via HTTP with proxy support and connection reuse"""
        try:
            url = f"{protocol}://{host}:{port}/resource_server/api/streaming/data/"
            payload = {
                "task_id": task_id,
                "data": data,
                "type": "data"
            }
            
            # Use proxy configuration from environment
            proxies = {}
            if os.environ.get('http_proxy'):
                proxies['http'] = os.environ.get('http_proxy')
            if os.environ.get('https_proxy'):
                proxies['https'] = os.environ.get('https_proxy')
            
            # Use session for connection reuse and reduce timeout
            if not hasattr(send_data_to_streaming_server, 'session'):
                send_data_to_streaming_server.session = requests.Session()
                send_data_to_streaming_server.session.proxies.update(proxies)
            
            # Add internal secret for authentication
            headers = {'X-Internal-Secret': os.environ.get('INTERNAL_STREAMING_SECRET', 'default-secret-change-me')}
            response = send_data_to_streaming_server.session.post(url, json=payload, headers=headers, timeout=2, verify=False)
            
            if response.status_code == 200:
                return True
            else:
                return False
                
        except Exception as e:
            return False

    def send_error_to_streaming_server(host, port, protocol, task_id, error):
        """Send error to streaming server via HTTP with proxy support"""
        try:
            url = f"{protocol}://{host}:{port}/resource_server/api/streaming/error/"
            payload = {
                "task_id": task_id,
                "error": error,
                "type": "error"
            }
            
            # Use proxy configuration from environment
            proxies = {}
            if os.environ.get('http_proxy'):
                proxies['http'] = os.environ.get('http_proxy')
            if os.environ.get('https_proxy'):
                proxies['https'] = os.environ.get('https_proxy')
            
            # Add internal secret for authentication
            headers = {'X-Internal-Secret': os.environ.get('INTERNAL_STREAMING_SECRET', 'default-secret-change-me')}
            response = requests.post(url, json=payload, headers=headers, timeout=5, proxies=proxies, verify=False)
            
            if response.status_code == 200:
                return True
            else:
                return False
                
        except Exception as e:
            return False

    def send_done_to_streaming_server(host, port, protocol, task_id):
        """Send completion to streaming server via HTTP with proxy support"""
        try:
            url = f"{protocol}://{host}:{port}/resource_server/api/streaming/done/"
            payload = {
                "task_id": task_id,
                "type": "done"
            }
            
            # Use proxy configuration from environment
            proxies = {}
            if os.environ.get('http_proxy'):
                proxies['http'] = os.environ.get('http_proxy')
            if os.environ.get('https_proxy'):
                proxies['https'] = os.environ.get('https_proxy')
            
            # Add internal secret for authentication
            headers = {'X-Internal-Secret': os.environ.get('INTERNAL_STREAMING_SECRET', 'default-secret-change-me')}
            response = requests.post(url, json=payload, headers=headers, timeout=5, proxies=proxies, verify=False)
            
            if response.status_code == 200:
                return True
            else:
                return False
                
        except Exception as e:
            return False

    def handle_streaming_request(url, headers, payload, start_time):
        """Handle streaming requests with real-time chunk streaming to streaming server"""
        # Get streaming server details from payload
        stream_server_host = payload.get('streaming_server_host')
        stream_server_port = payload.get('streaming_server_port')
        stream_server_protocol = payload.get('streaming_server_protocol', 'https')
        stream_task_id = payload.get('stream_task_id')
        
        if not all([stream_server_host, stream_server_port, stream_task_id]):
            raise Exception("Streaming requires streaming_server_host, streaming_server_port, and stream_task_id in payload")
        
        # Create clean payload for vLLM (remove streaming-specific parameters)
        vllm_payload = payload.copy()
        vllm_payload.pop('streaming_server_host', None)
        vllm_payload.pop('streaming_server_port', None)
        vllm_payload.pop('streaming_server_protocol', None)
        vllm_payload.pop('stream_task_id', None)
        
        try:
            # Make streaming request to vLLM with clean payload
            response = requests.post(url, headers=headers, json=vllm_payload, stream=True, verify=False)
            
            if response.status_code != 200:
                error_msg = f"API request failed with status code: {response.status_code}\nResponse text: {response.text}"
                # Send error to streaming server
                send_error_to_streaming_server(stream_server_host, stream_server_port, stream_server_protocol, stream_task_id, error_msg)
                raise Exception(error_msg)
            
            # Stream chunks in batched mode to streaming server
            streaming_chunks = []
            total_tokens = 0
            chunks_sent = 0
            
            # Aggressive batching variables
            batch_buffer = []
            batch_size = 20  # Send every 20 chunks (much more aggressive)
            batch_timeout = 0.5  # Or every 500ms, whichever comes first
            last_send_time = time.time()
            
            # Process chunks as they arrive and send to streaming server
            for chunk in response.iter_lines():
                if chunk:
                    chunk_data = chunk.decode('utf-8')
                    
                    # Send raw chunk data as-is to streaming server (no processing)
                    if chunk_data.strip() == 'data: [DONE]':
                        # Send any remaining batched chunks
                        if batch_buffer:
                            batch_data = '\n'.join(batch_buffer)
                            send_data_to_streaming_server(stream_server_host, stream_server_port, stream_server_protocol, stream_task_id, batch_data)
                            chunks_sent += len(batch_buffer)
                        
                        # Send completion to streaming server
                        send_done_to_streaming_server(stream_server_host, stream_server_port, stream_server_protocol, stream_task_id)
                        break
                    elif chunk_data.strip():
                        # Store raw chunk for metrics
                        streaming_chunks.append(chunk_data)
                        batch_buffer.append(chunk_data)
                        
                        # Send batch when buffer is full or timeout reached
                        current_time = time.time()
                        if len(batch_buffer) >= batch_size or (current_time - last_send_time) >= batch_timeout:
                            batch_data = '\n'.join(batch_buffer)
                            success = send_data_to_streaming_server(stream_server_host, stream_server_port, stream_server_protocol, stream_task_id, batch_data)
                            if success:
                                chunks_sent += len(batch_buffer)
                            batch_buffer = []
                            last_send_time = current_time
                        
                        # Parse for metrics only (not for content extraction)
                        try:
                            parsed_chunk = json.loads(chunk_data)
                            # Extract token usage if available
                            if 'usage' in parsed_chunk:
                                usage = parsed_chunk['usage']
                                if 'total_tokens' in usage:
                                    total_tokens = usage['total_tokens']
                        except json.JSONDecodeError:
                            pass  # Skip chunks that can't be parsed
            
            # Calculate metrics
            end_time = time.time()
            response_time = end_time - start_time
            
            # Calculate throughput (tokens per second)
            throughput_tokens_per_second = total_tokens / response_time if response_time > 0 else 0
            
            # Return streaming result for Globus Compute
            return json.dumps({
                "streaming": True,
                "task_id": stream_task_id,
                "response_time": response_time,
                "throughput_tokens_per_second": throughput_tokens_per_second,
                "total_tokens": total_tokens,
                "status": "completed",
                "chunks": streaming_chunks,
                "total_chunks": len(streaming_chunks),
                "chunks_sent_to_server": chunks_sent
            })
            
        except Exception as e:
            # Send error to streaming server
            try:
                send_error_to_streaming_server(stream_server_host, stream_server_port, stream_server_protocol, stream_task_id, str(e))
            except:
                pass
            
            # Calculate error metrics
            end_time = time.time()
            response_time = end_time - start_time
            
            # Return error result
            return json.dumps({
                "streaming": True,
                "task_id": stream_task_id,
                "response_time": response_time,
                "throughput_tokens_per_second": 0,
                "total_tokens": 0,
                "status": "error",
                "error": str(e)
            })

    try:
        # Determine the hostname
        hostname = socket.gethostname()
        os.environ['no_proxy'] = f"localhost,{hostname},127.0.0.1"

        # Get the API key from environment variable
        api_key = os.getenv("OPENAI_API_KEY", "random_api_key")

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        print("parameters", parameters)

        # Determine the endpoint based on the URL parameter
        openai_endpoint = parameters['model_params'].pop('openai_endpoint')

        # Determine the port based on the URL parameter
        api_port = parameters['model_params'].pop('api_port')

        # Check if streaming is requested
        stream = parameters['model_params'].get('stream', False)
        
        base_url = f"https://127.0.0.1:{api_port}/v1/"
        url = base_url + openai_endpoint

        # Prepare the payload
        payload = parameters['model_params'].copy()

        start_time = time.time()

        if stream:
            # Handle streaming request
            return handle_streaming_request(url, headers, payload, start_time)
        else:
            # Handle non-streaming request (original logic)
            return handle_non_streaming_request(url, headers, payload, start_time)

    except RequestException as e:
        # Handle network-related errors
        error_msg = f"Network error occurred: {str(e)}"
        if 'start_time' in locals():
            error_msg += f"\nResponse time: {time.time() - start_time}"
        raise Exception(error_msg)
    except Exception as e:
        # Handle any other unexpected errors
        error_msg = f"Unexpected error of type {type(e).__name__}: {str(e)}"
        if 'start_time' in locals():
            error_msg += f"\nResponse time: {time.time() - start_time}"
        raise Exception(error_msg)

# Creating Globus Compute client
gcc = globus_compute_sdk.Client()

# # Register the function
COMPUTE_FUNCTION_ID = gcc.register_function(vllm_inference_function)

# # Write function UUID in a file
uuid_file_name = "vllm_register_function_sophia_streaming.txt"
with open(uuid_file_name, "w") as file:
    file.write(COMPUTE_FUNCTION_ID)
    file.write("\n")
file.close()

# # End of script
print("Function registered with UUID -", COMPUTE_FUNCTION_ID)
print("The UUID is stored in " + uuid_file_name + ".")
print("")

# Example calls

# List of sample prompts
# prompts = [
#         "Explain the concept of machine learning in simple terms.",
#         "What are the main differences between Python and JavaScript?",
#         "Write a short story about a robot learning to paint.",
#         "Describe the process of photosynthesis.",
#         "What are the key features of a good user interface design?"
# ]


# # # Chat completion example
# chat_out = vllm_inference_function({
#     'model_params': {
#         'openai_endpoint': 'chat/completions',
#         'model': 'meta-llama/Meta-Llama-3-8B-Instruct',
#         'api_port': 8001,
#         "messages": [{"role": "user", "content": random.choice(prompts)}],
#         'logprobs': True
#     }
# })
# print("Chat Completion Output for meta-llama/Meta-Llama-3-8B-Instruct")
# print(chat_out)



# # # Chat completion example
# chat_out = vllm_inference_function({
#     'model_params': {
#         'openai_endpoint': 'chat/completions',
#         'model': 'meta-llama/Meta-Llama-3-70B-Instruct',
#         'api_port': 8000,
#         "messages": [{"role": "user", "content": random.choice(prompts)}],
#         "messages": [{"role": "user", "content": random.choice(prompts)}],
#         'logprobs': True
#     }
# })
# print("Chat Completion Output for meta-llama/Meta-Llama-3-70B-Instruct")
# print(chat_out)

# # # Chat completion example
# chat_out = vllm_inference_function({
#     'model_params': {
#         'openai_endpoint': 'chat/completions',
#     'model': 'mistralai/Mistral-7B-Instruct-v0.3',
#         'api_port': 8002,
#         "messages": [{"role": "user", "content": random.choice(prompts)}],
#         'logprobs': True
#     }
# })
# print("Chat Completion Output for meta-llama/Meta-Llama-3-8B-Instruct")
# print(chat_out)

# # Text completion example
# text_out = vllm_inference_function({
#     'model_params': {
#         'openai_endpoint': 'completions',
#         'model': 'meta-llama/Meta-Llama-3-8B-Instruct',
#         'temperature': 0.2,
#         'max_tokens': 150,
#         'prompt': "List all proteins that interact with RAD51",
#         'logprobs': True
#     }
# })
# print("\\nText Completion Output:")
# print(text_out) 