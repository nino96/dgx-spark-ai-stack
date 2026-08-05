# Compatibility and promotion gates

## Recorded GX10 baseline

The repository was prepared from an ASUS Ascent GX10 with DMI product GX10,
DGX OS base 7.2.3 / OTA 7.5.0, Ubuntu 24.04 ARM64, kernel
`6.17.0-1021-nvidia`, NVIDIA driver 580.159.03, CUDA 13.0.88, GB10 compute
capability 12.1, Docker 29.2.1, Compose 5.0.2, NVIDIA Container Toolkit
1.19.1, and about 121 GiB usable unified memory.

These values are evidence, not an instruction to downgrade or force package
versions onto another OEM. `doctor` reports differences and enforces only the
supported capabilities/major boundaries.

## FastAPI workaround

The locked NGC vLLM image contains vLLM 0.22.1+NVIDIA. FastAPI 0.137 changed
the route collection and older Prometheus instrumentation accessed `.path` on
a route that does not have it. The result is HTTP 500 for all requests. The
local image therefore layers exact FastAPI 0.136.3.

Relevant upstream work:

- [vLLM bug #45597](https://github.com/vllm-project/vllm/issues/45597)
- [vLLM fix #45629](https://github.com/vllm-project/vllm/pull/45629)

Removal gate for a replacement NGC image:

1. Build a candidate without the `pip install fastapi==0.136.3` layer.
2. Confirm the installed vLLM contains the metrics route-walk fix.
3. Exercise `/health`, `/metrics`, `/v1/models`, sync and streaming chat,
   tools, cancellation, and supported Responses API.
4. Run at least 20 concurrent short requests with no HTTP 500s.
5. Record image digest, package versions, and test output in the upgrade PR.

## Unsloth Fast candidate

`unsloth/Qwen3.6-35B-A3B-NVFP4-Fast` is locked but disabled. Published
throughput claims were measured on different hardware and high concurrency.
The candidate also recommends a newer vLLM/FlashInfer combination than the
current NGC image. Promotion requires a same-GX10 A/B against NVIDIA Qwen at
identical context, request corpus, batch/concurrency, and gateway settings.

Pass conditions:

- cold load and 30-minute soak are stable;
- outputs pass the repository task suite with no material regression;
- tool calls, reasoning parsing, streaming, and cancellation behave correctly;
- memory reserve and no-swap rules hold;
- median and p95 throughput/latency show a material operator-defined gain;
- the candidate revision, runtime image digest, and evidence are committed.

Until all conditions pass, NVIDIA Qwen remains `local/default`.

## Tailscale path mounts

Tailscale Serve removes a configured mount prefix before proxying. The stack
therefore maps public `/v1` to the backend target ending in `/v1`, and public
`/search` to the backend target ending in `/search`. This preserves the API
paths expected by LiteLLM and SearXNG. Recheck this behavior when upgrading
Tailscale because its Serve CLI and configuration format have changed before.

Reference: [Tailscale Serve command](https://tailscale.com/docs/reference/tailscale-cli/serve)
