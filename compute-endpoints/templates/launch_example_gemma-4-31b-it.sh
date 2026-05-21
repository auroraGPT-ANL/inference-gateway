source /home/openinference_svc/service_account.env
source /home/openinference_svc/endpoint_startup/endpoint_startup_environment.sh
source /eagle/inference_service/env/vllm-0.19.0/bin/activate

declare -a vllm_inputs=(
  ""  "google/gemma-4-31B-it"  "--port 8000 --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 --async-scheduling --tensor-parallel-size 8 --max-model-len 262144 --trust-remote-code --gpu-memory-utilization 0.9"
)

start_all_models vllm_inputs