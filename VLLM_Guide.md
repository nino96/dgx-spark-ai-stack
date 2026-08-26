---
title: "DGX Spark vLLM + Qwen 3.6 35B NVFP4 + OpenCode"
date: 2026-08-25
tags:
  - dgx-spark
  - vllm
  - qwen
  - nvfp4
  - opencode
  - local-llm
---

# DGX Spark vLLM + Qwen 3.6 35B NVFP4 + OpenCode

> [!summary]
> This guide consolidates the complete working setup and troubleshooting path from serving `nvidia/Qwen3.6-35B-A3B-NVFP4` on a **single NVIDIA DGX Spark / ASUS Ascent GX10** with NVIDIA's vLLM container, keeping model downloads persistent, applying the NVIDIA 26.07 xgrammar/apache-tvm-ffi compatibility patch needed for tool calling in this setup, validating the OpenAI-compatible API, and connecting the endpoint to **OpenCode** with automatic tool calling enabled.

## 1. What this setup looks like

The final architecture is:

```text
┌─────────────────────────────────────┐
│ Local development machine           │
│                                     │
│  OpenCode                           │
│     │                               │
│     │ OpenAI-compatible HTTP API    │
│     ▼                               │
│  http://DGX-IP:8000/v1              │
└───────────────┬─────────────────────┘
                │ LAN / Tailscale / SSH tunnel
                ▼
┌─────────────────────────────────────┐
│ DGX Spark / ASUS Ascent GX10        │
│                                     │
│ Docker                              │
│  ├─ vLLM container                  │
│  │    └─ Qwen 3.6 35B A3B NVFP4    │
│  │                                  │
│  └─ named volume: vllm-hf-cache     │
│       └─ persistent HF model cache  │
└─────────────────────────────────────┘
```

The important design decisions are:

- The vLLM runtime stays inside Docker.
- The Hugging Face model cache is stored in a **Docker named volume**, not inside the disposable container.
- Removing/recreating the vLLM container therefore does **not** force a model re-download.
- The model is exposed through vLLM's OpenAI-compatible `/v1` API.
- OpenCode connects using its generic OpenAI-compatible provider.
- vLLM is started with Qwen's reasoning parser and tool-call parser so OpenCode can use tools.

---

# 2. Assumptions

This guide assumes:

- Docker already works on the DGX Spark.
- NVIDIA GPU support works inside Docker.
- You already downloaded/pulled the NVIDIA NGC vLLM image.
- You are using one DGX Spark / GB10, not a cluster.
- The target model is:

```text
nvidia/Qwen3.6-35B-A3B-NVFP4
```

NVIDIA/vLLM's current DGX Spark recipe lists the model as:

- 35B total parameters
- about 3B active parameters
- native 262,144-token context
- NVIDIA ModelOpt NVFP4 checkpoint
- DGX Spark / GB10 supported
- vLLM >= 0.24.0 recommended for the DGX Spark NVFP4 route

---

# 3. Check Docker access

On the DGX Spark:

```bash
docker ps
```

If this gives a Docker permission error:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

Then verify again:

```bash
docker ps
```

---

# 4. Set reusable environment variables

Set the vLLM image tag you already downloaded.

For example:

```bash
export LATEST_VLLM_VERSION="<YOUR_NGC_VLLM_TAG>"
export VLLM_IMAGE="nvcr.io/nvidia/vllm:${LATEST_VLLM_VERSION}"
```

Set the model:

```bash
export MODEL_HANDLE="nvidia/Qwen3.6-35B-A3B-NVFP4"
```

If a Hugging Face token is required, enter it without putting the token directly into shell history:

```bash
read -rsp "Hugging Face token: " HF_TOKEN
echo
export HF_TOKEN
```

Check that Docker can see the image:

```bash
docker image inspect "$VLLM_IMAGE" >/dev/null && echo "vLLM image found"
```

---

# 5. Create a persistent Hugging Face model cache

## Recommended: Docker named volume

Do **not** use a root-level host directory such as `/data` unless you deliberately created and permissioned it.

Create a Docker-managed persistent cache instead:

```bash
docker volume create vllm-hf-cache
```

Verify it:

```bash
docker volume inspect vllm-hf-cache
```

Docker decides where the volume physically lives. You do not need to manage its filesystem permissions manually.

## Why this matters

Without an external cache mount, Hugging Face downloads can live only in the container's writable filesystem.

If that container is removed:

```bash
docker rm vllm-server
```

those files can disappear with it.

With:

```bash
--mount type=volume,src=vllm-hf-cache,dst=/hf-cache
```

the model files survive container removal and recreation.

> [!warning]
> `docker rm` does **not** delete this named volume.
>
> `docker volume rm vllm-hf-cache` **does** delete it and will force the model to be downloaded again.

---

# 6. NVIDIA vLLM 26.07: xgrammar/tool-calling compatibility issue

For the current NVIDIA NGC image used by this guide:

```text
nvcr.io/nvidia/vllm:26.07-py3
```

the earlier FastAPI 0.137 issue is no longer the blocker encountered in this setup.

Instead, **tool calling fails because of the xgrammar dependency combination shipped in the 26.07 image**.

NVIDIA's 26.07 release notes list:

```text
vLLM:       0.24.0
xgrammar:   0.2.0
transformers: 5.6.1
```

Reference:

https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-07.html

The NGC tag itself is here:

https://catalog.ngc.nvidia.com/orgs/nvidia/-/containers/vllm/26.07-py3

## What vLLM expects

vLLM's structural tool parser imports:

```python
from xgrammar import StructuralTag, normalize_tool_choice
```

Current vLLM source:

https://github.com/vllm-project/vllm/blob/main/vllm/tool_parsers/structural_tag_registry.py

But NVIDIA 26.07 ships `xgrammar 0.2.0`.

XGrammar **0.2.1** added/exported `normalize_tool_choice`; its release notes explicitly list:

```text
feat: expose normalize_tool_choice and unify Qwen XML structural tag builders
```

Reference:

https://github.com/mlc-ai/xgrammar/releases/tag/v0.2.1

Therefore `xgrammar 0.2.0` is too old for the tool-parser API that this vLLM build imports.

## apache-tvm-ffi mismatch

On the affected 26.07 container in this setup, `apache-tvm-ffi` was also older than the version required by the newer XGrammar package.

XGrammar 0.2.1 package metadata requires:

```text
apache-tvm-ffi>=0.1.9
```

Reference metadata:

https://wheels.developerfirst.ibm.com/ppc64le/linux-v2026.06.0/xgrammar/0.2.1%2Bppc64le1

Apache TVM FFI 0.1.9 release:

https://github.com/apache/tvm-ffi/releases/tag/v0.1.9

The safe compatibility patch used in this guide therefore upgrades **both** packages together:

```text
apache-tvm-ffi 0.1.9
xgrammar       0.2.1
```

## Verify what is actually inside your NGC image

Before patching, inspect the package versions yourself:

```bash
docker run --rm \
  --entrypoint python3 \
  nvcr.io/nvidia/vllm:26.07-py3 \
  -c 'import importlib.metadata as m; \
print("vllm:", m.version("vllm")); \
print("xgrammar:", m.version("xgrammar")); \
print("apache-tvm-ffi:", m.version("apache-tvm-ffi")); \
print("fastapi:", m.version("fastapi"))'
```

The exact package set in the image is more important than assumptions based on an older guide.

---

# 7. Build the 26.07 xgrammar compatibility image

Do **not** modify the running container interactively if you want a reproducible setup.

Instead, derive a tiny local image from NVIDIA's 26.07 image.

Create:

```bash
cat > Dockerfile.vllm-xgrammar <<'EOF'
ARG BASE_IMAGE=nvcr.io/nvidia/vllm:26.07-py3
FROM ${BASE_IMAGE}

# NVIDIA's 26.07 vLLM code imports normalize_tool_choice, which was first
# exported by xgrammar 0.2.1. The base image also ships apache-tvm-ffi 0.1.7
# in the affected setup even though both vLLM/xgrammar require a newer FFI.
RUN python3 -m pip install --no-cache-dir --no-deps \
    "apache-tvm-ffi==0.1.9" \
    "xgrammar==0.2.1"

RUN python3 -c "from xgrammar import StructuralTag, normalize_tool_choice; print('xgrammar tool-choice API: OK')"
EOF
```

Build it:

```bash
export VLLM_IMAGE="nvcr.io/nvidia/vllm:26.07-py3"
export PATCHED_VLLM_IMAGE="local/nvidia-vllm:26.07-xgrammar021"

docker build \
  --build-arg BASE_IMAGE="$VLLM_IMAGE" \
  -t "$PATCHED_VLLM_IMAGE" \
  -f Dockerfile.vllm-xgrammar .
```

## Why `--no-deps`?

The purpose of the derived image is to change only the two known incompatible packages.

Using:

```bash
--no-deps
```

prevents `pip` from re-resolving and potentially changing NVIDIA's other carefully pinned packages such as Transformers, Torch, FlashInfer, or other runtime dependencies.

We explicitly install the FFI dependency ourselves:

```text
apache-tvm-ffi==0.1.9
```

and then:

```text
xgrammar==0.2.1
```

## Verify the patched image

Check all relevant versions:

```bash
docker run --rm \
  --entrypoint python3 \
  "$PATCHED_VLLM_IMAGE" \
  -c 'import importlib.metadata as m; \
print("vllm:", m.version("vllm")); \
print("xgrammar:", m.version("xgrammar")); \
print("apache-tvm-ffi:", m.version("apache-tvm-ffi")); \
print("transformers:", m.version("transformers")); \
from xgrammar import StructuralTag, normalize_tool_choice; \
print("xgrammar tool-choice API: OK")'
```

You specifically want:

```text
xgrammar: 0.2.1
apache-tvm-ffi: 0.1.9
xgrammar tool-choice API: OK
```

Also verify the vLLM module that previously depended on the missing export:

```bash
docker run --rm \
  --entrypoint python3 \
  "$PATCHED_VLLM_IMAGE" \
  -c 'from vllm.tool_parsers import structural_tag_registry; print("vLLM structural tool parser import: OK")'
```

If that succeeds, the dependency-level tool parser problem is fixed before you spend time loading the model.

> [!important]
> This is a compatibility patch for `nvcr.io/nvidia/vllm:26.07-py3`.
>
> On a later NVIDIA image, **inspect the packaged versions first**. Do not blindly keep overriding dependencies after NVIDIA fixes the image upstream.

# 8. Start Qwen 3.6 35B NVFP4

First remove an old container with the same name. This does **not** delete the model cache:

```bash
docker rm -f vllm-server 2>/dev/null || true
```

## Recommended initial command

This keeps NVIDIA's important Qwen/DGX-Spark settings while avoiding unnecessary extra complexity for the first successful agentic deployment:

```bash
docker run -d \
  --name vllm-server \
  --gpus all \
  --ipc host \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  --entrypoint "" \
  -p 8000:8000 \
  -e HF_TOKEN="$HF_TOKEN" \
  -e HF_HOME=/hf-cache \
  -e HF_HUB_CACHE=/hf-cache/hub \
  --mount type=volume,src=vllm-hf-cache,dst=/hf-cache \
  "$PATCHED_VLLM_IMAGE" \
  vllm serve "$MODEL_HANDLE" \
    --served-model-name qwen36 \
    --tensor-parallel-size 1 \
    --trust-remote-code \
    --kv-cache-dtype fp8 \
    --moe-backend marlin \
    --gpu-memory-utilization 0.5 \
    --max-model-len 262144 \
    --max-num-seqs 8 \
    --max-num-batched-tokens 8192 \
    --enable-chunked-prefill \
    --async-scheduling \
    --enable-prefix-caching \
    --reasoning-parser qwen3 \
    --tool-call-parser qwen3_coder \
    --enable-auto-tool-choice
```

If a later NVIDIA image fixes the xgrammar dependency mismatch and you no longer need the compatibility image, replace:

```bash
"$PATCHED_VLLM_IMAGE"
```

with:

```bash
"$VLLM_IMAGE"
```

## Why `--served-model-name qwen36`?

Without it, the API model ID is normally the full Hugging Face model handle.

This:

```bash
--served-model-name qwen36
```

gives clients a short stable name:

```text
qwen36
```

That makes OpenCode configuration simpler and decouples the client-facing name from the Hugging Face repository name.

---

# 9. Optional: NVIDIA recipe optimizations

The current vLLM DGX Spark recipe for this exact NVFP4 checkpoint also recommends:

```bash
--speculative-config '{"method":"mtp","num_speculative_tokens":3,"moe_backend":"triton"}' \
--load-format fastsafetensors
```

Once the baseline server is confirmed stable, you can add these immediately before the parser flags:

```bash
    --enable-prefix-caching \
    --speculative-config '{"method":"mtp","num_speculative_tokens":3,"moe_backend":"triton"}' \
    --load-format fastsafetensors \
    --reasoning-parser qwen3 \
    --tool-call-parser qwen3_coder \
    --enable-auto-tool-choice
```

The benefit of adding optimizations **after** validating the baseline is simpler troubleshooting: if a new flag causes an issue, you know the core model/server path already works.

---

# 10. Watch the first startup

Follow the server logs:

```bash
docker logs -f vllm-server
```

The first run can include:

```text
Downloading...
Fetching...
Loading safetensors checkpoint shards...
Loading weights...
```

The model is being stored in:

```text
/hf-cache
```

inside the container, which maps to the persistent Docker volume:

```text
vllm-hf-cache
```

## Healthy milestones

Look for lines indicating:

```text
Loading safetensors checkpoint shards: 100%
```

and/or:

```text
Loading weights took ...
```

Then vLLM should finish engine/KV-cache initialization.

Finally, you want:

```text
Application startup complete.
```

The API should now be ready.

---

# 11. Wait for readiness automatically

Instead of repeatedly trying `/v1/models` while the model is loading:

```bash
timeout 900 bash -c '
  until curl -sf http://localhost:8000/health >/dev/null 2>&1; do
    echo "vLLM is still starting..."
    sleep 10
  done
'
```

Then:

```bash
echo "vLLM is ready"
```

---

# 12. Validate the API

## Health

```bash
curl -i http://localhost:8000/health
```

A healthy server should return a successful HTTP response.

## Models

```bash
curl -sS http://localhost:8000/v1/models | python3 -m json.tool
```

Because we used:

```bash
--served-model-name qwen36
```

you should see:

```json
{
  "data": [
    {
      "id": "qwen36"
    }
  ]
}
```

The exact response contains additional fields.

## Basic chat completion

```bash
curl -sS http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen36",
    "messages": [
      {
        "role": "user",
        "content": "Reply with exactly: vLLM is working"
      }
    ],
    "max_tokens": 128
  }' | python3 -m json.tool
```

---

# 13. Why `/v1/models` initially failed

There were two different symptoms during setup.

## Symptom A — connection reset

```text
curl: (56) Recv failure: Connection reset by peer
```

This usually means that port 8000 was reachable but the serving process was not ready or had failed during startup.

Check:

```bash
docker ps -a --filter name=vllm-server
```

and:

```bash
docker logs --tail=200 vllm-server
```

## Symptom B — tool/agent requests fail with xgrammar import errors

With the NVIDIA 26.07 image, ordinary API startup/chat can work while agentic/tool requests fail with an import error resembling:

```text
cannot import name 'normalize_tool_choice' from 'xgrammar'
```

or fail when vLLM loads its structural tool parser.

Check the package versions:

```bash
docker exec vllm-server python3 -c \
'import importlib.metadata as m; print(m.version("xgrammar")); print(m.version("apache-tvm-ffi"))'
```

For the compatibility image in this guide, the expected patched versions are:

```text
xgrammar       0.2.1
apache-tvm-ffi 0.1.9
```

Then test:

```bash
docker exec vllm-server python3 -c \
'from xgrammar import StructuralTag, normalize_tool_choice; print("OK")'
```

and:

```bash
docker exec vllm-server python3 -c \
'from vllm.tool_parsers import structural_tag_registry; print("OK")'
```

References:

- NVIDIA 26.07 package versions: https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-07.html
- XGrammar 0.2.1 release: https://github.com/mlc-ai/xgrammar/releases/tag/v0.2.1
- vLLM structural tool parser: https://github.com/vllm-project/vllm/blob/main/vllm/tool_parsers/structural_tag_registry.py


---

# 14. Understanding the NVFP4 / Marlin warning

You may see:

```text
Your GPU does not have native support for FP4 computation but FP4 quantization
is being used. Weight-only FP4 compression will be used leveraging the Marlin
kernel. This may degrade performance for compute-heavy workloads.
```

This message is confusing on GB10 because DGX Spark is a Blackwell device with FP4 capability.

The important point is that the warning describes the **kernel path selected by vLLM**, not whether the checkpoint itself is actually NVFP4.

The model is still:

```text
nvidia/Qwen3.6-35B-A3B-NVFP4
```

The current vLLM DGX Spark recipe explicitly specifies:

```bash
--moe-backend marlin
```

for this checkpoint.

So when following that recipe, seeing Marlin is not evidence that you accidentally downloaded a BF16 model.

The warning has also been reported as misleading/spurious on FP4-capable Blackwell hardware in vLLM issue discussions.

For this guide, use the backend prescribed by the current model recipe unless newer vLLM/NVIDIA guidance supersedes it.

---

# 15. Why OpenCode initially failed with `"auto" tool choice`

OpenCode is an agentic coding client. It sends tools/functions to the model and allows the model to decide when to invoke them.

That produces an OpenAI-style request containing:

```json
"tool_choice": "auto"
```

vLLM rejects that unless automatic tool calling is enabled.

The required server options are:

```bash
--enable-auto-tool-choice
```

and a model-appropriate parser:

```bash
--tool-call-parser qwen3_coder
```

For Qwen 3.6 35B A3B, the current vLLM recipe specifically uses:

```bash
--reasoning-parser qwen3
--tool-call-parser qwen3_coder
--enable-auto-tool-choice
```

That is why those flags are included in the primary launch command.

---

# 16. Test automatic tool calling before involving OpenCode

This isolates vLLM from client configuration.

Run on the DGX Spark:

```bash
curl -sS http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen36",
    "messages": [
      {
        "role": "user",
        "content": "What is the weather in Doha? Use the weather tool."
      }
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "Get weather for a city",
          "parameters": {
            "type": "object",
            "properties": {
              "city": {
                "type": "string"
              }
            },
            "required": ["city"]
          }
        }
      }
    ],
    "tool_choice": "auto",
    "max_tokens": 512
  }' | python3 -m json.tool
```

The model does not actually execute `get_weather`; this test only checks whether it can generate a structured tool call.

Look in the response for a field resembling:

```json
"tool_calls": [...]
```

If this request succeeds, the server-side OpenCode requirement is working.

---

# 17. Make the DGX Spark endpoint reachable from the client

You have three practical choices.

## Option A — direct LAN access

If the DGX Spark IP is:

```text
192.168.1.50
```

then test from the local machine:

```bash
curl http://192.168.1.50:8000/v1/models
```

The OpenCode base URL becomes:

```text
http://192.168.1.50:8000/v1
```

## Option B — Tailscale

If both systems are on the same Tailscale network, use the DGX Spark's Tailscale IP or MagicDNS hostname:

```text
http://<DGX-TAILSCALE-IP>:8000/v1
```

or:

```text
http://<DGX-MAGICDNS-NAME>:8000/v1
```

Verify from the client first:

```bash
curl http://<DGX-TAILSCALE-IP>:8000/v1/models
```

This is convenient for using the Spark away from the local LAN.

## Option C — SSH tunnel

This keeps vLLM reachable only through SSH.

On the local machine:

```bash
ssh -N -L 8000:127.0.0.1:8000 <username>@<dgx-host>
```

Then use:

```text
http://127.0.0.1:8000/v1
```

from OpenCode.

Test:

```bash
curl http://127.0.0.1:8000/v1/models
```

---

# 18. Configure OpenCode

OpenCode's global configuration lives at:

```text
~/.config/opencode/opencode.json
```

On Windows, the equivalent location is normally:

```text
%USERPROFILE%\.config\opencode\opencode.json
```

Create the directory if necessary.

Linux/macOS:

```bash
mkdir -p ~/.config/opencode
```

## Direct-LAN/Tailscale configuration

Replace the IP/hostname with the DGX Spark address reachable from your client:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "dgx-vllm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "DGX Spark vLLM",
      "options": {
        "baseURL": "http://<DGX-IP-OR-HOSTNAME>:8000/v1",
        "apiKey": "EMPTY"
      },
      "models": {
        "qwen36": {
          "name": "Qwen 3.6 35B A3B NVFP4 - DGX Spark",
          "limit": {
            "context": 262144,
            "output": 32768
          }
        }
      }
    }
  },
  "model": "dgx-vllm/qwen36",
  "small_model": "dgx-vllm/qwen36"
}
```

## SSH-tunnel configuration

Use:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "dgx-vllm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "DGX Spark vLLM",
      "options": {
        "baseURL": "http://127.0.0.1:8000/v1",
        "apiKey": "EMPTY"
      },
      "models": {
        "qwen36": {
          "name": "Qwen 3.6 35B A3B NVFP4 - DGX Spark",
          "limit": {
            "context": 262144,
            "output": 32768
          }
        }
      }
    }
  },
  "model": "dgx-vllm/qwen36",
  "small_model": "dgx-vllm/qwen36"
}
```

> [!note]
> `baseURL` ends at `/v1`.
>
> Correct:
>
> ```text
> http://<host>:8000/v1
> ```
>
> Do **not** configure:
>
> ```text
> http://<host>:8000/v1/chat/completions
> ```

---

# 19. Check OpenCode

Start OpenCode:

```bash
opencode
```

Then use:

```text
/models
```

You should see the custom DGX provider/model.

A simple command-line test is:

```bash
opencode run "Inspect the current project and tell me what language it primarily uses."
```

For a stronger agentic test, ask OpenCode to inspect a project and perform a harmless tool operation such as listing files or reading a small source file.

At the same time, watch the DGX logs:

```bash
docker logs -f vllm-server
```

You should see requests reaching:

```text
/v1/chat/completions
```

---

# 20. OpenCode error: `"auto" tool choice requires ...`

If OpenCode says:

```text
"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set
```

the server was launched without the agent/tool flags.

Confirm the running container arguments:

```bash
docker inspect vllm-server --format '{{json .Args}}' | python3 -m json.tool
```

Look for:

```text
--enable-auto-tool-choice
--tool-call-parser
qwen3_coder
```

If they are missing, recreate the container using the main command in this guide.

The persistent model cache remains intact.

---

# 21. Check exactly what Docker is running

Container image:

```bash
docker inspect vllm-server --format '{{.Config.Image}}'
```

Arguments:

```bash
docker inspect vllm-server --format '{{json .Args}}' | python3 -m json.tool
```

Environment:

```bash
docker inspect vllm-server --format '{{json .Config.Env}}' | python3 -m json.tool
```

Status:

```bash
docker ps -a --filter name=vllm-server
```

Recent logs:

```bash
docker logs --tail=200 vllm-server
```

---

# 22. Useful log filters

For obvious failures:

```bash
docker logs vllm-server 2>&1 | \
  grep -Ei 'error|traceback|exception|unsupported|cuda|oom|out of memory|no space|401|403|modelopt'
```

For startup milestones:

```bash
docker logs vllm-server 2>&1 | \
  grep -Ei 'download|loading|weights|cache|startup complete|uvicorn|route'
```

---

# 23. Check whether the persistent cache contains the model

Inspect the volume:

```bash
docker volume inspect vllm-hf-cache
```

Check its size using the vLLM image itself:

```bash
docker run --rm \
  --entrypoint /bin/bash \
  --mount type=volume,src=vllm-hf-cache,dst=/hf-cache \
  "$PATCHED_VLLM_IMAGE" \
  -lc 'du -sh /hf-cache; find /hf-cache -maxdepth 3 -type d | head -50'
```

If you are no longer using the patched image, replace it with:

```bash
"$VLLM_IMAGE"
```

A successfully downloaded 35B checkpoint should obviously consume many gigabytes rather than a few kilobytes.

---

# 24. Normal day-to-day lifecycle

## Stop the model

```bash
docker stop vllm-server
```

## Start the same container again

```bash
docker start vllm-server
```

Follow logs:

```bash
docker logs -f vllm-server
```

No model re-download should be necessary because the cache is persistent.

---

# 25. Recreate the server without re-downloading the model

This is useful when changing:

- vLLM arguments,
- context length,
- tool parser,
- memory utilization,
- image version,
- or model name.

Remove only the container:

```bash
docker rm -f vllm-server
```

Then run the desired `docker run` command again with:

```bash
--mount type=volume,src=vllm-hf-cache,dst=/hf-cache
```

The model cache remains.

---

# 26. Upgrade to a new vLLM image

Suppose a newer NGC image becomes available.

Set:

```bash
export LATEST_VLLM_VERSION="<NEW_TAG>"
export VLLM_IMAGE="nvcr.io/nvidia/vllm:${LATEST_VLLM_VERSION}"
```

Pull it if necessary:

```bash
docker pull "$VLLM_IMAGE"
```

Inspect the relevant runtime dependency versions before deciding whether the local compatibility image is still necessary:

```bash
docker run --rm \
  --entrypoint python3 \
  "$VLLM_IMAGE" \
  -c 'import importlib.metadata as m; \
print("vllm:", m.version("vllm")); \
print("xgrammar:", m.version("xgrammar")); \
print("apache-tvm-ffi:", m.version("apache-tvm-ffi")); \
print("transformers:", m.version("transformers"))'
```

Then directly verify the tool-choice API:

```bash
docker run --rm \
  --entrypoint python3 \
  "$VLLM_IMAGE" \
  -c 'from xgrammar import StructuralTag, normalize_tool_choice; print("xgrammar tool-choice API: OK")'
```

If this succeeds on the newer NVIDIA image and its dependency versions are compatible, use the unmodified NVIDIA image directly and retire the local compatibility image.

The same:

```text
vllm-hf-cache
```

volume can be attached to the new container, so the checkpoint does not need to be downloaded again.

---

# 27. When to consider the upstream vLLM container

NVIDIA's general Spark playbook and the vLLM recipe ecosystem can reference different container sources depending on the exact model/version.

The current vLLM recipe for this Qwen NVFP4 checkpoint specifically shows an upstream container such as:

```text
vllm/vllm-openai:v0.24.0-ubuntu2404
```

while this guide preserves the NGC route because that is the environment already being used.

If you run into an NGC-specific dependency or feature mismatch, an upstream image is therefore a reasonable fallback.

The architecture remains the same:

```text
upstream vLLM container
       +
vllm-hf-cache Docker volume
       +
same Qwen model
       +
same /v1 API
       +
same OpenCode configuration
```

Do not delete the cache volume just because you switch runtime images.

---

# 28. Full troubleshooting decision tree

## `/health` does not respond

Check:

```bash
docker ps -a --filter name=vllm-server
docker logs --tail=200 vllm-server
```

Likely causes include:

- model still loading,
- container exited,
- OOM/memory pressure,
- unsupported runtime/model combination,
- invalid vLLM option,
- model download/authentication failure.

## Basic chat/API works but tool calling returns HTTP 500

If the error contains:

```text
normalize_tool_choice
```

or another XGrammar structural-tool-parser import failure, verify:

```bash
docker exec vllm-server python3 -c \
'from xgrammar import StructuralTag, normalize_tool_choice; print("xgrammar OK")'
```

and:

```bash
docker exec vllm-server python3 -c \
'from vllm.tool_parsers import structural_tag_registry; print("vLLM tool parser OK")'
```

If either fails on `nvcr.io/nvidia/vllm:26.07-py3`, use the xgrammar/apache-tvm-ffi compatibility image in Section 7.

## `/v1/models` works but chat completion fails

Run:

```bash
curl -sS http://localhost:8000/v1/chat/completions ...
```

directly before debugging OpenCode.

This tells you whether the problem is server-side or client-side.

## Chat works but OpenCode reports `tool_choice: auto`

The server is missing:

```text
--enable-auto-tool-choice
--tool-call-parser qwen3_coder
```

Recreate it with the agentic flags.

## OpenCode cannot connect at all

From the OpenCode machine, test:

```bash
curl http://<DGX-IP>:8000/v1/models
```

If that does not work, OpenCode will not work either.

Solve networking first:

- DGX address,
- LAN routing,
- Tailscale,
- firewall,
- Docker `-p 8000:8000`,
- or SSH tunnel.

## Model re-downloads after container recreation

Check that the run command includes:

```bash
--mount type=volume,src=vllm-hf-cache,dst=/hf-cache
```

and:

```bash
-e HF_HOME=/hf-cache
-e HF_HUB_CACHE=/hf-cache/hub
```

Check:

```bash
docker volume inspect vllm-hf-cache
```

---

# 29. Cleanup and rollback

## Remove only the running server

Safe; model cache stays:

```bash
docker rm -f vllm-server
```

## Remove the local xgrammar compatibility image

After it is no longer needed:

```bash
docker rmi "$PATCHED_VLLM_IMAGE"
```

This does not remove the model cache.

## Remove the original NGC image

Only if you deliberately want to free the Docker image space:

```bash
docker rmi "$VLLM_IMAGE"
```

Again, the named Hugging Face volume remains unless explicitly removed.

## Delete the downloaded models too

Only do this when you intentionally want a complete model-cache reset:

```bash
docker rm -f vllm-server 2>/dev/null || true
docker volume rm vllm-hf-cache
```

> [!danger]
> After `docker volume rm vllm-hf-cache`, the model will have to be downloaded again.

---

# 30. Compact "known-good" sequence for NVIDIA 26.07

This is the short version to return to later.

## One-time setup

```bash
export VLLM_IMAGE="nvcr.io/nvidia/vllm:26.07-py3"
export PATCHED_VLLM_IMAGE="local/nvidia-vllm:26.07-xgrammar021"
export MODEL_HANDLE="nvidia/Qwen3.6-35B-A3B-NVFP4"

docker volume create vllm-hf-cache
```

## Build the 26.07 compatibility image

```bash
cat > Dockerfile.vllm-xgrammar <<'EOF'
ARG BASE_IMAGE=nvcr.io/nvidia/vllm:26.07-py3
FROM ${BASE_IMAGE}

# NVIDIA's 26.07 vLLM code imports normalize_tool_choice, which was first
# exported by xgrammar 0.2.1. The base image also ships apache-tvm-ffi 0.1.7
# in the affected setup even though both vLLM/xgrammar require a newer FFI.
RUN python3 -m pip install --no-cache-dir --no-deps \
    "apache-tvm-ffi==0.1.9" \
    "xgrammar==0.2.1"

RUN python3 -c "from xgrammar import StructuralTag, normalize_tool_choice; print('xgrammar tool-choice API: OK')"
EOF

docker build \
  --build-arg BASE_IMAGE="$VLLM_IMAGE" \
  -t "$PATCHED_VLLM_IMAGE" \
  -f Dockerfile.vllm-xgrammar .
```

Verify before loading a 35B model:

```bash
docker run --rm \
  --entrypoint python3 \
  "$PATCHED_VLLM_IMAGE" \
  -c 'import importlib.metadata as m; \
print("xgrammar:", m.version("xgrammar")); \
print("apache-tvm-ffi:", m.version("apache-tvm-ffi")); \
from xgrammar import StructuralTag, normalize_tool_choice; \
from vllm.tool_parsers import structural_tag_registry; \
print("tool parser dependencies: OK")'
```

## Start

If needed:

```bash
read -rsp "Hugging Face token: " HF_TOKEN
echo
export HF_TOKEN
```

Then:

```bash
docker rm -f vllm-server 2>/dev/null || true

docker run -d \
  --name vllm-server \
  --gpus all \
  --ipc host \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  --entrypoint "" \
  -p 8000:8000 \
  -e HF_TOKEN="$HF_TOKEN" \
  -e HF_HOME=/hf-cache \
  -e HF_HUB_CACHE=/hf-cache/hub \
  --mount type=volume,src=vllm-hf-cache,dst=/hf-cache \
  "$PATCHED_VLLM_IMAGE" \
  vllm serve "$MODEL_HANDLE" \
    --served-model-name qwen36 \
    --tensor-parallel-size 1 \
    --trust-remote-code \
    --kv-cache-dtype fp8 \
    --moe-backend marlin \
    --gpu-memory-utilization 0.5 \
    --max-model-len 262144 \
    --max-num-seqs 8 \
    --max-num-batched-tokens 8192 \
    --enable-chunked-prefill \
    --async-scheduling \
    --enable-prefix-caching \
    --reasoning-parser qwen3 \
    --tool-call-parser qwen3_coder \
    --enable-auto-tool-choice
```

## Watch

```bash
docker logs -f vllm-server
```

## Test health and models

```bash
curl -i http://localhost:8000/health
```

```bash
curl -sS http://localhost:8000/v1/models | python3 -m json.tool
```

## Test the tool parser

Use the explicit tool-call curl example in Section 16 before debugging OpenCode.

## Stop

```bash
docker stop vllm-server
```

## Restart

```bash
docker start vllm-server
```

## Recreate without downloading the model again

```bash
docker rm -f vllm-server
```

Then rerun the server command above.

As long as you keep:

```text
vllm-hf-cache
```

the Hugging Face model files remain persistent.

---

# 31. Sources and references

## NVIDIA / DGX Spark

- NVIDIA — **Serve LLMs with vLLM on DGX Spark**  
  https://build.nvidia.com/spark/vllm/instructions

- NVIDIA — **Agent-ready models: Qwen3.6-35B-A3B NVFP4 on DGX Spark**  
  https://build.nvidia.com/spark/vllm/agent-ready-models

- NVIDIA — **vLLM 26.07 release notes**  
  Confirms the 26.07 container includes vLLM 0.24.0, Transformers 5.6.1, and xgrammar 0.2.0.  
  https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-07.html

- NVIDIA NGC — **vLLM 26.07-py3 container**  
  https://catalog.ngc.nvidia.com/orgs/nvidia/-/containers/vllm/26.07-py3

- vLLM Recipes — **Qwen/Qwen3.6-35B-A3B**  
  https://recipes.vllm.ai/Qwen/Qwen3.6-35B-A3B

## xgrammar / tool-calling compatibility

- XGrammar — **v0.2.1 release**  
  This release explicitly added/exported `normalize_tool_choice`.  
  https://github.com/mlc-ai/xgrammar/releases/tag/v0.2.1

- XGrammar — **current `normalize_tool_choice` implementation**  
  https://github.com/mlc-ai/xgrammar/blob/main/python/xgrammar/builtin_structural_tag.py

- vLLM — **structural tool parser registry**  
  Shows vLLM importing `StructuralTag` and `normalize_tool_choice` from xgrammar.  
  https://github.com/vllm-project/vllm/blob/main/vllm/tool_parsers/structural_tag_registry.py

- XGrammar 0.2.1 package metadata — **requires `apache-tvm-ffi>=0.1.9`**  
  https://wheels.developerfirst.ibm.com/ppc64le/linux-v2026.06.0/xgrammar/0.2.1%2Bppc64le1

- Apache TVM FFI — **v0.1.9 release**  
  https://github.com/apache/tvm-ffi/releases/tag/v0.1.9

## OpenCode

- OpenCode — **Providers / custom OpenAI-compatible provider**  
  https://opencode.ai/docs/providers/

- OpenCode — **Configuration locations and precedence**  
  https://opencode.ai/docs/config

## vLLM general documentation

- vLLM — **OpenAI-compatible server**  
  https://docs.vllm.ai/en/latest/serving/openai_compatible_server/

- vLLM — **Tool calling**  
  https://docs.vllm.ai/en/latest/features/tool_calling/

- vLLM documentation home  
  https://docs.vllm.ai/en/latest/

## Historical troubleshooting note

An earlier container/dependency combination encountered during this setup also exposed a FastAPI 0.137 `_IncludedRouter` problem. That is **not the current 26.07 workaround used by this guide**, but these references are retained for historical troubleshooting if an older image reproduces it:

- vLLM issue search / tracker  
  https://github.com/vllm-project/vllm/issues

- FastAPI discussion around `_IncludedRouter` / route internals  
  https://github.com/fastapi/fastapi/discussions/15791

---

# 32. Recommended steady-state setup

For this particular system, the clean steady-state arrangement is:

```text
DGX Spark
├── NVIDIA/compatible vLLM Docker image
├── vllm-server container
│   └── disposable / safely recreatable
│
├── vllm-hf-cache Docker volume
│   └── persistent / keep this
│
└── TCP 8000
    └── OpenAI-compatible /v1 API

Development machine
└── OpenCode
    └── dgx-vllm/qwen36
        └── http://DGX:8000/v1
```

The mental model to keep is:

> **Container = disposable runtime.  
> Docker volume = persistent model storage.  
> `/v1` = client contract.**

That separation makes upgrading vLLM, changing launch flags, troubleshooting OpenCode, or swapping model runtimes much less risky.
