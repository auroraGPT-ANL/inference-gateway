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

    def extract_models_info_from_file(file_path, job_dict):
        """
        This function now extracts model_name(s), framework, and cluster from the file.
        Returns a dict with keys: 'models', 'framework', 'cluster'.
        """
        models_str = 'N/A'
        framework_str = 'N/A'
        cluster_str = 'N/A'
        if not os.path.exists(file_path):
            # We’ll return a dict with N/A if file doesn’t exist
            job_dict["Models"] = models_str
            job_dict["Framework"] = framework_str
            job_dict["Cluster"] = cluster_str
            return job_dict
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # Extract all model_name= lines
        model_pattern = re.compile(r'model_name\S*\s*=\s*"([^"]+)"')
        all_models = model_pattern.findall(content)
        models_str = ",".join(all_models) if all_models else "N/A"

        # Extract framework=
        framework_pattern = re.compile(r'framework\s*=\s*"([^"]+)"')
        found_framework = framework_pattern.findall(content)
        framework_str = found_framework[0] if found_framework else "N/A"

        # Extract cluster=
        cluster_pattern = re.compile(r'cluster\s*=\s*"([^"]+)"')
        found_cluster = cluster_pattern.findall(content)
        cluster_str = found_cluster[0] if found_cluster else "N/A"

        job_dict["Models"] = models_str
        job_dict["Framework"] = framework_str
        job_dict["Cluster"] = cluster_str
        return job_dict

    def determine_model_status(submit_path, job_dict):
        """
        Determine model_status by checking submit_path + '.stdout' file.
        If file does not exist or line not found, model_status = 'starting'
        If line "All models started successfully." is found, model_status = 'running'
        """
        out_file = submit_path + ".stdout"
        if not os.path.exists(out_file):
            job_dict["Model Status"] = "starting"
            return job_dict

        with open(out_file, 'r', encoding='utf-8') as f:
            for line in f:
                if "All models started successfully." in line:
                    job_dict["Model Status"] = "running"
                    return job_dict
        job_dict["Model Status"] = "starting"
        return job_dict
    
    def determine_batch_job_status(job_id, job_dict):
        home_dir = os.path.expanduser('~')
        batch_jobs_path = os.path.join(home_dir, "batch_jobs")
        # Get all files in the batch_jobs directory, sorted by modification time with the latest file first
        batch_jobs_files = os.listdir(batch_jobs_path)
        batch_jobs_files.sort(key=lambda x: os.path.getmtime(os.path.join(batch_jobs_path, x)), reverse=True)
        job_dict["Model Status"] = "starting"
        # Check if any file name contains the job id from batch_jobs_files
        for file in batch_jobs_files:
            if job_id in file:
                # split the file name by underscore and fetch model_name, batch_id, username, pbs_job_id
                model_name, batch_id, username, pbs_job_id = file.split("_")
                job_dict["Models"] = model_name
                job_dict["Batch ID"] = batch_id
                job_dict["Username"] = username
                job_dict["Model Status"] = "running"
                return job_dict
        return job_dict
    
    def common_job_attributes(attributes, job_dict, job_id, job_state):
        job_dict["Job ID"] = job_id
        job_dict["Job State"] = job_state
        job_dict["Host Name"] = attributes.get('exec_host', 'N/A')
        job_dict["Job Comments"] = attributes.get('comment', 'N/A')
        job_dict["Nodes Reserved"] = attributes.get('Resource_List.nodect', 'N/A')
        walltime = attributes.get('resources_used.walltime', 'N/A')
        if walltime != 'N/A':
            job_dict["Walltime"] = walltime
        estimated_start = attributes.get('estimated.start_time', 'N/A')
        if estimated_start != 'N/A':
            estimated_start += " (Chicago time)"
            job_dict["Estimated Start Time"] = estimated_start
        return job_dict
    
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
        private_batch_running = []
        private_batch_queued = []

        for job_id in job_ids:
            job_dict = {}

            full_info = run_command(f"TZ='America/Chicago' qstat -xf {job_id}")
            attributes = parse_qstat_xf_output(full_info)
            job_state = attributes.get('job_state', 'N/A')
            submit_path = extract_submit_path(attributes.get('Submit_arguments', ''))

            if submit_path:
                job_dict = extract_models_info_from_file(submit_path, job_dict)
            if job_state == 'R':
                if "batch_job" in job_dict["Models"]:
                    job_dict = determine_batch_job_status(job_id, job_dict)  
                    job_dict = common_job_attributes(attributes, job_dict, job_id, job_state)
                    private_batch_running.append(job_dict)
                else:
                    job_dict = determine_model_status(submit_path, job_dict)
                    job_dict = common_job_attributes(attributes, job_dict, job_id, job_state)
                    running_jobs.append(job_dict)
            elif job_state == 'Q':
                job_dict["Model Status"] = 'queued'
                if "batch_job" in job_dict["Models"]:
                    job_dict = common_job_attributes(attributes, job_dict, job_id, job_state)
                    private_batch_queued.append(job_dict)
                else:
                    job_dict = common_job_attributes(attributes, job_dict, job_id, job_state)
                    queued_jobs.append(job_dict)
            else:
                job_dict["Model Status"] = 'other'
                job_dict = common_job_attributes(attributes, job_dict)
                other_jobs.append(job_dict)
        # Create the final JSON structure
        final_output = {
            "running": running_jobs,
            "queued": queued_jobs,
            "others": other_jobs,
            "private-batch-running": private_batch_running,
            "private-batch-queued": private_batch_queued
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