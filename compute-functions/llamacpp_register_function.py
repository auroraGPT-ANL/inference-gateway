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
    # Get the API key from environment variable
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        api_key="random_api_key"

    start_time = time.time()
    print("parameters",parameters)
    response = requests.post(base_url, data=json.dumps(**parameters['model_params']))
    end_time = time.time()
    response_time = end_time - start_time        

    # Convert the response to a JSON-formatted string
    json_response = response.json()
    json_response['response_time'] = response_time

    # Print the JSON response for debugging
    print(json_response)
    return json_response

# Creating Globus Compute client
gcc = globus_compute_sdk.Client()

# Register the function
COMPUTE_FUNCTION_ID = gcc.register_function(llamacpp_inference)

# Write function UUID in a file
uuid_file_name = "llama_cpp_function_uuid.txt"
with open(uuid_file_name, "w") as file:
    file.write(COMPUTE_FUNCTION_ID)
    file.write("\n")
file.close()

# End of script
print("Function registered with UUID -", COMPUTE_FUNCTION_ID)
print("The UUID is stored in " + uuid_file_name + ".")
print("")