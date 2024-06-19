# Import packages
import globus_compute_sdk

# Define Globus Compute function
def vllm_inference_function(parameters, **kwargs):
    import socket
    import os
    import time
    from openai import OpenAI  
    import json
    import sys
    class CustomEncoder(json.JSONEncoder):
        def default(self, obj):
            if hasattr(obj, '__dict__'):
                return obj.__dict__
            elif isinstance(obj, set):
                return list(obj)  # Convert sets to lists
            return json.JSONEncoder.default(self, obj)
    # Determine the hostname
    hostname = socket.gethostname()
    os.environ['no_proxy'] = "localhost,{hostname}".format(hostname=hostname)
    # Construct the base_url
    base_url = f"http://{hostname}:8000/v1"
    # Get the API key from environment variable
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        api_key = "random_api_key"
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
    )
    print("parameters", parameters)
    start_time = time.time()
    completion = client.chat.completions.create(
        **parameters['model_params']
    )
    end_time = time.time()
    response_time = end_time - start_time        
    response_data = json.dumps(completion, cls=CustomEncoder, indent=4)
    print(response_data)
    response_size = sys.getsizeof(response_data)
    metrics = {
        'response_time': response_time,
        'response_size_bytes': response_size,
        'api_usage': {
            'completion_tokens': getattr(completion, 'usage', {}).get('completion_tokens', 0),
            'prompt_tokens': getattr(completion, 'usage', {}).get('prompt_tokens', 0),
            'total_tokens': getattr(completion, 'usage', {}).get('total_tokens', 0)
        }
    }
    # Combine the API response and metrics
    output = {**json.loads(response_data), **metrics}
    json_output = json.dumps(output, indent=4)
    # Use custom encoder to serialize the response
    return json_output

# Creating Globus Compute client
gcc = globus_compute_sdk.Client()

# Register the function
COMPUTE_FUNCTION_ID = gcc.register_function(vllm_inference_function)

# Write function UUID in a file
uuid_file_name = "vllm_function_uuid.txt"
with open(uuid_file_name, "w") as file:
    file.write(COMPUTE_FUNCTION_ID)
    file.write("\n")
file.close()

# End of script
print("Function registered with UUID -", COMPUTE_FUNCTION_ID)
print("The UUID is stored in " + uuid_file_name + ".")
print("")

## Example call
#vllm_inference_function({'model_params': {'model': 'meta-llama/Meta-Llama-3-8B-Instruct', 'temperature': 0.2, 'max_tokens': 150, "messages":[{"role": "user", "content": "List all proteins that interact with RAD51"}],'logprobs':True}})