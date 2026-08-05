ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Temporary compatibility layer for vLLM's Prometheus route walker.
# Remove only after the test in docs/COMPATIBILITY.md passes on the replacement
# NGC image. Upstream fix: https://github.com/vllm-project/vllm/pull/45629
ARG FASTAPI_VERSION=0.136.3
RUN python3 -m pip install --no-cache-dir --upgrade "fastapi[standard]==${FASTAPI_VERSION}" \
 && python3 -c 'import fastapi; assert fastapi.__version__ == "0.136.3", fastapi.__version__'
