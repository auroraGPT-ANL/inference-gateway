import globus_compute_sdk

# Function to kill a PBS job based on it's job ID
def qdel_inference_function(PBS_job_id=None):
    import subprocess

    # Make sure the job ID is an integer
    if not isinstance(PBS_job_id, int):
        raise RuntimeError(f"Command failed: PBS_jod_id must be an integer.")
    
    # Run the qdel command
    cmd = f"qdel {PBS_job_id}"
    result = subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    
    # Return successful qdel command exit code (should be 0)
    return result.returncode

# Creating Globus Compute client
gcc = globus_compute_sdk.Client()

# # Register the function
COMPUTE_FUNCTION_ID = gcc.register_function(qdel_inference_function)

# # Write function UUID in a file
uuid_file_name = "qdel_register_function_sophia.txt"
with open(uuid_file_name, "w") as file:
    file.write(COMPUTE_FUNCTION_ID)
    file.write("\n")
file.close()

# # End of script
print("Function registered with UUID -", COMPUTE_FUNCTION_ID)
print("The UUID is stored in " + uuid_file_name + ".")
print("")