# Import packages
import globus_compute_sdk

# Define Globus Compute function
def llamacpp_inference (parameters, **kwargs):
    import socket
    import json
    import os
    import time
    import requests

    # Determine the hostname
    hostname = socket.gethostname()
    os.environ['no_proxy'] = "localhost,{hostname}".format(hostname=hostname)
    # Construct the base_url
    base_url = f"http://localhost:8080/completion"
    start_time = time.time()
    print("parameters",parameters)
    response = requests.post(base_url, data=json.dumps(parameters['model_params']))
    end_time = time.time()
    response_time = end_time - start_time        

    # Convert the response to a JSON-formatted string
    json_response = response.json()
    json_response['response_time'] = response_time

    # Print the JSON response for debugging
    print(json_response)
    return json_response

llamacpp_inference({'model_params': {'model': 'meta-llama3-70b-instruct', 'temperature': 0.2, 'max_tokens': 150, 'prompt': 'List all proteins that interact with RAD51'}})