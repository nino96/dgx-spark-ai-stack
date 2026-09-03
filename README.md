# spark-ai-stack

Reproducible local-AI serving stack for a DGX-Spark-class box (unified-memory
GB10, aarch64). One authenticated OpenAI-compatible endpoint over Tailscale;
every backend, SearXNG, and the admin UI stay on loopback. Built for the ASUS
Ascent GX10 (`gx10-b210`) — GB10 sm_121, 121GiB unified memory, Ubuntu 24.04,
CUDA 13 — but the platform checks in `bin/doctor` also recognize NVIDIA DGX
Spark Founders Edition.

This repo does not manage BIOS, firmware, the kernel, the NVIDIA driver, CUDA,
or the vendor OS image. Recover those through the vendor's own path (see
`docs/RECOVERY.md`), then use this repo for everything above the OS.

The binding interface spec for all of the above is `docs/CONTRACTS.md` — if
this file and CONTRACTS.md ever disagree, CONTRACTS.md wins.

## Architecture

```text
                              tailnet client
                                    |
                                    | HTTPS (tailscale serve, no Funnel)
                                    v
                    +----------------------------------+
                    |     tailscale serve  (§8)         |
                    |  /        -> 127.0.0.1:3000       |
                    |  /v1      -> 127.0.0.1:4000/v1    |
                    |  /search  -> 127.0.0.1:8888/search|
                    |  /grafana -> 127.0.0.1:3001/grafana (monitoring only)
                    +----------------------------------+
                                    |
        loopback only (127.0.0.1) below this line
                                    |
        +---------------+   +--------------+   +-------------+
        |  openwebui    |   |   litellm    |   |   searxng   |
        |  :3000        |-->|   :4000      |   |   :8888     |
        +---------------+   +------+-------+   +-------------+
                                    |
                                    | routes to whichever backend
                                    | is active in each slot
                                    v
                +-------------------+-------------------+
                |                                        |
        primary slot :8001                     secondary slot :8002
        (vllm | llamacpp | sglang)             (vllm-secondary | llamacpp-secondary)
        one text/utility model at a time        one vision model (or 2nd text
                                                 model) at a time
                +-------------------+-------------------+
                                    |
                          postgres (litellm's DB, no published port)

        optional: monitoring profile (prometheus :9090, node_exporter :9100,
                  grafana :3001) and utilities profile (embeddings :8011,
                  whisper :8010 stub)
```

Model backends are hot-swapped, not all run at once: `spark-ai-ctl
backend-start` renders a compose override (`command:`, `ports:`,
allowlisted `environment:`) from the catalog and brings up exactly one
container per (backend, slot). `docs/CONTRACTS.md` §3/§4 has the full port
and compose-topology tables.

## Security model, in one paragraph

AI coding agents and interactive shells run as `niyam-gb10`, an unprivileged
user with full read/write on this repo (therefore **untrusted input** to
anything privileged) and, after cutover, no `docker` group membership. The
only door to root is `sudo spark-ai-ctl <verb>` — an argparse-gated wrapper
that reads exclusively from root-owned files under `/etc/spark-ai-stack`,
never from this repo, except for the one verb (`sync`) whose entire job is to
diff-then-copy repo files into that root-owned tree after a human looks at
the diff. Secrets live at `/etc/spark-ai-stack/secrets.env` (root:root 0600)
and per-service derived files under `/etc/spark-ai-stack/env.d/*.env` (also
0600) — the `niyam-gb10` user, and therefore any agent running as it, cannot
read them. Full threat model and verification commands: `docs/SECURITY.md`.

## Quickstart (fresh box)

```bash
git clone <this-repo-url> ~/code/spark-ai-stack
cd ~/code/spark-ai-stack

sudo ./bootstrap.sh                    # idempotent; installs spark-ai-ctl,
                                        # sudoers, systemd units, secrets-init,
                                        # and an initial `sync --yes`

bin/sparkctl secrets status            # confirm the fixed key set exists (HF_TOKEN
                                        # is left empty by secrets-init)
sudoedit /etc/spark-ai-stack/secrets.env   # hand-edit in your Hugging Face token
bin/sparkctl secrets init              # re-derive env.d/*.env after editing HF_TOKEN

sudo spark-ai-ctl sync                 # review the diff before every future change
                                        # (bootstrap.sh already ran this once with --yes)

bin/sparkctl core up                   # litellm, openwebui, postgres, searxng
bin/sparkctl core status

bin/modelctl fetch qwen3.6-35b         # downloads the boot model's HF snapshot
bin/modelctl activate qwen3.6-35b      # starts it, waits healthy, runs sanity, reloads gateway

bin/sparkctl tailscale-configure --yes # publish /, /v1, /search (and /grafana if up)
bin/doctor                             # read-only check of the whole picture
```

`sudo ./bootstrap.sh --harden` (run once you've confirmed the NOPASSWD path
works) removes `niyam-gb10` from the `docker` group — the last step of
cutover; see `docs/OPERATIONS.md` and `docs/MIGRATION-T640.md`.

## Daily driving

```bash
bin/modelctl catalog                          # every model, fit-now vs current MemAvailable
bin/modelctl status                           # active set + live health + memory
bin/modelctl activate qwen3.6-27b             # swap the primary-slot model
bin/modelctl activate qwen3.6-35b --with qwen3.8-27b   # text (primary) + vision (secondary)
bin/modelctl sanity qwen3.6-35b               # full probe set, on demand
bin/modelctl logs qwen3.6-35b                 # prints the `sudo docker logs` command to run
```

**Model swap semantics**: `activate <model>` is transactional — it stops
whatever conflicts on the same slot or backend, starts the new one, waits for
health, runs the fast sanity probes, reloads the gateway, and only then
commits. Any failure at any step rolls back to the previous active set
automatically (`bin/modelctl` handles this; nothing to do manually).

**Concurrency rule**: at most two models active at once, one per slot
(`primary` 8001, `secondary` 8002). Two models may run together only if they
have distinct roles (`text` / `vision` / `utility`) — that's the normal
"one big text model + a vision model" pair. Two models sharing a role can
only run together if that exact pair is in `config/models.yaml`
`tested_pairs:` (a soak-gated allowlist — see `docs/OPERATIONS.md`). Budget
math is `sum(budget_gib) + host_reserve_gib < MemAvailable-at-baseline`, read
from `/proc/meminfo` — GB10 reports no usable GPU memory via
`nvidia-smi`/NVML, so there is no other source of truth for this.

## Where secrets live, and why agents can't read them

`/etc/spark-ai-stack/secrets.env` (root:root, 0600) and its per-service
derivatives under `/etc/spark-ai-stack/env.d/` (also 0600). Nothing under
`/etc/spark-ai-stack` is writable, or in most cases even readable, by
`niyam-gb10`. This is deliberate: an AI coding agent operating as
`niyam-gb10` can edit anything in this repo, so the repo itself must never be
where a real credential lives. See `docs/SECURITY.md` for the full model and
the exact exposure table (which secret reaches which container).

## Doc index

| Doc | Covers |
|---|---|
| `docs/CONTRACTS.md` | Binding interface spec — paths, ports, verbs, schemas. Source of truth. |
| `docs/SECURITY.md` | Threat model, invariants, verification commands. |
| `docs/OPERATIONS.md` | Day-to-day runbook: start/stop, swap models, health, logs, monitoring, evals. |
| `docs/RECOVERY.md` | Wipe-and-restore runbook after an OS reinstall. |
| `docs/UPGRADES.md` | Pin/bump procedure per component (images, llama.cpp, vLLM, sglang, models, lm-eval). |
| `docs/COMPATIBILITY.md` | Known-good pins, workarounds, and open model-specific quirks. |
| `docs/HERMES.md` | Future-phase integration stub for the NousResearch Hermes agent. |
| `docs/MIGRATION-T640.md` | The legacy-stack cutover sequence and t640 retirement plan. |
| `evals/sanity/README.md` | Post-activation sanity probes. |
| `evals/lm-eval/README.md` | On-demand benchmark regression tracking. |
| `monitoring/README.md` | Prometheus/Grafana overlay. |
