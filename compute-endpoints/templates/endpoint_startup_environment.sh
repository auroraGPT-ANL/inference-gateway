# Storage space on the compute node
export COMPUTE_NODE_STORAGE="/raid/scratch/inference_service/model_weights"

# Proxy setup
export HTTP_PROXY="http://proxy.alcf.anl.gov:3128"
export HTTPS_PROXY="http://proxy.alcf.anl.gov:3128"
export http_proxy=$HTTP_PROXY
export https_proxy=$HTTPS_PROXY
export ftp_proxy=$HTTP_PROXY
      
# Threading / NUMA
export OMP_NUM_THREADS=4
      
# vLLM-specific
export VLLM_LOG_LEVEL=WARN
export USE_FASTSAFETENSOR=true
export VLLM_IMAGE_FETCH_TIMEOUT=60
export VLLM_USE_RAY_COMPILED_DAG_CHANNEL_TYPE=shm
      
# Cache
export TORCHINDUCTOR_CACHE_DIR="${COMPUTE_NODE_STORAGE}/.cache/torch_inductor"
export VLLM_CACHE_ROOT="${COMPUTE_NODE_STORAGE}/.cache/vllm"
export TRITON_CACHE_DIR="${COMPUTE_NODE_STORAGE}/.cache/triton"
mkdir -p $TORCHINDUCTOR_CACHE_DIR
mkdir -p $VLLM_CACHE_ROOT
mkdir -p $TRITON_CACHE_DIR

# Logs
export LOG_DIR="/eagle/inference_service/vllm_logs"

# Model weights directories
export WEIGHTS_SOURCE_BASE="/eagle/inference_service/model_weights"
export WEIGHTS_DEST_BASE="${COMPUTE_NODE_STORAGE}"

# SSL certificates
export SSL_KEY="~/certificates/mykey.key"
export SSL_CERT="~/certificates/mycert.crt"

# Misc
ulimit -c unlimited
export TRANSFORMERS_OFFLINE=1

# To keep track of jobs
CLUSTER="sophia"
FRAMEWORK="vllm"


build_model_command() {
    local env_vars="$1"
    local model_name="$2"
    local vllm_flags="$3"
    local weights_dir
    weights_dir=$(get_weights_dir "$model_name")

    printf '%s\n' "${env_vars} vllm serve ${weights_dir} --served-model-name ${model_name} --host 127.0.0.1 ${vllm_flags} --ssl-keyfile ${SSL_KEY} --ssl-certfile ${SSL_CERT}"
}


get_log_file() {
    local model_name="$1"
    printf '%s\n' "${LOG_DIR}/logfile_${CLUSTER}_${FRAMEWORK}_${model_name}_$(hostname).log"
}


get_log_files() {
    local -n models_ref="$1"

    for model_name in "${models_ref[@]}"; do
        get_log_file "$model_name"
    done
}


sync_model_weights_on_scratch() {
    local model_name="$1"
    local source_base="${2:-/eagle/inference_service/model_weights}"
    local dest_base="${3:-/raid/scratch/inference_service/model_weights}"
    local parallel_jobs="${4:-8}"
    local file_exts="${5:-safetensors json txt py model jinja yaml v3 bin pth pr}"

    local source_dir="$source_base/$model_name"
    if [ ! -d "$source_dir" ]; then
        echo "Error: Source directory does not exist: $source_dir" >&2
        return 1
    fi

    local dest_dir="$dest_base/$model_name"
    mkdir -p "$dest_dir"

    echo ""
    echo "Synchronising ${model_name} model weights ..."
    echo "Evaluating non-safetensors files..."
    local temp_list
    temp_list=$(mktemp)

    for ext in $file_exts; do
        for src_file in "$source_dir"/*."$ext"; do
            if [ -f "$src_file" ] && [[ "$src_file" != *.safetensors ]]; then
                local filename
                filename=$(basename "$src_file")
                local dest_file="$dest_dir/$filename"
                local src_size
                src_size=$(stat -f%z "$src_file" 2>/dev/null || stat -c%s "$src_file" 2>/dev/null || echo -1)

                if [ ! -f "$dest_file" ] || [ "$(stat -f%z "$dest_file" 2>/dev/null || stat -c%s "$dest_file" 2>/dev/null || echo 0)" -ne "$src_size" ]; then
                    echo "$src_file" >> "$temp_list"
                fi
            fi
        done
    done

    if [ -s "$temp_list" ]; then
        local count
        count=$(wc -l < "$temp_list")
        echo "Parallel copying $count non-.safetensors files:"
        cat "$temp_list"
        xargs -P "$parallel_jobs" -I {} cp {} "$dest_dir" < "$temp_list"
        echo "Parallel copy completed."
    else
        echo "All non-.safetensors files already exist and are complete."
    fi

    rm -f "$temp_list"

    echo "Evaluating .safetensors files..."
    temp_list=$(mktemp)

    for src_file in "$source_dir"/*.safetensors; do
        if [ -f "$src_file" ]; then
            local filename
            filename=$(basename "$src_file")
            local dest_file="$dest_dir/$filename"
            local src_size
            src_size=$(stat -f%z "$src_file" 2>/dev/null || stat -c%s "$src_file" 2>/dev/null || echo -1)

            if [ ! -f "$dest_file" ] || [ "$(stat -f%z "$dest_file" 2>/dev/null || stat -c%s "$dest_file" 2>/dev/null || echo 0)" -ne "$src_size" ]; then
                echo "$src_file" >> "$temp_list"
            fi
        fi
    done

    if [ -s "$temp_list" ]; then
        local count
        count=$(wc -l < "$temp_list")
        echo "Parallel copying $count .safetensors files:"
        cat "$temp_list"
        xargs -P "$parallel_jobs" -I {} cp {} "$dest_dir" < "$temp_list"
        echo "Parallel copy completed."
    else
        echo "All .safetensors files already exist and are complete."
    fi

    rm -f "$temp_list"
}


get_weights_dir() {
    local model_name="$1"
    local weights_dir="${WEIGHTS_DEST_BASE}/${model_name}"

    sync_model_weights_on_scratch "$model_name" "$WEIGHTS_SOURCE_BASE" "$WEIGHTS_DEST_BASE" >&2

    if [[ ! -d "$weights_dir" ]]; then
        echo "Warning: weights dir not found for ${model_name}, falling back to model name" >&2
        printf '%s\n' "$model_name"
    elif [[ -z "$(ls -A "$weights_dir")" ]]; then
        echo "Warning: weights dir is empty for ${model_name}, falling back to model name" >&2
        printf '%s\n' "$model_name"
    else
        printf '%s\n' "$weights_dir"
    fi
}


# Accepts a flat array with stride 3: (env_vars, model_name, vllm_flags) per model.
start_all_models() {
    local -n vllm_inputs_ref="$1"

    while true; do
        echo "Starting models sequence..."

        for (( i = 0; i < ${#vllm_inputs_ref[@]}; i += 3 )); do
            local env_vars="${vllm_inputs_ref[i]}"
            local model_name="${vllm_inputs_ref[i+1]}"
            local vllm_flags="${vllm_inputs_ref[i+2]}"

            local log_file; log_file=$(get_log_file "$model_name")
            local command; command=$(build_model_command "$env_vars" "$model_name" "$vllm_flags")
            local attempt_counter=0

            echo ""
            echo "Executing the following command:"
            echo "$command"
            echo ""
            echo "Writing in the following log:"
            echo "$log_file"
            echo ""

            if ! start_model "$model_name" "$command" "$log_file" attempt_counter; then
                continue 2  # restart the while loop from the beginning
            fi
        done

        echo "All models started successfully."
        break
    done
}


start_model() {
    local model_name="$1"
    local command="$2"
    local log_file="$3"
    local -n attempt_counter_ref="$4"  # Pass by reference for attempt counter
    local max_attempts=2
    local timeout=3600  # Default timeout (can be parameterized)

    while [ "$attempt_counter_ref" -lt "$max_attempts" ]; do
        attempt_counter_ref=$((attempt_counter_ref + 1))
        echo "Starting $model_name (Attempt $attempt_counter_ref of $max_attempts)"

        # Start the model in the background
        log_dir="$(dirname "$log_file")"
        
        # Create the directory if it doesn't exist
        mkdir -p "$log_dir"

        # Create an empty file if it doesn't already exist
        touch "$log_file"
        > "$log_file"

        nohup bash -c "$command" > "$log_file" 2>&1 &
        local pid=$!

        local start_time=$(date +%s)
        while true; do
            if [[ -f "$log_file" ]] && grep -q "INFO:     Application startup complete." "$log_file"; then
                echo "$model_name started successfully"
                return 0
            fi

            local current_time=$(date +%s)
            local elapsed_time=$((current_time - start_time))

            if [ "$elapsed_time" -ge "$timeout" ]; then
                echo "Timeout reached for $model_name. Killing process."
                kill -9 "$pid" 2>/dev/null || true
                break
            fi

            sleep 5
        done

        echo "Failed to start $model_name. Cleaning up and retrying..." | tee -a error_log.txt
        cleanup_python_processes
    done

    echo "Failed to start $model_name after $max_attempts attempts" | tee -a error_log.txt
    return 1
}


cleanup_python_processes() {
    echo "Cleaning up existing Python processes..."

    # Define patterns to kill
    local patterns=("vllm serve" "multiprocessing.spawn" "multiprocessing.resource_tracker")

    for pattern in "${patterns[@]}"; do
        pids=$(pgrep -f "$pattern")
        for pid in $pids; do
            echo "Killing process $pid ($pattern)"
            kill -9 "$pid" 2>/dev/null || true
        done
    done

    # Kill all Python processes owned by the current user
    pids=$(pgrep -u "$USER" python)
    for pid in $pids; do
        echo "Killing Python process $pid"
        kill -9 "$pid" 2>/dev/null || true
    done

    sleep 5  # Give some time for processes to terminate

    # Force kill any remaining processes (second pass just in case)
    for pattern in "${patterns[@]}"; do
        pids=$(pgrep -f "$pattern")
        for pid in $pids; do
            echo "Force killing process $pid ($pattern)"
            kill -9 "$pid" 2>/dev/null || true
        done
    done

    pids=$(pgrep -u "$USER" python)
    for pid in $pids; do
        echo "Force killing Python process $pid"
        kill -9 "$pid" 2>/dev/null || true
    done

    echo "Cleanup complete."
}