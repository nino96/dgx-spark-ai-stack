# DGX Spark: Qwen3.6-35B-A3B-NVFP4 with vLLM, OpenWebUI, and OpenCode

This guide sets up NVIDIA's Qwen3.6-35B-A3B NVFP4 model on a DGX Spark using
the NVIDIA vLLM 26.07 image and the compatibility Dockerfile in this
repository.

The workflow is split into two operations:

1. vllm/build-vllm.sh builds the patched image once.
2. vllm/start-vllm.sh starts or recreates the vLLM container without building.

The current setup uses vLLM 26.07, xgrammar 0.2.1, apache-tvm-ffi 0.1.9,
Marlin for the quantized MoE, a BF16 (non-FP8) KV cache, a 30 GiB KV-cache
cap, a 262,144-token context, and up to four simultaneous sequences. In this
setup, BF16 is the requested full-precision cache mode; it is 16-bit rather
than FP32.

This document is a setup guide, not an additional instruction source: the
authoritative runtime settings remain in vllm/start-vllm.sh.

## 1. Repository files

- vllm/Dockerfile.vllm-26.07-xgrammar-fix
  - starts from nvcr.io/nvidia/vllm:26.07-py3
  - installs the xgrammar and apache-tvm-ffi compatibility pins
- vllm/build-vllm.sh
  - builds local/nvidia-vllm:26.07-py3-xgrammar021
- vllm/start-vllm.sh
  - runs the already-built image as vllm-server

The Dockerfile is not built on every launch. Build it after changing the base
image, dependency pins, or Dockerfile. Use the start script for normal
restarts.

## 2. Prerequisites

You need:

- a DGX Spark with NVIDIA drivers and Docker GPU support
- access to nvcr.io/nvidia/vllm:26.07-py3
- a Hugging Face account that can download
  nvidia/Qwen3.6-35B-A3B-NVFP4
- a Hugging Face access token
- OpenWebUI and/or OpenCode running on this host or able to reach port 8000

Check the GPU and Docker integration:

If the image pull reports an authorization error, complete the NGC login below
and rerun this check.

~~~bash
nvidia-smi
docker run --rm --gpus all nvcr.io/nvidia/vllm:26.07-py3 \
  python3 -c 'import torch; print(torch.cuda.get_device_name(0)); print(torch.cuda.get_device_capability(0))'
~~~

The expected DGX Spark device is GB10 with compute capability (12, 1).

### NVIDIA Container Registry authentication

The first build pulls the NVIDIA base image from NGC. If Docker has not
already been authenticated on this host, create an NGC API key in the NVIDIA
NGC portal and log in without putting the key in shell history:

~~~bash
read -rsp "NGC API key: " NGC_API_KEY
printf '\n'
printf '%s' "$NGC_API_KEY" | docker login nvcr.io \
  --username '$oauthtoken' --password-stdin
unset NGC_API_KEY
~~~

The build account needs permission to pull the vLLM 26.07 image.

## 3. Configure the Hugging Face token

The launcher reads the token from ~/.huggingface/token. Create it once:

~~~bash
mkdir -p ~/.huggingface
chmod 700 ~/.huggingface
printf '%s\n' 'hf_your_token_here' > ~/.huggingface/token
chmod 600 ~/.huggingface/token
~~~

Do not commit this file or put the token directly in a shell script. Verify
only that it exists:

~~~bash
test -s ~/.huggingface/token && echo "Hugging Face token file is present"
~~~

## 4. Create the persistent model cache

Model files are stored in a Docker volume so they survive container
recreation:

~~~bash
docker volume create vllm-hf-cache
docker volume inspect vllm-hf-cache
~~~

The first startup downloads the model into this volume. Later starts reuse it.

## 5. Why the compatibility image exists

The NVIDIA 26.07 vLLM code imports xgrammar.normalize_tool_choice, while the
original image's xgrammar package does not export that symbol. The Dockerfile
installs:

- xgrammar==0.2.1, which exports normalize_tool_choice
- apache-tvm-ffi==0.1.9, matching the vLLM/xgrammar runtime requirement

The image build imports StructuralTag and normalize_tool_choice as a
verification step. A successful build checks the API that caused the
OpenWebUI/LiteLLM 500 error.

This is a narrow compatibility layer; it does not rebuild vLLM or change model
weights.

## 6. Build the image once

From the repository root:

~~~bash
cd /home/niyam-gb10/Documents
chmod +x vllm/build-vllm.sh vllm/start-vllm.sh
./vllm/build-vllm.sh
~~~

Verify the tag and imports:

~~~bash
docker image inspect local/nvidia-vllm:26.07-py3-xgrammar021 \
  --format '{{.RepoTags}}'

docker run --rm --gpus all \
  local/nvidia-vllm:26.07-py3-xgrammar021 \
  python3 -c 'from xgrammar import StructuralTag, normalize_tool_choice; print("xgrammar API OK")'
~~~

If this fails, fix the image before starting the server.

## 7. Start vLLM

~~~bash
cd /home/niyam-gb10/Documents
./vllm/start-vllm.sh
~~~

The script stops/removes an old vllm-server container, starts the local image,
mounts vllm-hf-cache, passes the token, listens on 0.0.0.0:8000, and serves
the API model name qwen36. It does not run docker build.

Follow startup:

~~~bash
docker logs -f vllm-server
~~~

The first run can take longer while it downloads the model and compiles or
tunes kernels.

## 8. Runtime settings

The authoritative settings are in vllm/start-vllm.sh.

| Setting | Value | Purpose |
| --- | --- | --- |
| Model | nvidia/Qwen3.6-35B-A3B-NVFP4 | DGX Spark NVFP4 model |
| MoE backend | marlin | Quantized MoE execution |
| KV cache dtype | bfloat16 | Non-FP8, 16-bit KV cache |
| KV cache memory | 30G | Fixed cache budget |
| Maximum context | 262144 | Maximum tokens per request |
| Maximum sequences | 4 | Concurrent sequence limit |
| Batched tokens | 8192 | Scheduler batch-token limit |
| Attention backend | flashinfer | Attention implementation |
| Speculative decoding | MTP, 3 tokens | Draft-token speculation |
| Served model | qwen36 | Client/API model name |

The launcher also enables chunked prefill, async scheduling, prefix caching,
Qwen3 reasoning parsing, and the qwen3_coder tool-call parser.

## 9. Why the KV cache is explicitly capped

DGX Spark has unified CPU/GPU memory, so free -h, nvidia-smi, and CUDA can
show different views of the same physical pool. A percentage such as
gpu_memory_utilization=0.6 is not a promise that 40% of system RAM remains
unused.

In one measured startup, vLLM reported:

- 21.99 GiB for model loading
- 65.85 GiB available to the automatic KV-cache allocator
- 2,951,136 cache tokens
- 11.26 full 262,144-token requests at that uncapped capacity
- about 3.97 GiB CUDA-free memory after allocation

That uncapped result is not the target configuration. vLLM's automatic
calculation included a negative CUDA-graph estimate on unified memory, which
made the reported cache capacity overly optimistic.

The current 30 GiB cap makes the budget predictable:

- roughly 21--22 GiB for model weights and runtime state
- 30 GiB for KV cache
- the remainder for CUDA workspaces, graphs, MTP, allocator overhead, and the
  operating system

Four full 262,144-token requests were estimated at roughly 23--24 GiB of BF16
KV cache, leaving several GiB inside the 30 GiB cap. Actual usage depends on
prompt length, generated-token length, batching, and workspace requirements.

Increase the cap gradually only after monitoring memory. If startup runs out of
memory, reduce the cap first, then max-num-seqs or max-model-len.

## 10. Verify the API

Health:

~~~bash
curl -fsS http://127.0.0.1:8000/health
~~~

Models:

~~~bash
curl -fsS http://127.0.0.1:8000/v1/models
~~~

Basic chat:

~~~bash
curl -fsS http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen36",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "max_tokens": 64
  }'
~~~

The client model name must be qwen36, matching served-model-name.

## 11. Verify tool calling

The launcher enables auto tool choice, the qwen3_coder tool-call parser, and
the qwen3 reasoning parser. Test with:

~~~bash
curl -fsS http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen36",
    "messages": [{"role": "user", "content": "What is the weather in Doha?"}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get the weather for a city",
        "parameters": {
          "type": "object",
          "properties": {"city": {"type": "string"}},
          "required": ["city"]
        }
      }
    }],
    "tool_choice": "auto",
    "max_tokens": 128
  }'
~~~

The response should contain a parsed tool_calls entry when selected. If the
server reports cannot import name normalize_tool_choice, inspect the image:

~~~bash
docker inspect vllm-server --format '{{.Config.Image}}'
~~~

It should be local/nvidia-vllm:26.07-py3-xgrammar021.

## 12. Connect OpenWebUI

For OpenWebUI on the host, use http://127.0.0.1:8000/v1. For another
container or host, use the DGX Spark LAN/Tailscale address or another
reachable host address.

OpenWebUI OpenAI-compatible settings:

- Base URL: http://<vllm-host>:8000/v1
- API key: any non-empty value if the UI requires one
- Model: qwen36

Do not append /chat/completions to the base URL.

## 13. Connect OpenCode

Configure an OpenAI-compatible provider:

- provider type: @ai-sdk/openai-compatible
- base URL: http://<vllm-host>:8000/v1
- model: qwen36

For local OpenCode, use 127.0.0.1. For a remote client, use a routable
LAN/Tailscale address. An SSH tunnel is:

~~~bash
ssh -N -L 8000:127.0.0.1:8000 <user>@<dgx-spark-host>
~~~

The client can then use http://127.0.0.1:8000/v1.

## 14. Container lifecycle

Inspect:

~~~bash
docker ps --filter name=vllm-server
docker inspect vllm-server --format '{{.Config.Image}}'
~~~

Restart without rebuilding:

~~~bash
docker restart vllm-server
~~~

Recreate using the current launcher:

~~~bash
./vllm/start-vllm.sh
~~~

Stop:

~~~bash
docker stop vllm-server
~~~

The restart-unless-stopped policy brings the container back after a Docker
daemon or host restart unless it was explicitly stopped.

## 15. When to rebuild

Rebuild after changing the Dockerfile, NVIDIA base image tag, dependency pins,
or build-script image tag:

~~~bash
./vllm/build-vllm.sh
./vllm/start-vllm.sh
~~~

A normal start-vllm.sh run does not download the image or rebuild. The model
cache remains in vllm-hf-cache.

## 16. Troubleshooting

### normalize_tool_choice import error

Check the running image:

~~~bash
docker inspect vllm-server --format '{{.Config.Image}}'
docker exec vllm-server python3 -c \
  'from xgrammar import normalize_tool_choice; print("OK")'
~~~

If the image is wrong, run build-vllm.sh and then start-vllm.sh.

### CUDA out of memory or cache initialization failure

Reduce one or more of kv-cache-memory-bytes, max-num-seqs, max-model-len, or
max-num-batched-tokens. The safest first change is reducing the KV cap, for
example from 30G to 26G. A launcher-only change does not require rebuilding.

### Tool calls fail to parse

Confirm the three tool/reasoning parser flags are present, the client sends
tool_choice auto when appropriate, and the model name is qwen36.

### Marlin or FlashInfer warnings

Backend or CUDA graph warnings are not automatically fatal. Check /health and
make a small chat request. Marlin is the appropriate starting backend for this
NVFP4 quantized MoE model; benchmark before replacing it.

### Slow first startup

The first run may download about 22 GiB and compile/tune kernels:

~~~bash
docker logs -f vllm-server
docker stats vllm-server
~~~

Do not delete vllm-hf-cache unless you intentionally want a fresh download.

## 17. Cleanup

Remove only the container:

~~~bash
docker rm -f vllm-server
~~~

Remove the local image:

~~~bash
docker image rm local/nvidia-vllm:26.07-py3-xgrammar021
~~~

Removing the cache is destructive to the local model download:

~~~bash
docker volume rm vllm-hf-cache
~~~

## 18. References

- [NVIDIA vLLM 26.07 release notes](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-07.html)
- [Official Qwen3.6-35B-A3B vLLM recipe](https://github.com/vllm-project/recipes/blob/main/models/Qwen/Qwen3.6-35B-A3B.yaml)
- [Qwen3.6-35B-A3B-NVFP4 model configuration](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4/blob/main/config.json)
- [xgrammar 0.2.1 release](https://github.com/mlc-ai/xgrammar/releases/tag/v0.2.1)
- [vLLM cache configuration documentation](https://docs.vllm.ai/en/latest/api/vllm/config/cache/)
