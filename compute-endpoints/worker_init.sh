#!/bin/bash

export HTTP_PROXY="http://proxy.alcf.anl.gov:3128"
export HTTPS_PROXY="http://proxy.alcf.anl.gov:3128"
export http_proxy="http://proxy.alcf.anl.gov:3128"
export https_proxy="http://proxy.alcf.anl.gov:3128"
export ftp_proxy="http://proxy.alcf.anl.gov:3128"

source ~/miniconda3/etc/profile.d/conda.sh
conda activate /eagle/argonne_tpc/inference-gateway/envs/vllmv0.5.5-sophia-env


export HF_DATASETS_CACHE="/eagle/argonne_tpc/model_weights/"
export HF_HOME="/eagle/argonne_tpc/model_weights/"
export RAY_TMPDIR="/tmp"
export NCCL_SOCKET_IFNAME=infinibond0
# export NCCL_NET_GDR_LEVEL=PHB
# export NCCL_CROSS_NIC=1
# export NCCL_COLLNET_ENABLE=1
# export NCCL_DEBUG=INFO

# Function to clean up existing Python processes
cleanup_python_processes() {
    echo "Cleaning up existing Python processes..."
    
    # Get process IDs for specific patterns
    pids=$(pgrep -f "vllm serve")
    for pid in $pids; do
        echo "Killing process $pid (vllm serve)"
        kill -9 $pid
    done

    pids=$(pgrep -f "multiprocessing.spawn")
    for pid in $pids; do
        echo "Killing process $pid (multiprocessing.spawn)"
        kill -9 $pid
    done

    pids=$(pgrep -f "multiprocessing.resource_tracker")
    for pid in $pids; do
        echo "Killing process $pid (multiprocessing.resource_tracker)"
        kill -9 $pid
    done

    # Kill all Python processes owned by the current user
    pids=$(pgrep -u $USER python)
    for pid in $pids; do
        echo "Killing Python process $pid"
        kill -9 $pid
    done
    
    sleep 5  # Give some time for processes to terminate
    
    # Force kill any remaining processes (second pass just in case)
    pids=$(pgrep -f "vllm serve")
    for pid in $pids; do
        echo "Force killing process $pid (vllm serve)"
        kill -9 $pid
    done

    pids=$(pgrep -f "multiprocessing.spawn")
    for pid in $pids; do
        echo "Force killing process $pid (multiprocessing.spawn)"
        kill -9 $pid
    done

    pids=$(pgrep -f "multiprocessing.resource_tracker")
    for pid in $pids; do
        echo "Force killing process $pid (multiprocessing.resource_tracker)"
        kill -9 $pid
    done

    pids=$(pgrep -u $USER python)
    for pid in $pids; do
        echo "Force killing Python process $pid"
        kill -9 $pid
    done
    
    echo "Cleanup complete."
}

# Function to start a model with timeout and retry
start_model() {
    local model_name="$1"
    local command="$2"
    local log_file="$3"
    local -n attempt_counter_ref="$4"  # Pass by reference for attempt counter
    local max_attempts=3
    local timeout=600  # 10 minutes timeout
    


    while [ $attempt_counter_ref -lt $max_attempts ]; do
        attempt_counter_ref=$((attempt_counter_ref + 1))
        echo "Starting $model_name (Attempt $attempt_counter_ref of $max_attempts)"
        
        nohup bash -c "$command" > "$log_file" 2>&1 &
        local pid=$!
        # for attempt in $(seq 1 $max_attempts); do
        #     echo "Starting $model_name (Attempt $attempt of $max_attempts)"
            
        #     # Start the model in the background
        #     nohup bash -c "$command" > "$log_file" 2>&1 &
        #     local pid=$!
        # Wait for the startup message or timeout
        local start_time=$(date +%s)
        while true; do
            if [[ -f "$log_file" ]] && grep -q "INFO:     Application startup complete." "$log_file"; then
                echo "$model_name started successfully"
                return 0
            fi

            local current_time=$(date +%s)
            local elapsed_time=$((current_time - start_time))

            if [ $elapsed_time -ge $timeout ]; then
                echo "Timeout reached for $model_name. Killing process."
                kill -9 $pid
                break
            fi

            sleep 5
        done

        echo "Failed to start $model_name. Cleaning up and retrying..." | tee -a error_log.txt
        cleanup_python_processes
        return 1  # Signal failure to restart loop
    done
    cleanup_python_processes
    echo "Failed to start $model_name after $max_attempts attempts" | tee -a error_log.txt
    exit 1
}

cleanup_python_processes

# Initialize retry counters for each model
retry_counter_model_1=0
retry_counter_model_2=0
retry_counter_model_3=0


# Loop to start models and restart if any model fails
while true; do
    echo "Starting models sequence..."

    # Start first model
    if ! start_model "Meta-Llama-3-70B-Instruct" "CUDA_VISIBLE_DEVICES=0,1,2,3 vllm serve meta-llama/Meta-Llama-3-70B-Instruct --host 127.0.0.1 --port 8000 --tensor-parallel-size 4 --gpu-memory-utilization 0.95 --max-model-len 4096 --enforce-eager --disable-custom-all-reduce --use-v2-block_manager --ssl-keyfile ~/certificates/mykey.key --ssl-certfile ~/certificates/mycert.crt" "~/logfile_sophia-vllm-meta-llama-3-70b-instruct.log" retry_counter_model_1; then
        continue  # Restart from the beginning if this fails
    fi

    # Start second model
    if ! start_model "Meta-Llama-3-8B-Instruct" "CUDA_VISIBLE_DEVICES=4,5 vllm serve meta-llama/Meta-Llama-3-8B-Instruct --host 127.0.0.1 --port 8001 --tensor-parallel-size 2 --gpu-memory-utilization 0.95 --max-model-len 4096 --enforce-eager --disable-custom-all-reduce --use-v2-block_manager --ssl-keyfile ~/certificates/mykey.key --ssl-certfile ~/certificates/mycert.crt" "~/logfile_sophia-vllm-meta-llama-3-8b-instruct.log" retry_counter_model_2; then
        continue  # Restart from the beginning if this fails
    fi

    # Start third model
    if ! start_model "Mistral-7B-Instruct-v0.3" "CUDA_VISIBLE_DEVICES=6,7 vllm serve mistralai/Mistral-7B-Instruct-v0.3 --host 127.0.0.1 --port 8002 --tensor-parallel-size 2 --gpu-memory-utilization 0.95 --max-model-len 4096 --enforce-eager --disable-custom-all-reduce --use-v2-block_manager --ssl-keyfile ~/certificates/mykey.key --ssl-certfile ~/certificates/mycert.crt" "~/logfile_sophia-vllm-mistral-7b-instruct-v0.3.log" retry_counter_model_3; then
        continue  # Restart from the beginning if this fails
    fi

    echo "All models started successfully."
    break  # Exit the loop if all models start successfully
done





# Start first model
# start_model "Meta-Llama-3-70B-Instruct" "CUDA_VISIBLE_DEVICES=0,1,2,3 vllm serve meta-llama/Meta-Llama-3-70B-Instruct --host 127.0.0.1 --port 8000 --tensor-parallel-size 4 --gpu-memory-utilization 0.95 --max-model-len 4096 --enforce-eager --disable-custom-all-reduce --use-v2-block_manager --ssl-keyfile ~/certificates/mykey.key --ssl-certfile ~/certificates/mycert.crt > ~/logfile_sophia-vllm-meta-llama-3-70b-instruct.log 2>&1" ~/logfile_sophia-vllm-meta-llama-3-70b-instruct.log
# Start second model
# start_model "Meta-Llama-3-8B-Instruct" "CUDA_VISIBLE_DEVICES=4,5 vllm serve meta-llama/Meta-Llama-3-8B-Instruct --host 127.0.0.1 --port 8001 --tensor-parallel-size 2 --gpu-memory-utilization 0.95 --max-model-len 4096 --enforce-eager --disable-custom-all-reduce --use-v2-block_manager --ssl-keyfile ~/certificates/mykey.key --ssl-certfile ~/certificates/mycert.crt > ~/logfile_sophia-vllm-meta-llama-3-8b-instruct.log 2>&1" ~/logfile_sophia-vllm-meta-llama-3-8b-instruct.log
# Start third model
#start_model "Mistral-7B-Instruct-v0.3" "CUDA_VISIBLE_DEVICES=6,7 vllm serve mistralai/Mistral-7B-Instruct-v0.3 --host 127.0.0.1 --port 8002 --tensor-parallel-size 2 --gpu-memory-utilization 0.95 --max-model-len 4096 --enforce-eager --disable-custom-all-reduce --use-v2-block_manager --ssl-keyfile ~/certificates/mykey.key --ssl-certfile ~/certificates/mycert.crt > ~/logfile_sophia-vllm-mistral-7b-instruct-v0.3.log 2>&1" ~/logfile_sophia-vllm-mistral-7b-instruct-v0.3.log