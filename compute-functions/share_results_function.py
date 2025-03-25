import globus_compute_sdk

# Share batch results function
def share_batch_results(parameters):
    """Share batch results via a Guest collection with the user who requested it."""

    from dotenv import load_dotenv
    from globus_sdk import TransferClient, ClientApp
    from uuid import UUID
    from pydantic import BaseModel, ConfigDict
    import json
    import os

    # Create input parameter validation class
    class Param(BaseModel):
        user_id: str
        username: str
        result: str
        model_config = ConfigDict(extra='forbid')

    # Validate parameters
    parameters = Param(**parameters)
    _ = UUID(parameters.user_id).version

    # Extract results_file from inference result string
    results_file = json.loads(parameters.result)["results_file"]

    # Define constants
    USER_FOLDER = f"/batch_results/{parameters.username}/"
    RESULTS_FOLDER = results_file.split(USER_FOLDER)[1].split("/")[0]
    GUEST_COLLECTION_ID = "5d64d93a-1293-4dae-a8c6-39d51daf2dd3" # InferenceUpload - Eagle

    # Load Globus application credentials to manage the Guest collection
    load_dotenv(dotenv_path="/home/openinference_svc/.env", override=True)
    CLIENT_ID = os.getenv("GLOBUS_COMPUTE_CLIENT_ID")
    CLIENT_SECRET = os.getenv("GLOBUS_COMPUTE_CLIENT_SECRET")

    # Create authenticated Transfer client
    client_app = ClientApp("transfer-client", client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    transfer_client = TransferClient(app=client_app)

    # If the user identity does not have permission to the folder yet ...
    access_list = transfer_client.endpoint_acl_list(GUEST_COLLECTION_ID)
    if parameters.user_id not in [data["principal"] for data in access_list.data["DATA"]]:
    
        # Define the read-only permission rule for the user
        rule_data = {
            "DATA_TYPE": "access",
            "principal_type": "identity",
            "principal": parameters.user_id,
            "path": USER_FOLDER,
            "permissions": "r",
        }

        # Add the permission to the Guest collection
        _ = transfer_client.add_endpoint_acl_rule(GUEST_COLLECTION_ID, rule_data)

    # Create link to point users to results through the Globus webapp
    data_access = ""
    data_access += "https://app.globus.org/file-manager?"
    data_access += f"origin_id={GUEST_COLLECTION_ID}&"
    data_access += f"origin_id={GUEST_COLLECTION_ID}&"
    data_access += f"origin_path={os.path.join(USER_FOLDER, RESULTS_FOLDER)}&"
    data_access += "two_pane=true"

    # Return link to access data
    return {"data_access": data_access}

# Creating Globus Compute client
gcc = globus_compute_sdk.Client()

# # Register the function
COMPUTE_FUNCTION_ID = gcc.register_function(share_batch_results)

# # Write function UUID in a file
uuid_file_name = "share_results_function.txt"
with open(uuid_file_name, "w") as file:
    file.write(COMPUTE_FUNCTION_ID)
    file.write("\n")
file.close()

# # End of script
print(f"Function registered with UUID - {COMPUTE_FUNCTION_ID}")
print(f"The UUID is stored in {uuid_file_name}.")
print("")