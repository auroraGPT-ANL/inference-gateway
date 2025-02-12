# Import packages
import globus_compute_sdk

# Function to print environment variable
def print_variables():
    import os
    import sys
    return f"VARIABLE_TEST equals --> {os.environ.get('VARIABLE_TEST')}, python -- {sys.executable}"

# Creating Globus Compute client
gcc = globus_compute_sdk.Client()

# Register the function
COMPUTE_FUNCTION_ID = gcc.register_function(print_variables, public=False)

# Write function UUID in a file
print(COMPUTE_FUNCTION_ID+"\n")
uuid_file_name = "print_variables_uuid.txt"
with open(uuid_file_name, "w") as file:
    file.write(COMPUTE_FUNCTION_ID)
    file.write("\n")