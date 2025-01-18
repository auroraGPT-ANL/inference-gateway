import globus_compute_sdk
import json

def vllm_batch_inference_function(parameters):
    import os
    import time
    import subprocess
    import json
    import signal
    import sys
    import re


    # --- 1) Parse and validate parameters ---
    model_name = parameters['model_params'].pop('model',None)
    input_file = parameters['model_params'].pop('input_file',None)
    custom_output_file_path = parameters['model_params'].pop('output_file', '/lus/eagle/projects/argonne_tpc/inference-service-batch-results/') 
    
    if not model_name:
        raise ValueError("Missing required parameter: 'model'")
    if not input_file:
        raise ValueError("Missing required parameter: 'input_file'")

    # Check file existence and read permission
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")
    if not os.access(input_file, os.R_OK):
        raise PermissionError(f"No read permission for file: {input_file}")

    # --- 2) Construct unique output/checkpoint filenames ---
    # Strip extension from the input filename
    # --- 2) Decide on the output filename ---
    input_basename = os.path.splitext(os.path.basename(input_file))[0]
    model_sanitized = model_name.replace("/", "_").replace(" ", "_").replace(":", "_")
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    output_filename = f"{input_basename}_{model_sanitized}_{timestamp_str}.results.jsonl"
    checkpoint_filename = f"{input_basename}_{model_sanitized}_{timestamp_str}.checkpoint.json"
    # Use the user-provided output_file, and validate its directory
    output_dir = os.path.dirname(custom_output_file_path) or "."
    if not os.access(output_dir, os.W_OK):
        raise PermissionError(
            f"No write permission for directory: {output_dir} "
        )
    # For checkpoint, use same dir as output_file with .checkpoint.json
    output_file = os.path.join(output_dir, output_filename)
    checkpoint_file = os.path.join(output_dir, checkpoint_filename)
    
    # Keep track of partial usage in memory
    partial_usage = {
        "total_tokens_so_far": 0,
        "num_responses_so_far": 0
    }

    # --- 3) Define a signal handler for graceful termination ---
    def handle_sigterm(signum, frame):
        print("[INFO] Caught SIGTERM; writing partial checkpoint...")
        with open(checkpoint_file, "w") as ckpt:
            json.dump(partial_usage, ckpt, indent=4)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    # --- 4) Build and run the vLLM batch command ---
    #
    #  Here we specify settings for 8 A100 GPUs (e.g., --tensor-parallel-size=8).
    #  Adjust or add flags as appropriate for your environment:
    #   - device=cuda ensures we run on GPUs
    #   - distributed-executor-backend=mp (multiprocessing) is typical on a single node with 8 GPUs
    #   - gpu-memory-utilization=0.9 means vLLM will try to use up to 90% of each GPU's memory
    #   - dtype=bfloat16 or float16 can be used; pick whichever is preferred
    #
    cmd = [
        "python",
        "-m",
        "vllm.entrypoints.openai.run_batch",
        "-i", input_file,
        "-o", output_file,
        "--model", model_name,
        "--device", "cuda",
        "--distributed-executor-backend", "mp",
        "--tensor-parallel-size", "8",
        "--gpu-memory-utilization", "0.95",
        "--enable-chunked-prefill",
        "--dtype", "bfloat16"
    ]

    start_time = time.time()
    completed_proc = subprocess.run(cmd, capture_output=True)
    end_time = time.time()

    if completed_proc.returncode != 0:
        stderr = completed_proc.stderr.decode()
        raise RuntimeError(f"vllm run_batch command failed:\n{stderr}")

    # --- 5) Parse the results to accumulate usage ---
    total_tokens = 0
    num_responses = 0
    pattern = re.compile(r'"total_tokens":\s*(\d+)')
    with open(output_file, "r") as f:
        for line in f:
            match = pattern.search(line)
            if match:
                total_tokens += int(match.group(1))
                num_responses += 1
                partial_usage["total_tokens_so_far"] = total_tokens
                partial_usage["num_responses_so_far"] = num_responses
            # Continuously update partial usage

    # --- 6) Compute final metrics and write final checkpoint ---
    response_time = end_time - start_time
    throughput = total_tokens / response_time if response_time > 0 else 0
    metrics = {
        "response_time": response_time,
        "throughput_tokens_per_second": throughput,
        "total_tokens": total_tokens,
        "num_responses": num_responses
    }

    with open(checkpoint_file, "w") as ckpt:
        json.dump(metrics, ckpt, indent=4)

    # --- 7) Return only the paths + summary metrics ---
    output = {
        "results_file": output_file,
        "checkpoint_file": checkpoint_file,
        "metrics": metrics
    }

    return json.dumps(output, indent=4)


# Create Globus Compute client
gcc = globus_compute_sdk.Client()

# Register the function
COMPUTE_FUNCTION_ID = gcc.register_function(vllm_batch_inference_function)
print("Registered Function ID:", COMPUTE_FUNCTION_ID)

# Write function UUID to a file
uuid_file_name = "vllm_batch_inference_function.txt"
with open(uuid_file_name, "w") as file:
    file.write(COMPUTE_FUNCTION_ID)
    file.write("\n")
file.close()

# unit test
# # # Chat completion example
# chat_out = vllm_batch_inference_function({
#     'model_params': {
#         'model': 'meta-llama/Llama-3.1-8B-Instruct',
#         'input_file': '/home/openinference_svc/test_vllm/openai_example_batch.jsonl'
#     }
# })
# print("batch output for meta-llama/Llama-3.1-8B-Instruct")
# print(chat_out)