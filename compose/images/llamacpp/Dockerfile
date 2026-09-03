# Built/tagged as spark-ai/llamacpp:f9e832c (see compose/compose.yml `llamacpp` and
# `embeddings` services, config/versions.lock.yaml `images.llamacpp`).
#
# Multi-stage: CUDA devel base builds llama-server, CUDA runtime base ships it.
#
# CUDA_TAG NEEDS-PIN: verify this exact nvidia/cuda tag exists for linux/arm64 at
# build time (registry access wasn't available while writing this Dockerfile). Host
# driver reports CUDA 13.0.3 (see /usr/local/cuda/version.json on this box); a 13.0.x
# devel/runtime image pair is expected to be forward/backward compatible with that
# driver but has not been verified against this exact Dockerfile.
ARG CUDA_TAG=13.0.1
ARG BUILDER_BASE=nvidia/cuda:${CUDA_TAG}-devel-ubuntu24.04
ARG RUNTIME_BASE=nvidia/cuda:${CUDA_TAG}-runtime-ubuntu24.04

FROM ${BUILDER_BASE} AS builder

# Pinned llama.cpp commit. Carried from the schema-1 config/versions.lock.yaml
# (sources.llama_cpp: ref b10289, upstream https://github.com/ggml-org/llama.cpp.git)
# — NOT the local ~/llama.cpp-qwen4exp checkout on this box, which is an unpublished
# experimental fork (custom qwen4exp indexer-cache changes) that can't be `git
# clone`d by this Dockerfile. Verify b10289 still configures/builds cleanly against
# this CUDA_TAG before relying on it in production; bump to a newer upstream release
# tag (and update config/versions.lock.yaml) if it doesn't.
ARG LLAMACPP_COMMIT=f9e832c10e9444cb168ddcb579cc62c154f3068b

RUN apt-get update && apt-get install --no-install-recommends -y \
      git \
      build-essential \
      cmake \
      ninja-build \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone https://github.com/ggml-org/llama.cpp.git . \
    && git checkout "${LLAMACPP_COMMIT}"

# CUDA/GGML cmake flags. -DGGML_CUDA=ON and
# -DCMAKE_CUDA_ARCHITECTURES=121 come from docs/CONTRACTS.md §4 verbatim; the rest
# were found by inspecting ~/llama.cpp-qwen4exp/build/CMakeCache.txt (a local
# from-source checkout built on this same box) and replicated here where they
# differ from — or reinforce — the ggml/CMakeLists.txt defaults:
#
#  -DGGML_CUDA=ON                 required: enables the CUDA backend at all.
#  -DCMAKE_CUDA_ARCHITECTURES=121 GB10 is sm_121 (Blackwell). Per
#                                 ggml/src/ggml-cuda/CMakeLists.txt, NATIVE detection
#                                 (GGML_NATIVE=ON, the ggml default, WITH a GPU
#                                 visible at configure time) would resolve to the
#                                 Blackwell-specific "121a-real" variant, not plain
#                                 "121" — but no GPU is visible inside a `docker
#                                 build`, so native detection cannot be used here at
#                                 all, and CONTRACTS.md's explicit "121" is used
#                                 as-is. If runtime kernel selection misbehaves,
#                                 try "121a-real" instead (see report: this is a
#                                 CONTRACTS.md wording vs. actual-ggml-cmake mismatch
#                                 worth a second look, not something this workstream
#                                 can silently resolve).
#  -DGGML_NATIVE=OFF              must be OFF for a portable Docker build: no GPU is
#                                 attached at build time, and leaving native ON with
#                                 no GPU produces "garbage" architectures per the
#                                 ggml-cuda CMakeLists.txt's own comment.
#  -DGGML_CUDA_FA=ON              FlashAttention CUDA kernels; ON in the reference
#                                 CMakeCache.txt (also the ggml/CMakeLists.txt
#                                 default — set explicitly for clarity/pinning).
#  -DGGML_CUDA_GRAPHS=ON          CUDA graphs; ON in the reference CMakeCache.txt.
#  -DGGML_CPU_REPACK=ON           runtime Q4_0 -> Q4_X_X repack; ON in the reference
#                                 CMakeCache.txt (also the ggml/CMakeLists.txt
#                                 default).
#  -DGGML_SCHED_MAX_COPIES=4      pipeline-parallelism input-copy count; matches the
#                                 reference CMakeCache.txt value (also the
#                                 ggml/CMakeLists.txt default).
RUN cmake -S . -B build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DGGML_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=121 \
      -DGGML_NATIVE=OFF \
      -DGGML_CUDA_FA=ON \
      -DGGML_CUDA_GRAPHS=ON \
      -DGGML_CPU_REPACK=ON \
      -DGGML_SCHED_MAX_COPIES=4 \
    && cmake --build build --config Release -j"$(nproc)" --target llama-server

FROM ${RUNTIME_BASE} AS runtime

# curl is required for the compose healthcheck (curl -f http://localhost:8080/health).
RUN apt-get update && apt-get install --no-install-recommends -y \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# llama.cpp's CMake build drops llama-server plus its shared libs (libggml*,
# libllama.so — BUILD_SHARED_LIBS is the ggml/CMakeLists.txt default) together under
# build/bin/. Copy the whole directory rather than globbing individual *.so files so
# the copy can't silently miss a lib if a future llama.cpp bump changes the set of
# shared objects produced.
COPY --from=builder /src/build/bin/ /opt/llama.cpp/
ENV PATH="/opt/llama.cpp:${PATH}" \
    LD_LIBRARY_PATH="/opt/llama.cpp:${LD_LIBRARY_PATH}"

EXPOSE 8080
ENTRYPOINT ["llama-server"]
