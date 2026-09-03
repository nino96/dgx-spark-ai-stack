# Compatibility notes

Knowledge carried forward from `docs/legacy/COMPATIBILITY.md` and
`docs/legacy/vllm-playbook/`, plus what workstream C's implementation notes
surfaced while building `compose/images/*`. This is the "why is this pin
here, and when is it safe to remove" reference — read it before any bump in
`docs/UPGRADES.md` that touches vLLM, llama.cpp, or a model-specific quirk.

## vLLM: xgrammar 0.2.1 + apache-tvm-ffi 0.1.9 pin (NGC vllm 26.07-py3)

**Why it exists**: `nvcr.io/nvidia/vllm:26.07-py3` (vLLM 0.22.1+NVIDIA)
imports `xgrammar.normalize_tool_choice` for its tool-choice/structured-output
code path. The base image's own xgrammar does not export that symbol, and
apache-tvm-ffi in the base image is 0.1.7 while both vLLM and xgrammar 0.2.1
require 0.1.9. Without the fix, the container fails at import time.

**The fix** (`compose/images/vllm/Dockerfile`): `pip install --no-deps
apache-tvm-ffi==0.1.9 xgrammar==0.2.1`, then a build-time verification step
(`from xgrammar import StructuralTag, normalize_tool_choice`) that fails the
build loudly if a future base-image bump makes the layer a silent no-op.
This is the known-good lineage that was already running in production before
this refactor (`docs/legacy/vllm-playbook/Dockerfile.vllm-26.07-xgrammar-fix`,
`build-vllm.sh`, `start-vllm.sh`).

**Removal gate** (per the legacy doc, still valid): before dropping this
layer on a newer NGC image, confirm the image's shipped xgrammar already
exports `normalize_tool_choice` at >= 0.2.1, then exercise `/health`,
`/metrics`, `/v1/models`, sync + streaming chat, tools, cancellation, and the
Responses API, plus 20+ concurrent short requests with zero HTTP 500s, before
removing the pin.

## FastAPI 0.137 metrics-middleware breakage (watch item, fixed upstream)

FastAPI 0.137 changed route collection; older vLLM Prometheus
instrumentation called `.path` on a route object that no longer has one,
producing HTTP 500 on **every** request. Upstream fixed this in
[vLLM PR #45629](https://github.com/vllm-project/vllm/pull/45629) (tracking
[issue #45597](https://github.com/vllm-project/vllm/issues/45597)). This
box's legacy vLLM deployment carried a standalone fix layer for this
(`docs/legacy/vllm-playbook/Dockerfile.vllm-fastapi-pin`:
`pip install --upgrade "fastapi[standard]<0.137"`).

**Status in this repo**: `compose/images/vllm/Dockerfile` as written does
**not** currently carry this pin — only the xgrammar/tvm-ffi layer above.
Treat this as a **watch item**: if `nvcr.io/nvidia/vllm:26.07-py3` (or any
future NGC tag) starts returning HTTP 500 on every route, this is almost
certainly the cause. Fix by either (a) confirming the base image already
contains vLLM's PR #45629 fix (then no action needed), or (b) adding
`pip install --upgrade "fastapi[standard]<0.137"` back as a Dockerfile
layer, same pattern as the xgrammar fix and the legacy Dockerfile above.
Test:
`/health`, `/metrics`, `/v1/models`, chat, streaming, and 20+ concurrent
requests before considering either state "confirmed good."

## llama.cpp CMake flags: sm_121/121a nuances

`compose/images/llamacpp/Dockerfile` builds with
`-DCMAKE_CUDA_ARCHITECTURES=121` per `docs/CONTRACTS.md` §4, verbatim. Two
things worth knowing before touching this:

- **`-DGGML_CUDA_F16=ON` is an obsolete flag.** It no longer exists as a
  CMake option in `ggml/CMakeLists.txt` / `ggml-cuda`'s `CMakeLists.txt` at
  the currently pinned llama.cpp commit (`f9e832c`, ref `b10289`) — confirmed
  by inspecting a local from-source checkout on this box that shares the
  same ggml-cuda backend code. CMake emits "Manually-specified variables
  were not used by the project" and ignores it; this is harmless, not a
  build failure. It's kept in the Dockerfile for literal `docs/CONTRACTS.md`
  §4 compliance. **Action item**: drop the flag from both
  `docs/CONTRACTS.md` §4 and the Dockerfile together in a future cleanup
  commit, once someone confirms it's safe to stop carrying it.
- **`121` vs. `121a-real`.** GB10 is sm_121 (Blackwell). llama.cpp's own
  native-arch detection (`GGML_NATIVE=ON`, the ggml default, when a GPU is
  visible at configure time) would resolve to the Blackwell-specific
  `121a-real` variant, not plain `121`. But no GPU is visible inside a
  `docker build`, so native detection can't run at build time regardless —
  the Dockerfile sets `GGML_NATIVE=OFF` and uses the explicit `121` from
  `docs/CONTRACTS.md` §4 as-is. If a build's kernels misbehave at runtime in
  a way that smells like an arch mismatch, try rebuilding with
  `-DCMAKE_CUDA_ARCHITECTURES=121a-real` instead — this is a `docs/CONTRACTS.md`
  wording-vs-actual-ggml-cmake question, not something resolved yet.

## GB10 has no NVML/nvidia-smi memory stats

Unified 121 GiB CPU/GPU memory; `nvidia-smi`/NVML report no usable GPU memory
figures on this board. There is deliberately no DCGM/nvidia-exporter
container in the monitoring profile for the same reason — it would have
nothing real to report. All memory budgeting (`bin/modelctl`'s activation
precheck, `bin/doctor`'s `mem_baseline` check, Grafana's memory panel) reads
`/proc/meminfo` `MemAvailable` (`node_exporter`'s
`node_memory_MemAvailable_bytes` for the Grafana path) instead. GPU
utilization/temperature/power/clocks ARE available, but only via the
`nvidia-smi` CLI directly on the host — there's no exporter wired up for
them (`watch -n1 nvidia-smi` is the supported way to watch them live).

## sglang `:spark` maturity caveats

`lmsysorg/sglang:spark` is pulled unbuilt and pinned by digest (once
resolved — currently `digest: null`, never pulled/verified from this repo's
stack as of this writing). Treat as experimental-tier per
`docs/CONTRACTS.md` §4/§7: no catalog entry currently targets it, no sanity
history exists for it on this stack, and `config/versions.lock.yaml` /
`compose/compose.yml` both still reference it by tag only. Before promoting
any sglang-backed model out of experimental status, run the same
sanity+acceptance gate as any other backend bump (`docs/UPGRADES.md`) and
record the resolved digest.

## Known model-specific quirks

- **DeepSeek V4 Flash (`deepseek-v4-flash`, IQ2XXS/w2Q2K quant) — quality
  caveat at Q2.** This is an aggressive low-bit quant (effectively exclusive
  at `budget_gib: 100` — it's sized to use nearly the whole machine). Q2-class
  quantization is known to degrade output quality more than higher-bit quants,
  particularly on tasks requiring precise instruction-following or long
  chains of reasoning. No systematic quality regression test has been run
  against this specific quant on the new (llamacpp, non-ds4) serving path
  yet — `bin/modelctl sanity deepseek-v4-flash` plus a `gsm8k_cot`/`arc_easy`
  `lm-eval` baseline run (`docs/OPERATIONS.md`) should be the first thing
  done after this model's first activation on the refactored stack, so
  there's an archived reference point for `compare.py` on any future
  drafter/quant change.
- **Qwen3.8-Flash-Next (`qwen3.8-flash-next`, experimental) needs the
  n-gram-SSD-offload llama.cpp fork.** This is **not** the mainline pinned
  llama.cpp commit used by `compose/images/llamacpp/` — it requires a
  community fork with n-gram SSD-offload support, built natively on-host and
  run under its own systemd unit outside the compose stack entirely (see
  `config/experimental.yaml`'s note on this entry). Do not expect
  `spark-ai-ctl backend-start` to ever touch this model; it is deliberately
  outside that path.
- **`tool_choice=required` returning HTTP 500 on the legacy 26.07-lineage
  vLLM image — needs re-testing on the new stack.** This failure mode was
  observed during prior sanity-suite validation against the legacy
  (pre-refactor) vLLM deployment on this box; it has not been re-confirmed
  or re-triggered against `spark-ai/vllm:26.07-xg021` as built by this
  repo's `compose/images/vllm/Dockerfile`. `evals/sanity/run.py`'s
  `tool_call` probe (`docs/CONTRACTS.md` §10) forces exactly
  `tool_choice="required"` against a trivial weather tool for this reason —
  it is the fast-path check that would catch a recurrence immediately after
  any vLLM bump (`docs/UPGRADES.md`'s vLLM procedure calls this out
  explicitly). Until re-tested and either confirmed-fixed or
  confirmed-reproduced on the new image, treat any tool-call failure on a
  freshly bumped vLLM image as this quirk resurfacing, not a new bug.
- **`qwen3.8-27b` (vision, secondary slot) has unvalidated vision-specific
  serving flags.** `config/models.yaml`'s entry mirrors `qwen3.6-27b`'s
  baseline NVFP4 flags (`--quantization modelopt --trust-remote-code`) but
  does not set any vision-specific flags (e.g. `--limit-mm-per-prompt`, a
  vision chat template) — none were specified anywhere in
  `docs/CONTRACTS.md` or the old catalog. Run `bin/modelctl sanity
  qwen3.8-27b` (which includes the `vision` probe for `role: vision` models)
  on first activation and expect to need to add flags before it correctly
  serves image content.
