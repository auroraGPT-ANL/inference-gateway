source /home/openinference_svc/service_account.env
source /home/openinference_svc/endpoint_startup/endpoint_startup_environment.sh
source /eagle/inference_service/env/vllm-0.19.0/bin/activate

declare -a vllm_inputs=(
  "CUDA_VISIBLE_DEVICES=0,1,2,3 VLLM_ATTENTION_BACKEND=TRITON_ATTN"  "openai/gpt-oss-120b"  "--port 8000 --tool-call-parser openai --enable-auto-tool-choice --tensor-parallel-size 4 --max-model-len 32768 --max-num-seqs 32"
  "CUDA_VISIBLE_DEVICES=4,5,6,7 VLLM_ATTENTION_BACKEND=TRITON_ATTN"  "openai/gpt-oss-20b"   "--port 8001 --tool-call-parser openai --enable-auto-tool-choice --tensor-parallel-size 4"
)

start_all_models vllm_inputs