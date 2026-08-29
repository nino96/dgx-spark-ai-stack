# CONTRACTS — Interface Spec (source of truth for the refactor)

Every component MUST follow this spec. If something here proves unimplementable, stop and flag it
instead of silently deviating. Machine: ASUS Ascent GX10 (`gx10-b210`), GB10 sm_121 aarch64,
121GiB unified memory, Ubuntu 24.04, CUDA 13.0, Docker 29.x / Compose v5. GPU memory stats are NOT
available via nvidia-smi/NVML on GB10 — all memory accounting uses `/proc/meminfo` MemAvailable.

## 1. Host paths

| Path | Owner/Mode | Purpose |
|---|---|---|
| `/home/niyam-gb10/code/spark-ai-stack` | user | this repo (user-writable, UNTRUSTED by privileged code) |
| `/home/niyam-gb10/ai-data` | user | DATA_ROOT |
| `~/ai-data/huggingface` | user | HF cache (HF_HOME) mounted into backends |
| `~/ai-data/models/gguf` | user | GGUF files mounted into llamacpp |
| `~/ai-data/kb` | user | knowledge-base dir (future Hermes hook) |
| `~/ai-data/state` | user | modelctl orchestration state (active.json, lock) |
| `/etc/spark-ai-stack/` | root:root 0755 | root-owned config root |
| `/etc/spark-ai-stack/secrets.env` | root:root 0600 | ALL secrets (see §6) |
| `/etc/spark-ai-stack/compose/` | root:root 0644 files | **synced** copies of repo compose files + litellm template |
| `/etc/spark-ai-stack/models.yaml` | root:root 0644 | **synced** copy of the model catalog |
| `/run/spark-ai-stack/` | root:root 0755 | wrapper-rendered runtime files (override, litellm config), tmpfiles.d |
| `/var/lib/spark-ai-stack/active.json` | root:root 0644 | authoritative active-model state (written by wrapper only) |
| `/usr/local/sbin/spark-ai-ctl` | root:root 0755 | privileged wrapper (NOPASSWD sudo target) |
| `/etc/sudoers.d/spark-ai-stack` | root:root 0440 | sudoers drop-in |
| `/etc/systemd/system/spark-ai-core.service` | root | boots core stack |
| `/etc/systemd/system/spark-ai-boot-model.service` | root | runs ensure-boot after core |

## 2. Security model (LOAD-BEARING — do not weaken)

Threat model: AI coding agents run as `niyam-gb10`. That user is REMOVED from the `docker` group
at cutover. Invariants:

1. Privileged code (`spark-ai-ctl`, systemd units) NEVER reads user-writable files. Compose files,
   catalog, and litellm template are consumed only from `/etc/spark-ai-stack/` (root-owned copies).
2. Repo → `/etc` copying happens ONLY via `sudo sparkctl sync` (password-gated, prints a diff
   before copying; that diff review is the human trust gate).
3. `spark-ai-ctl` is the only NOPASSWD sudo target. It accepts ONLY enumerated verbs and
   `choices=`-restricted arguments (argparse). No free-form paths, no `-f`, no env passthrough,
   `shell=False` everywhere. It renders all runtime inputs (compose override, litellm config)
   itself from root-owned sources into `/run/spark-ai-stack/`.
4. Rendered overrides may only: set `command:`, set `ports:` to `127.0.0.1:<slot port>:<container
   port>`, set non-secret `environment:` keys. Volume mounts in overrides are limited to
   hardcoded prefixes (`~/ai-data/huggingface`, `~/ai-data/models/gguf`) — enforced by the
   wrapper's render code, which is the only author of the file.
5. Secrets reach containers via `env_file: /etc/spark-ai-stack/secrets.env` entries in the
   root-owned compose files, split per service (each container gets ONLY its keys — see §6).
   Postgres uses compose `secrets:` + `POSTGRES_PASSWORD_FILE`.
6. Every service binds `127.0.0.1` only. Tailnet exposure only via `tailscale serve` (§8).
7. `HF_TOKEN` is injected ONLY into backend containers (vllm/llamacpp/sglang), never litellm
   (a user-writable litellm route file could otherwise exfiltrate env refs — routes are
   root-rendered anyway, defense in depth).

Residual risk (document, don't hide): root is trusted; `sudo sparkctl sync` rubber-stamping is the
human gate; the wrapper is new privileged code and gets line-by-line review.

## 3. Port map (all loopback)

| Port | Service |
|---|---|
| 3000 | openwebui |
| 3001 | grafana (monitoring profile) |
| 4000 | litellm gateway |
| 8001 | model slot `primary` |
| 8002 | model slot `secondary` |
| 8010 | whisper (utilities profile, stub initially) |
| 8011 | embeddings (utilities profile) |
| 8888 | searxng (kept from legacy so t640 URL change is path-only) |
| 9090 | prometheus (monitoring profile) |
| 9100 | node_exporter (monitoring profile) |

## 4. Compose topology

- Project name: `spark-ai`. Files in repo: `compose/compose.yml`, `compose/monitoring.yml`.
  Consumed by privileged code from `/etc/spark-ai-stack/compose/`.
- `compose/compose.yml` services:
  - Core (no profile): `litellm`, `openwebui`, `postgres`, `searxng`.
  - `profiles: [models]`: `vllm`, `llamacpp`, `sglang` — skeletons only: image, GPU reservation,
    `ipc: host`, volumes (HF cache for vllm/sglang; gguf dir for llamacpp), healthcheck,
    `security_opt: [no-new-privileges:true]`, restart policy — **no `command:`, no `ports:`**
    (both come from the wrapper-rendered override).
  - `profiles: [utilities]`: `embeddings` (llamacpp image serving an embedding GGUF), `whisper`
    (defined but may start as a documented stub).
- Wrapper invocation shape (hardcoded inside `spark-ai-ctl`):
  `docker compose --project-name spark-ai -f /etc/spark-ai-stack/compose/compose.yml
   [-f /etc/spark-ai-stack/compose/monitoring.yml] [-f /run/spark-ai-stack/override.yaml] <verb>`
- Named volumes: `spark-ai-pgdata`, `spark-ai-openwebui`, `spark-ai-grafana`, `spark-ai-prometheus`.
- Images (pin by digest in `config/versions.lock.yaml`):
  - vllm: build from `compose/images/vllm/Dockerfile` — base `nvcr.io/nvidia/vllm:26.07-py3`
    + `apache-tvm-ffi==0.1.9` + `xgrammar==0.2.1` (known-good lineage currently running; see
    `docs/legacy/vllm-playbook/`). Tag `spark-ai/vllm:26.07-xg021`.
  - llamacpp: build from `compose/images/llamacpp/Dockerfile` — CUDA 13 devel base (aarch64),
    llama.cpp pinned commit, `-DGGML_CUDA=ON -DGGML_CUDA_F16=ON -DCMAKE_CUDA_ARCHITECTURES=121`,
    runtime stage with `llama-server`. Tag `spark-ai/llamacpp:<commit-short>`.
  - sglang: `lmsysorg/sglang:spark` pinned by digest (experimental-tier backend).
  - litellm: `ghcr.io/berriai/litellm` (digest-pinned; version chosen at implementation time).
  - openwebui: `ghcr.io/open-webui/open-webui` (digest-pinned).
  - postgres: `postgres:17` (digest-pinned, official arm64).
  - searxng: `searxng/searxng` (digest-pinned).

## 5. `spark-ai-ctl` verb interface (Python 3, argparse; stdlib + PyYAML from apt `python3-yaml` only)

```
spark-ai-ctl core-up | core-down | core-restart | core-ps
spark-ai-ctl backend-start <model-id>     # model-id validated against /etc/spark-ai-stack/models.yaml
spark-ai-ctl backend-stop  <slot|model-id>
spark-ai-ctl gateway-reload               # re-render litellm config from active.json + template, restart litellm
spark-ai-ctl monitoring-up | monitoring-down
spark-ai-ctl utilities-up | utilities-down
spark-ai-ctl secrets-init                 # create/complete secrets.env (openssl rand), never overwrite set keys
spark-ai-ctl secrets-status               # key names + set/unset + length only, NEVER values
spark-ai-ctl secrets-rotate <KEY>         # KEY in fixed choices list (§6)
spark-ai-ctl sync                         # diff + copy repo compose/catalog/template → /etc/spark-ai-stack
spark-ai-ctl status                       # active.json + compose ps, machine-readable JSON on --json
```
- `backend-start`: reads root catalog → validates model exists & not experimental (unless
  `--experimental`) → renders `/run/spark-ai-stack/override.yaml` (per §2.4) → `compose up -d
  <backend service>` → updates `/var/lib/spark-ai-stack/active.json` → re-renders
  `/run/spark-ai-stack/litellm-config.yaml` from template + active set → does NOT wait for health
  (user-side modelctl polls).
- All state-mutating verbs take an flock on `/run/spark-ai-stack/lock`.
- `sync` and `secrets-*` are EXCLUDED from the NOPASSWD sudoers line (password required):
  sudoers grants NOPASSWD only for: `spark-ai-ctl core-*`, `backend-*`, `gateway-reload`,
  `monitoring-*`, `utilities-*`, `status`. (Implement as two sudoers lines with explicit
  argument words, or a NOPASSWD line per allowed verb — no wildcards after the verb.)

## 6. Secrets (`/etc/spark-ai-stack/secrets.env`)

Keys (fixed set): `LITELLM_MASTER_KEY`, `POSTGRES_PASSWORD`, `WEBUI_SECRET_KEY`,
`SEARXNG_SECRET`, `HF_TOKEN`.
Per-service exposure (via per-service root-owned env files derived at `secrets-init`/`sync` time
into `/etc/spark-ai-stack/env.d/<service>.env`, each 0600):
- litellm: `LITELLM_MASTER_KEY`, `DATABASE_URL` (derived from POSTGRES_PASSWORD)
- openwebui: `WEBUI_SECRET_KEY`, `OPENAI_API_KEY=$LITELLM_MASTER_KEY` (points at litellm)
- searxng: `SEARXNG_SECRET`
- vllm / llamacpp / sglang: `HF_TOKEN` (+ `HF_HOME=/data/hf`)
- postgres: file-based secret only.
NGC registry auth (`docker login nvcr.io`) lives in root's `~/.docker/config.json`.

## 7. Model catalog schema (`config/models.yaml`, real YAML; experimental entries in `config/experimental.yaml`)

```yaml
schema_version: 2
defaults:
  host_reserve_gib: 10
  health: {path: /v1/models, timeout_s: 1200}
models:
  qwen3.6-35b:
    backend: vllm            # vllm | llamacpp | sglang | ds4(experimental)
    slot: primary            # primary | secondary
    role: text               # text | vision | utility
    boot: true               # exactly one model may set this
    budget_gib: 72
    source:
      hf_repo: nvidia/Qwen3.6-35B-A3B-NVFP4
      revision: <pin at fetch time>
    served_model_name: qwen3.6-35b
    args: []                 # backend CLI args; for this model port from docs/legacy/vllm-playbook/start-vllm.sh (known-good MTP/flashinfer config)
    health: {timeout_s: 1200}
```
Catalog entries to define: `qwen3.6-35b` (above, boot), `qwen3.6-27b` (vllm,
`nvidia/Qwen3.6-27B-NVFP4`, args per model card: `--quantization modelopt --max-model-len 262144
--reasoning-parser qwen3`, budget 40), `qwen3.8-27b` (vllm, `unsloth/Qwen3.8-27B-NVFP4`,
role: vision, budget 45), `nemotron-3.5-lightning-30b` (vllm,
`nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`, optional speculative config w/
`...-NVFP4-DSpark` draft, budget 40), `deepseek-v4-flash` (llamacpp, local GGUF
`~/ai-data/models/gguf/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf`
+ optional drafter `DSpark-drafter-Q2K-Q8-0731.gguf`, budget 100 → effectively exclusive).
Experimental: `deepseek-v4-flash-ds4` (ds4 backend), `qwen3.8-flash-next` (llamacpp fork,
n-gram SSD offload — native build, own systemd unit, never in the main compose stack).

Concurrency rule (modelctl): active set fits if `sum(budget_gib) + host_reserve_gib <
MemAvailable-at-baseline` AND (single model, OR all pairs have distinct roles from
{text, vision, utility}). Same-role pairs additionally require membership in
`tested_pairs:` (soak-gated allowlist) in models.yaml.

## 8. Tailscale serve map (`sparkctl tailscale-configure`)

```
/        → http://127.0.0.1:3000          # OpenWebUI at root (assets/Socket.IO need root path)
/v1      → http://127.0.0.1:4000/v1       # LiteLLM OpenAI API
/search  → http://127.0.0.1:8888/search   # SearXNG (t640's litellm consumes this)
/grafana → http://127.0.0.1:3001/grafana  # only when monitoring up (Grafana needs GF_SERVER_ROOT_URL + serve_from_sub_path)
```
No Funnel. SSE/Socket.IO must be acceptance-tested through the serve URL.

## 9. User-side CLIs (unprivileged, Python 3; PyYAML from apt `python3-yaml`)

`bin/modelctl`: `catalog | fetch <m> | verify <m|all> | activate <m> [--with <m2>] [--experimental]
[--skip-sanity] | deactivate <m|all> | status [--json] | logs <m> [-f] | sanity <m> | eval <m>
[--tasks ...] | ensure-boot`.
Orchestration: flock on `~/ai-data/state/modelctl.lock`; budget precheck via /proc/meminfo;
`sudo spark-ai-ctl backend-start/stop/gateway-reload` for privileged steps; `wait_healthy` HTTP
polling (timeouts: vllm 1200s, llamacpp 300s, sglang 900s, ds4 1800s); post-activation
`evals/sanity/run.py` gate — on failure: backend-stop + restore previous active set (rollback);
fetch via `huggingface_hub` CLI into `~/ai-data/huggingface` (user-owned, no sudo).

`bin/sparkctl`: `bootstrap` (delegates to `sudo ./bootstrap.sh`) | `sync` (`sudo spark-ai-ctl
sync`) | `secrets <init|status|rotate KEY>` | `core <up|down|restart|status>` |
`monitoring <up|down>` | `utilities <up|down>` | `tailscale-configure` | `doctor [...]` |
`acceptance [...]` | `migrate` (legacy artifact adoption; pre-cutover only).

`bin/doctor`: read-only; exits nonzero on failure; checks: platform (aarch64, GB10, CUDA 13,
driver), MemAvailable baseline, disk space, secrets presence+modes (via `sudo spark-ai-ctl
secrets-status` if sudo available, else skip), loopback-boundary scan (no stack port on
0.0.0.0), docker-group membership warning, `/etc/spark-ai-stack` sync freshness (hash compare,
read-only), core service health, active model health + `MemAvailable` delta vs budget,
tailscale serve config presence, image digest match vs lockfile.

## 10. Sanity eval contract (`evals/sanity/`)

`evals/sanity/run.py --api-base http://127.0.0.1:<port>/v1 --model <served_name> [--role text|vision]
[--long-context] [--json]` → exit 0 pass / 1 fail / 2 error. Probes (each with pass criteria):
1. `basic`: deterministic Q ("What is 2+2? Answer with just the number.") → contains "4",
   finish_reason=stop, non-empty.
2. `template`: response must NOT contain raw template tokens (`<|im_start|>`, `<|endoftext|>`,
   `[INST]`, `<think>` leakage when reasoning disabled) or the literal prompt echoed.
3. `repetition`: 200-token generation; fail if any 12-gram repeats ≥4 times.
4. `stop`: `stop=["STOP"]` honored; max_tokens honored (finish_reason=length when clipped).
5. `tool_call`: forced tool choice returns valid JSON args for a trivial weather tool.
6. `long-context` (optional, slow): 32k-token needle retrieval.
7. `vision` (role=vision): tiny base64 image → correct dominant-color answer.
Runner: stdlib + `urllib`/`json` only (no external deps). Used by modelctl post-activation
(fast set: 1–5) and `modelctl sanity` (all).

## 11. lm-eval contract (`evals/lm-eval/`)

Containerized `lm-eval` (pinned pip version in Dockerfile, aarch64) run on demand:
`modelctl eval <m> --tasks gsm8k,arc_easy,...` → `local-completions` against
`http://host.docker.internal:<slot-port>/v1/completions` (add extra_hosts) or host network on
loopback. Results JSON archived to `~/ai-data/state/evals/<model>/<date>.json`; a tiny
comparison helper prints deltas vs previous run of same model.

## 12. Repo layout & ownership map (who writes what during the refactor)

Per approved plan §"Target repo structure". Workstream ownership (subagents must stay in-lane):
- **A (restructure)**: deletions/moves, config/models.yaml + experimental.yaml, .gitignore
- **B (privilege)**: bootstrap.sh, security/ (spark-ai-ctl, sudoers, tmpfiles.d), systemd/, docs/SECURITY.md
- **C (compose)**: compose/compose.yml, compose/images/*, config/litellm.template.yaml, config/versions.lock.yaml, config/searxng-settings.yml
- **D (CLIs)**: bin/modelctl, bin/sparkctl, bin/doctor, tests/*, .github/workflows/ci.yml
- **E (evals)**: evals/*
- **F (monitoring)**: monitoring/*, compose/monitoring.yml
- **G (docs)**: README.md, docs/{OPERATIONS,RECOVERY,UPGRADES,COMPATIBILITY,HERMES}.md
