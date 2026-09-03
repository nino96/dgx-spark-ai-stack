#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly VLLM_VERSION="26.07-py3"
readonly VLLM_BASE_IMAGE="nvcr.io/nvidia/vllm:${VLLM_VERSION}"
readonly VLLM_IMAGE="local/nvidia-vllm:${VLLM_VERSION}-xgrammar021"
readonly VLLM_DOCKERFILE="${SCRIPT_DIR}/Dockerfile.vllm-26.07-xgrammar-fix"

echo "==> Building ${VLLM_IMAGE}..."
docker build \
  --build-arg "BASE_IMAGE=${VLLM_BASE_IMAGE}" \
  --file "${VLLM_DOCKERFILE}" \
  --tag "${VLLM_IMAGE}" \
  "${SCRIPT_DIR}"

echo "==> Built ${VLLM_IMAGE} successfully"
