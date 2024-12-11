import globus_compute_sdk
import random

def infinity_inference_function(parameters):
    import socket
    import os
    import time
    import requests
    import json

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
    
    base_url = f"http://127.0.0.1:{api_port}/"
    url = base_url + openai_endpoint

    # Prepare the payload
    payload = parameters['model_params'].copy()
    
    start_time = time.time()
    
    # Make the POST request
    response = requests.post(url, headers=headers, json=payload, verify=False)
    
    end_time = time.time()
    response_time = end_time - start_time

    # Check if the request was successful
    if response.status_code == 200:
        completion = response.json()
    else:
        raise Exception(f"API request failed with status code: {response.status_code} {response}")

    # Extract usage information
    usage = completion.get('usage', {})
    total_num_tokens = usage.get('total_tokens', 0)

    throughput = total_num_tokens / response_time if response_time > 0 else 0

    metrics = {
        'response_time': response_time,
        'throughput_tokens_per_second': throughput
    }

    # Combine the API response and metrics
    output = {**completion, **metrics}
    json_output = json.dumps(output, indent=4)

    return json_output

# Creating Globus Compute client
gcc = globus_compute_sdk.Client()

# # Register the function
COMPUTE_FUNCTION_ID = gcc.register_function(infinity_inference_function)

# # Write function UUID in a file
uuid_file_name = "infinity_register_function_sophia_multiple_models.txt"
with open(uuid_file_name, "w") as file:
    file.write(COMPUTE_FUNCTION_ID)
    file.write("\n")
file.close()

# # End of script
print("Function registered with UUID -", COMPUTE_FUNCTION_ID)
print("The UUID is stored in " + uuid_file_name + ".")
print("")