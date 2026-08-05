# Implementation plan

## Objective

Build a standalone Git repository that can reproduce the application layer of
an ASUS Ascent GX10 or NVIDIA DGX Spark and expose all active inference
backends through one authenticated OpenAI-compatible endpoint. Runtime data is
under `~/ai-data`; secrets are in
`~/.config/spark-ai-stack/secrets.env` with mode `0600`. Neither is tracked.

The first implementation is deliberately single-machine and single-user.
Hugging Face is the artifact source, not another serving backend. Distributed
Spark inference and automatic SearXNG tool injection are out of scope.

## Decisions

1. Use Ansible for host convergence and Docker Compose for container services.
2. Use LiteLLM as the stable gateway. It binds to loopback with a master key.
3. Boot the gateway, SearXNG, and the NVIDIA Qwen3.6 35B NVFP4 profile.
4. Publish gateway `/v1` and SearXNG `/search/` only through Tailscale Serve.
5. Keep vendor firmware, BIOS, kernel, GPU driver, CUDA, and DGX OS outside
   Ansible. Unsupported hosts stop at preflight with vendor recovery guidance.
6. Run Qwen and the 4B llama.cpp profile together only after a local soak gate
   is marked passed. All new combinations are exclusive by default.
7. Reserve 8 GiB of unified host memory. Activation requires declared budgets
   and an allowlisted combination.
8. DS4 is always exclusive. Stopping DS4 restores Qwen automatically.
9. API requests do not cold-start models. The route table contains active
   models only, plus `local/default` pointing at the current default.
10. Keep NVIDIA Qwen as the default. Unsloth Fast remains disabled until a
    same-machine A/B gate demonstrates stable, material improvement without a
    task-suite regression.

## Locked inputs

The machine-readable source of truth is `config/versions.lock.yaml`.

| Artifact | Locked identity |
|---|---|
| Entrpi DS4 | `82d2a6f7ec580d979f8352d3249ad5415a321e89` |
| llama.cpp `b10289` | `f9e832c10e9444cb168ddcb579cc62c154f3068b` |
| NVIDIA Qwen3.6 35B NVFP4 | `491c2f1ea524c639598bf8fa787a93fed5a6fbce` |
| Qwen3.5 4B GGUF | `4168f45a16a1290d65a4ec0fa312ae917a4c15d6` |
| Qwen GGUF SHA-256 | `13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983` |
| DS4 base SHA-256 | `ca22ae2f838e14077c22bc1c1417b71b45b5e5a3687bd96c2ac6e17fdb6261c0` |
| DS4 drafter SHA-256 | `8fa269560dc76fd73e4233ad9b1938b5f65dd363381fd9b1a5c6183f7d12d686` |
| Unsloth candidate | `1c3f884bc99aac2524f6d49bcbac8c88401afd66` (disabled) |
| NGC vLLM ARM64 | `26.06-py3@sha256:47539d1e...b279a` |
| LiteLLM | `v1.86.2@sha256:c0fded5f...6e8f1` |
| SearXNG ARM64 | `2026.7.3@sha256:a27984e8...c0c7` |
| FastAPI workaround | exact `0.136.3` |
| Cosign ARM64 | `v3.0.6`, SHA-256 `bedac92e...ef2b8` |

LiteLLM v1.86.2 is intentionally newer than the compromised PyPI releases
1.82.7 and 1.82.8. Only the official signed container is used; `bin/doctor
--verify-images` verifies the signed release-index digest using the public key
fetched from its immutable introducing commit and checked by SHA-256.

## Work packages

### Provisioning

- Bootstrap a pinned Ansible virtual environment.
- Detect DMI vendor/product, `/etc/dgx-release`, Ubuntu 24.04 ARM64, GB10
  compute capability 12.1, CUDA 13, Docker, Compose, NVIDIA Container Toolkit,
  Tailscale, memory, and disk.
- Support `asus_gx10` and `nvidia_dgx_spark` inventory variants.
- Install only missing application dependencies and Tailscale.
- Build DS4 and llama.cpp at locked commits for `sm_121`.
- Install systemd user units and enable linger for boot without login.

### Artifact handling

- Download with explicit Hugging Face revisions.
- Store Hugging Face cache, GGUFs, source, builds, caches, and controller state
  under `~/ai-data`.
- Verify GGUF size and SHA-256 before activation.
- Verify Git worktrees are at their locked commits.
- Verify all image references include digests and the LiteLLM signature passes.

### Service and routing layer

- Bind LiteLLM, vLLM, llama.cpp, DS4, and SearXNG to their assigned loopback
  ports.
- Generate LiteLLM routes atomically from `active.json`.
- Restart the gateway only after a new backend is healthy.
- Keep the prior active state and route file for rollback.
- Publish with Tailscale Serve; never enable Funnel.

### Lifecycle controller

`bin/modelctl` implements `catalog`, `fetch`, `verify`, `activate`,
`deactivate`, `status`, `logs`, `routes`, and JSON output. Activation takes an
exclusive lock, validates artifacts and memory policy, saves a transaction
snapshot, stops conflicts, starts the target, waits for health, swaps routes,
and persists state. Any failure restores the previous active services and
routes. A request to LiteLLM can never start a stopped backend.

### Existing-machine migration

- Import the old Hugging Face token only when explicitly requested.
- Generate new LiteLLM and SearXNG secrets; never copy the old SearXNG secret.
- Adopt the two known GGUFs by hard link when source and target share a
  filesystem, otherwise copy without deleting the source; then hash them.
- Export the read-only `vllm-hf-cache` named volume into the bind-mounted cache.
- Keep old units, source trees, paths, images, containers, and volumes for
  rollback. The old SearXNG container is stopped only immediately before the
  replacement claims port 8888.
- Run legacy retirement only with the separate `bin/cleanup-legacy` tool after
  parity and reboot have been explicitly confirmed.

## Rollout sequence

1. Run static validation and controller unit tests.
2. Run `bootstrap`, `stack secrets-init`, and `stack migrate` on the existing
   GX10 without stopping old services.
3. Run Ansible preflight and build native artifacts.
4. Verify model hashes and source commits.
5. Start the replacement Qwen backend and gateway; test on loopback.
6. Stop old SearXNG immediately before starting replacement SearXNG.
7. Configure Tailscale Serve and test auth from a tailnet client.
8. Reboot and rerun the complete acceptance suite.
9. Record Qwen+llama soak evidence and only then change the allowlist status.
10. Keep rollback assets for an operator-chosen retention period. Cleanup is a
    separate, explicit operation.

## Acceptance criteria

- A second Ansible run reports no changes.
- CI validates Ansible, YAML/JSON, Compose, shell scripts, controller tests,
  secrets, and large-file exclusions.
- GPU container, DS4 CUDA, and llama.cpp SM121 checks pass.
- Every model revision and checksum verifies.
- After reboot, gateway, search, and Qwen start without an interactive login.
- No AI/search service listens on `0.0.0.0` at the host boundary.
- Tailscale HTTPS works; missing or invalid gateway API keys return `401`.
- Every active backend passes model listing, sync/streaming chat, tools,
  cancellation, and supported Responses API tests through the unified URL.
- Qwen plus llama.cpp passes the concurrent reserve/swap soak before its
  allowlist status changes to `passed`.
- DS4 activation and Qwen restoration preserve the client URL.
- Failed, unhealthy, or over-budget activation restores the previous state.
- Unsloth Fast is promoted only after its documented A/B gate passes.

## Definition of done

The repository can take a vendor-recovered GX10 or DGX Spark from verified
host baseline to an authenticated, reboot-persistent endpoint without manual
service edits. The checked-in lockfile and documentation are sufficient to
explain, reproduce, test, upgrade, and roll back every application-layer
decision.
