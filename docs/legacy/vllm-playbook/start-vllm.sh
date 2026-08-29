#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# vLLM Server Startup Script for DGX Spark
# =============================================================================
# - Model: nvidia/Qwen3.6-35B-A3B-NVFP4 (MoE, 40 layers, 262k context)
# - Image: NVIDIA 26.07 with xgrammar/tvm-ffi compatibility fix
# - Restart: docker --restart unless-stopped auto-starts after reboot
# - KV Cache: bfloat16 (full precision)
# - Tool parser: qwen3_coder (correct for Qwen3.6 models)
# - Attention: FlashInfer, MoE: Marlin (DGX Spark NVFP4 recipe)
# - MTP speculative decoding: 3 draft tokens for higher generation throughput
# =============================================================================

readonly VLLM_VERSION="26.07-py3"
readonly VLLM_IMAGE="local/nvidia-vllm:${VLLM_VERSION}-xgrammar021"
readonly HF_MODEL_HANDLE="nvidia/Qwen3.6-35B-A3B-NVFP4"
readonly HF_TOKEN_FILE="${HOME}/.huggingface/token"

if [[ ! -r "${HF_TOKEN_FILE}" ]]; then
  echo "ERROR: Hugging Face token not found or unreadable: ${HF_TOKEN_FILE}" >&2
  exit 1
fi

export HF_TOKEN
HF_TOKEN="$(<"${HF_TOKEN_FILE}")"

echo "==> Removing old vllm-server container (if any)..."
docker rm vllm-server -f || true

echo "==> Starting vLLM server..."
docker run -d \
  --name vllm-server \
  --restart unless-stopped \
  --gpus all \
  --ipc=host \
  -p 8000:8000 \
  -e HF_TOKEN \
  -e HF_HOME=/hf-cache \
  -e HF_HUB_CACHE=/hf-cache/hub \
  --mount type=volume,src=vllm-hf-cache,dst=/hf-cache \
  "${VLLM_IMAGE}" \
  vllm serve "${HF_MODEL_HANDLE}" \
    --served-model-name qwen36 \
    --host 0.0.0.0 \
    --port 8000 \
    --tensor-parallel-size 1 \
    --trust-remote-code \
    --kv-cache-dtype bfloat16 \
    --attention-backend flashinfer \
    --moe-backend marlin \
    --kv-cache-memory-bytes 30G \
    --max-model-len 262144 \
    --max-num-seqs 4 \
    --max-num-batched-tokens 8192 \
    --enable-chunked-prefill \
    --async-scheduling \
    --enable-prefix-caching \
    --speculative-config '{"method":"mtp","num_speculative_tokens":3,"moe_backend":"triton"}' \
    --load-format fastsafetensors \
    --reasoning-parser qwen3 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder

echo "==> vLLM server started successfully on port 8000"
