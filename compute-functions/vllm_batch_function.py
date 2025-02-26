import globus_compute_sdk
import json
def chunked_vllm_inference_function(parameters):
    import os
    import subprocess
    import uuid
    import time
    import json

    def run_chunk_inference(lines_buffer, model_name, base_name, batch_id, final_output_file, token_pattern, chunk_index):
        """
        1) Write lines_buffer to a temp chunk file
        2) Call vLLM run_batch on that file (subprocess)
        3) Append chunk output to final_output_file
        4) Remove the chunk in/out file
        5) Return (tokens_in_this_chunk, responses_in_this_chunk)
        """
        import os
        import subprocess
        import uuid
        import time

        # Create a unique chunk ID
        unique_id = uuid.uuid4().hex[:6]
        chunk_id = f"chunk_{chunk_index}_{unique_id}_{base_name}"

        chunk_input_file = f"/tmp/{batch_id}_{chunk_id}.input.jsonl"
        chunk_output_file = f"/tmp/{batch_id}_{chunk_id}.output.jsonl"
        chunk_log_file = f"/tmp/{batch_id}_{chunk_id}.log"  # capture stdout/stderr

        # Write chunk input
        with open(chunk_input_file, "w") as cf:
            cf.writelines(lines_buffer)

        # Build command
        # NOTE: If you want to run on GPU with multiple processes, you might need:
        #    --device=cuda and --distributed-executor-backend=mp
        # but that's HPC environment-dependent.
        cmd = [
            "python", "-m", "vllm.entrypoints.openai.run_batch",
            "-i", chunk_input_file,
            "-o", chunk_output_file,
            "--model", model_name,
            "--tensor-parallel-size", "8",
            "--gpu-memory-utilization", "0.95",
            "--max-model-len", "16384",
            "--disable-log-requests",
            "--multi-step-stream-outputs", "False",
            "--trust-remote-code"
        ]

        # Capture vLLM stdout/stderr in a log file
        with open(chunk_log_file, "wb") as lf:
            completed = subprocess.run(cmd, stdout=lf, stderr=lf)

        if completed.returncode != 0:
            # If run_batch failed, we read the log and raise an error
            with open(chunk_log_file, "rb") as lf:
                log_output = lf.read().decode(errors='replace')
            raise RuntimeError(
                f"vLLM run_batch command failed for chunk {chunk_index}.\n"
                f"Log output:\n{log_output}"
            )

        # Parse chunk output, extract total_tokens, append to final output
        tokens_in_this_chunk = 0
        responses_in_this_chunk = 0

        with open(chunk_output_file, "r") as cof, open(final_output_file, "a") as final_out:
            for line in cof:
                final_out.write(line)
                match = token_pattern.search(line)
                if match:
                    tokens_in_this_chunk += int(match.group(1))
                    responses_in_this_chunk += 1

        # Clean up chunk files
        try:
            os.remove(chunk_input_file)
            os.remove(chunk_output_file)
            # Optionally keep logs to debug chunk issues
            # os.remove(chunk_log_file)
        except OSError:
            pass

        return tokens_in_this_chunk, responses_in_this_chunk


    """
    Dynamically chunk a large input JSONL file into smaller pieces
    (e.g., 100 lines), run each chunk with vLLM, and append results
    to a single output file. If the job ends (SIGTERM), store progress
    in a progress JSON so that the user can resume next time.

    Also:
      - Track how long each chunk took to process, storing in progress file.
      - Log each chunk's subprocess stdout/stderr to a chunk-specific log file.

    Expected parameters['model_params'] to include:
      - 'model': The model name or path for vLLM.
      - 'input_file': The large input JSONL file with requests.
      - 'output_folder_path': (optional) Directory path for final combined results.
      - 'chunk_size': (optional) # lines per chunk (default=100)
      - 'username': (optional) User name (default='anonymous')
    """
    import os
    import time
    import subprocess
    import signal
    import sys
    import re
    import uuid

    # 1) Parse parameters
    model_params = parameters.get('model_params', {})
    model_name = model_params.get('model')
    input_file = model_params.get('input_file')
    final_output_dir = model_params.get('output_folder_path', '/lus/eagle/projects/argonne_tpc/inference-service-batch-results/')
    chunk_size = model_params.get('chunk_size', 20000)
    batch_id = parameters.get('batch_id',f"batch_{uuid.uuid4().hex[:6]}")
    username = parameters.get('username', 'anonymous')
    if not model_name:
        raise ValueError("Missing parameter 'model' in model_params.")
    if not input_file:
        raise ValueError("Missing parameter 'input_file' in model_params.")

    # Construct unique final output + progress file paths
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    final_output_dir = os.path.join(final_output_dir, f"{base_name}_{model_name.split('/')[-1]}_{batch_id}")
    final_output_file = os.path.join(final_output_dir, f"{base_name}_{timestamp_str}.results.jsonl")
    progress_file = os.path.join(final_output_dir, f"{base_name}_{timestamp_str}.progress.json")
    home_dir = os.path.expanduser('~')
    pbs_job_id = os.environ.get('PBS_JOBID')
    # split model_name by '/' if it exists and take the last element
    status_file = os.path.join(home_dir, "batch_jobs", f"{model_name.split('/')[-1]}_{batch_id}_{username}_{pbs_job_id}.status")
    # Create the status file directory and file if it doesn't exist
    os.makedirs(os.path.dirname(status_file), exist_ok=True)
    with open(status_file, "w") as sf:
        sf.write(f"Batch job {batch_id} started successfully.\n")

    # 2) Validate input file
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")
    if not os.access(input_file, os.R_OK):
        raise PermissionError(f"No read permission for file: {input_file}")

    # Create directories if needed (depends on HPC environment if allowed)
    # Create directories with world-readable and writable permissions.
    old_umask = os.umask(0)
    try:
        os.makedirs(os.path.dirname(final_output_file), mode=0o777, exist_ok=True)
        os.makedirs(os.path.dirname(progress_file), mode=0o777, exist_ok=True)
    finally:
        os.umask(old_umask)

    # 2) Validate input file
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")
    if not os.access(input_file, os.R_OK):
        raise PermissionError(f"No read permission for file: {input_file}")


    # 3) Initialize or load progress data
    progress_data = {
        "lines_processed": 0,
        "total_tokens_so_far": 0,
        "num_responses_so_far": 0,
        "chunk_history": []  # store info about each chunk (time, lines, etc.)
    }
    if os.path.isfile(progress_file):
        with open(progress_file, "r") as pf:
            saved = json.load(pf)
            progress_data.update(saved)

    lines_processed = progress_data["lines_processed"]
    total_tokens_global = progress_data["total_tokens_so_far"]
    num_responses_global = progress_data["num_responses_so_far"]

    # 4) Handle SIGTERM for checkpointing
    def handle_sigterm(signum, frame):
        print("[INFO] Caught SIGTERM; writing partial progress...")
        progress_data["lines_processed"] = lines_processed
        progress_data["total_tokens_so_far"] = total_tokens_global
        progress_data["num_responses_so_far"] = num_responses_global
        with open(progress_file, "w") as pf:
            json.dump(progress_data, pf, indent=4)
        os.chmod(progress_file, 0o666)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    # 5) Make sure final output file is writable
    #    We'll append chunk results after each chunk
    with open(final_output_file, "a") as _:
        pass
    os.chmod(final_output_file, 0o666)


    # 6) Read from the input file, skipping lines_processed
    token_pattern = re.compile(r'"total_tokens":\s*(\d+)')
    start_time = time.time()

    print(f"[INFO] Resuming from line {lines_processed}. Chunk size = {chunk_size}")

    lines_buffer = []
    chunk_index = len(progress_data["chunk_history"])  # so chunk numbering picks up

    # If HPC kills the job after finishing the first chunk, we can resume from chunk_index
    with open(input_file, "r") as infile:
        # Skip lines we've already processed
        for _ in range(lines_processed):
            next(infile)

        # Collect lines in memory up to chunk_size
        for line in infile:
            lines_buffer.append(line)
            if len(lines_buffer) >= chunk_size:
                # process chunk
                chunk_start_time = time.time()
                chunk_tokens, chunk_responses = run_chunk_inference(
                    lines_buffer, model_name,base_name, batch_id,
                    final_output_file, token_pattern, chunk_index
                )
                chunk_end_time = time.time()

                # update global counters
                total_tokens_global += chunk_tokens
                num_responses_global += chunk_responses
                lines_processed += len(lines_buffer)

                # log chunk time
                chunk_duration = chunk_end_time - chunk_start_time

                # store partial progress
                progress_data["lines_processed"] = lines_processed
                progress_data["total_tokens_so_far"] = total_tokens_global
                progress_data["num_responses_so_far"] = num_responses_global
                progress_data["chunk_history"].append({
                    "chunk_index": chunk_index,
                    "chunk_lines": len(lines_buffer),
                    "chunk_tokens": chunk_tokens,
                    "chunk_responses": chunk_responses,
                    "chunk_time_sec": chunk_duration
                })

                with open(progress_file, "w") as pf:
                    json.dump(progress_data, pf, indent=4)
                os.chmod(progress_file, 0o666)

                lines_buffer = []
                chunk_index += 1

        # leftover lines in buffer
        if lines_buffer:
            chunk_start_time = time.time()
            chunk_tokens, chunk_responses = run_chunk_inference(
                lines_buffer, model_name, base_name, batch_id,
                final_output_file, token_pattern, chunk_index
            )
            chunk_end_time = time.time()

            total_tokens_global += chunk_tokens
            num_responses_global += chunk_responses
            lines_processed += len(lines_buffer)

            chunk_duration = chunk_end_time - chunk_start_time

            progress_data["lines_processed"] = lines_processed
            progress_data["total_tokens_so_far"] = total_tokens_global
            progress_data["num_responses_so_far"] = num_responses_global
            progress_data["chunk_history"].append({
                "chunk_index": chunk_index,
                "chunk_lines": len(lines_buffer),
                "chunk_tokens": chunk_tokens,
                "chunk_responses": chunk_responses,
                "chunk_time_sec": chunk_duration
            })
            with open(progress_file, "w") as pf:
                json.dump(progress_data, pf, indent=4)
            os.chmod(progress_file, 0o666)

    end_time = time.time()
    response_time = end_time - start_time
    throughput = total_tokens_global / response_time if response_time > 0 else 0

    # 7) Summarize final usage
    final_metrics = {
        "response_time": response_time,
        "throughput_tokens_per_second": throughput,
        "total_tokens": total_tokens_global,
        "num_responses": num_responses_global,
        "lines_processed": lines_processed,
    }
    print("[INFO] Completed all lines in input_file!")
    print("Final metrics:", final_metrics)

    # Update progress file with final metrics
    progress_data.update(final_metrics)
    with open(progress_file, "w") as pf:
        json.dump(progress_data, pf, indent=4)
    os.chmod(progress_file, 0o666)

    # Return info
    output = {
        "results_file": final_output_file,
        "progress_file": progress_file,
        "metrics": final_metrics
    }
    return json.dumps(output, indent=4)


gcc = globus_compute_sdk.Client()
COMPUTE_FUNCTION_ID = gcc.register_function(chunked_vllm_inference_function)
print("Registered Function ID:", COMPUTE_FUNCTION_ID)

# Write function UUID to a file
with open("vllm_inference_function_batch_single_node.txt", "w") as f:
    f.write(COMPUTE_FUNCTION_ID + "\n")


# chat_out = chunked_vllm_inference_function({
#     'model_params': {
#         'model': 'meta-llama/Llama-3.3-70B-Instruct',
#         'input_file': '/lus/eagle/projects/argonne_tpc/cucinell/Data/prompts_massgen/basic_prompts_1M_test1.txt'
#     }
# })
# print("batch output for meta-llama/Llama-3.3-70B-Instruct")
# print(chat_out)