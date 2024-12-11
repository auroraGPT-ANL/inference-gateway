import globus_compute_sdk

def qstat_inference_function():
    import os
    import re
    import json
    import subprocess


    def run_command(cmd):
        """Run a command and return its output as a list of lines."""
        result = subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
        return result.stdout.strip().split('\n')

    def parse_qstat_xf_output(lines):
        attributes = {}
        current_attr = None
        current_val_lines = []

        # Allow dots and other characters in attribute names.
        attr_line_pattern = re.compile(r'^\s*([A-Za-z0-9_\.\-]+)\s*=\s*(.*)$')

        for line in lines:
            match = attr_line_pattern.match(line)
            if match:
                # Store previous attribute
                if current_attr is not None:
                    attributes[current_attr] = "".join(current_val_lines).strip()
                current_attr = match.group(1)
                current_val = match.group(2)
                current_val_lines = [current_val.strip()]
            else:
                # Continuation line for the current attribute
                if current_attr is not None:
                    current_val_lines.append(line.strip())

        # Store the last attribute
        if current_attr is not None:
            attributes[current_attr] = "".join(current_val_lines).strip()

        return attributes

    def extract_submit_path(submit_args):
        # submit_args should now be a fully restored single line.
        parts = submit_args.split()
        if not parts:
            return None
        return parts[-1]

    def extract_models_from_file(file_path):
        if not os.path.exists(file_path):
            return "N/A"

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        pattern = re.compile(r'model_name\S*\s*=\s*"([^"]+)"')
        models = pattern.findall(content)
        
        if not models:
            return "N/A"

        return ",".join(models)
    
    def determine_model_status(submit_path):
        """
        Determine model_status by checking submit_path + '.stdout' file.
        If file does not exist or line not found, model_status = 'starting'
        If line "All models started successfully." is found, model_status = 'running'
        """
        out_file = submit_path + ".stdout"
        if not os.path.exists(out_file):
            return "starting"
        
        with open(out_file, 'r', encoding='utf-8') as f:
            for line in f:
                if "All models started successfully." in line:
                    return "running"
        return "starting"

    def run_qstat():
        user = os.environ.get('USER')
        if not user:
            raise RuntimeError("USER environment variable not set.")

        # Get all jobs for the user
        qstat_output = run_command(f"qstat -u {user}")

        job_pattern = re.compile(r'^(\d+\.\S+)')
        job_ids = []

        for line in qstat_output:
            m = job_pattern.match(line)
            if m:
                job_id = m.group(1)
                # Remove any trailing '*' from the job_id
                job_id = job_id.rstrip('*')
                job_ids.append(job_id)

        # We'll store results in lists keyed by state category
        running_jobs = []
        queued_jobs = []
        other_jobs = []

        for job_id in job_ids:
            full_info = run_command(f"TZ='America/Chicago' qstat -xf {job_id}")
            attributes = parse_qstat_xf_output(full_info)

            job_state = attributes.get('job_state', 'N/A')
            walltime = 'N/A'
            if job_state == 'R':
                walltime = attributes.get('resources_used.walltime', 'N/A')

            exec_host = attributes.get('exec_host', 'N/A')
            comment = attributes.get('comment', 'N/A')

            submit_args = attributes.get('Submit_arguments', '')
            submit_path = extract_submit_path(submit_args)

            models = "N/A"
            if submit_path:
                models = extract_models_from_file(submit_path)

            node_count = attributes.get('Resource_List.nodect', 'N/A')
             # Determine model status based on the .out file
            model_status = "starting"
            if submit_path:
                model_status = determine_model_status(submit_path)
            job_dict = {
                "Models Served": models,
                "Model Status": model_status,
                "Job ID": job_id,
                "Job State": job_state,
                "Walltime": walltime,
                "Host Name": exec_host,
                "Job Comments": comment,
                "Nodes Reserved": node_count
            }

            if job_state == 'R':
                running_jobs.append(job_dict)
            elif job_state == 'Q':
                estimated_start = attributes.get('estimated.start_time', 'N/A')
                if estimated_start != 'N/A':
                    estimated_start += " (Chicago time)"
                job_dict["estimated_start_time"] = estimated_start
                queued_jobs.append(job_dict)
            else:
                other_jobs.append(job_dict)

        # Create the final JSON structure
        final_output = {
            "running": running_jobs,
            "queued": queued_jobs,
            "others": other_jobs
        }

        # Print the JSON output
        return final_output

    output = run_qstat()
    json_output = json.dumps(output, indent=4)

    return json_output

# Creating Globus Compute client
gcc = globus_compute_sdk.Client()

# # Register the function
COMPUTE_FUNCTION_ID = gcc.register_function(qstat_inference_function)

# # Write function UUID in a file
uuid_file_name = "qstat_register_function_sophia.txt"
with open(uuid_file_name, "w") as file:
    file.write(COMPUTE_FUNCTION_ID)
    file.write("\n")
file.close()

# # End of script
print("Function registered with UUID -", COMPUTE_FUNCTION_ID)
print("The UUID is stored in " + uuid_file_name + ".")
print("")