# Built/tagged as spark-ai/vllm:26.07-xg021 (see compose/compose.yml `vllm` service
# and config/versions.lock.yaml `images.vllm_base`).
#
# Base: nvcr.io/nvidia/vllm:26.07-py3 — this is the known-good vLLM lineage currently
# running on this box in production (see docs/legacy/vllm-playbook/start-vllm.sh and
# build-vllm.sh). The digest is NOT pinned below: the old config/versions.lock.yaml
# only ever recorded 26.06-py3's digest, and docs/CONTRACTS.md §4 calls for 26.07-py3
# specifically — pin the digest in config/versions.lock.yaml on first `docker pull`.
ARG BASE_IMAGE=nvcr.io/nvidia/vllm:26.07-py3
FROM ${BASE_IMAGE}

# WHY: NVIDIA's 26.07 vLLM release imports `xgrammar.normalize_tool_choice` (used by
# vLLM's tool-choice / structured-output code path), which was first exported by
# xgrammar 0.2.1. The base image ships an older xgrammar lacking that symbol, so vLLM
# fails at import time without this fix. The base image also ships apache-tvm-ffi
# 0.1.7, even though both vLLM and xgrammar 0.2.1 require 0.1.9.
#
# Carried verbatim from the pins in
# docs/legacy/vllm-playbook/Dockerfile.vllm-26.07-xgrammar-fix, which is the fix
# currently keeping vLLM running on this box.
RUN python3 -m pip install --no-cache-dir --no-deps \
    "apache-tvm-ffi==0.1.9" \
    "xgrammar==0.2.1"

# Fails the build loudly if the fix regresses (e.g. a future base image bump ships
# its own newer/incompatible xgrammar and this layer becomes a silent no-op).
RUN python3 -c "from xgrammar import StructuralTag, normalize_tool_choice; print('xgrammar tool-choice API: OK')"
