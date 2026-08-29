# Operations

Day-to-day runbook. Binding spec: `docs/CONTRACTS.md`. Threat model behind the
`sudo`/`bin/sparkctl` split: `docs/SECURITY.md`.

## Start / stop core

```bash
bin/sparkctl core up          # sudo spark-ai-ctl core-up   -- litellm, openwebui, postgres, searxng
bin/sparkctl core status      # sudo spark-ai-ctl core-ps   -- docker compose ps for the whole project
bin/sparkctl core restart
bin/sparkctl core down        # stops containers, does NOT remove volumes
```

`core up`/`down`/`restart`/`status` are NOPASSWD sudoers entries (no password
prompt once `bootstrap.sh` has run). At boot, `spark-ai-core.service` runs
`core-up` and `spark-ai-boot-model.service` runs `bin/modelctl ensure-boot`
after it (see `systemd/*.service`).

## Activate / swap models

```bash
bin/modelctl catalog                                   # every model + fits_now vs current MemAvailable
bin/modelctl fetch <model>                             # download HF snapshot or GGUF+drafter
bin/modelctl verify <model|all>                        # sha256 / snapshot-dir checks
bin/modelctl activate <model>                          # swap the model occupying its slot
bin/modelctl activate <model> --with <model2>          # e.g. a text primary + a vision secondary
bin/modelctl activate <model> --experimental           # required for anything in config/experimental.yaml
bin/modelctl activate <model> --skip-sanity            # skip the post-activation sanity gate (debugging only)
bin/modelctl deactivate <model|all>
```

`activate` is transactional: it validates slot/role/tested-pair rules and the
memory budget, verifies GGUF artifacts, stops whatever conflicts on the same
slot or the same backend/container, starts the requested model(s), waits for
health, runs the fast sanity probe set (`basic, template, repetition, stop,
tool_call`) unless `--skip-sanity`, reloads the LiteLLM gateway, and only then
commits the new active set. Any failure at any step rolls back to the
previous active set and restarts it automatically — nothing to clean up by
hand.

**Text + vision pair rule**: at most two models active at once, one per slot
(`primary` → `:8001`, `secondary` → `:8002`). A pair is only admitted if:

1. the two models declare different `slot` values, and
2. either they have different `role` (`text`/`vision`/`utility` — this is the
   normal one-big-model + vision-pair shape), **or** the exact pair (by
   model-id set) appears in `config/models.yaml` `tested_pairs:` with
   `status: passed`.

Two models on the *same* backend (e.g. two `vllm` entries) can never run
concurrently regardless of slot — `backend` maps to one compose service per
slot, and starting one stops any other active model on that same service.

**"One-big-model + text-vision pair"** in practice:

```bash
bin/modelctl activate qwen3.6-35b --with qwen3.8-27b   # text primary + vision secondary
```

`qwen3.6-35b`/`qwen3.6-27b`/`nemotron-3.5-lightning-30b` are all `role: text,
slot: primary` — only one of them can ever be active at a time (same slot).
`qwen3.8-27b` is `role: vision, slot: secondary` and can pair with any of
them without a `tested_pairs:` entry, since the roles differ.

## Soak-testing a same-role pair into `tested_pairs`

Same-role pairs (e.g. two text models, one per slot) need explicit soak
approval before `activate --with` will admit them:

```bash
# 1. baseline
free -b; sudo spark-ai-ctl core-ps

# 2. temporarily add the pair to config/models.yaml's tested_pairs: in a worktree, e.g.
#    tested_pairs:
#      - {models: [qwen3.6-35b, deepseek-v4-flash], status: passed}
sudo spark-ai-ctl sync              # land the temporary catalog change in /etc

# 3. admit both, then soak
bin/modelctl activate qwen3.6-35b
bin/modelctl activate deepseek-v4-flash --with qwen3.6-35b
tests/soak/run.sh                   # see tests/soak/README.md; run >= 30 min, mixed prompt lengths

# 4. watch MemAvailable, swap, journalctl -k, both backend logs, cancellation
#    behavior. Fail on any restart, OOM/CUDA/NVRM error, swap growth, or a
#    MemAvailable drop below host_reserve_gib (10 GiB by default).
```

Revert the temporary `tested_pairs:` edit and re-sync immediately if the gate
fails — `modelctl` otherwise treats the pair as exclusive. Only commit the
`tested_pairs:` entry (with the soak report) once it genuinely passes; see
`tests/soak/README.md`.

## Check health

```bash
bin/doctor                    # read-only; platform, memory baseline, disk, secrets presence,
                               # loopback boundary, docker-group membership, sync freshness,
                               # core service health, active-model health, tailscale serve,
                               # image digest pins. Exit 0 unless a check FAILs (WARN never fails).
bin/doctor --json
bin/doctor --capture-baseline # write ~/ai-data/state/baseline.json from CURRENT MemAvailable --
                               # run this at idle (core up, no models active) right after core-up,
                               # before activating anything, so budget math has a real baseline

bin/modelctl status           # active set + live health + mem_available_gib (JSON: --json)
bin/modelctl sanity <model>   # full sanity probe set (incl. long_context, vision) against an
                               # already-active model

bin/sparkctl acceptance       # python3 tests/acceptance.py -- unified endpoint + SearXNG checks
                               # (needs LITELLM_MASTER_KEY in the environment; see below)
```

`tests/acceptance.py` flags: `--base-url` (default
`http://127.0.0.1:4000/v1`), `--model` (default: the catalog's `boot: true`
model), `--search-url` (default `http://127.0.0.1:8888/search`),
`--active-model` (repeatable, expected visible aliases for a concurrent
profile), `--key-env` (default `LITELLM_MASTER_KEY` — the env var name to
read the bearer key from), `--responses` (also exercise `/v1/responses`).
Run it with the real master key in the environment. `secrets-status` never
prints values (docs/CONTRACTS.md §5) — read the value with your own sudo
password, then export it for the shell running acceptance:

```bash
sudo cat /etc/spark-ai-stack/secrets.env | grep ^LITELLM_MASTER_KEY=   # password prompt
export LITELLM_MASTER_KEY='<the value printed above>'
bin/sparkctl acceptance
```

If you'd rather not read the value at all, `sudo spark-ai-ctl secrets-rotate
LITELLM_MASTER_KEY` generates a fresh one (invalidates every existing
client's key; re-derives `env.d/*.env` automatically) — same `sudo cat` step
afterward to retrieve it.

Note: `bin/doctor` takes only `--json` and `--capture-baseline` — there is no
`--full` flag. `bin/modelctl sanity <model>` is the equivalent "run everything"
check for a single active model.

## Logs

The `niyam-gb10` user has no `docker` group membership after
`bootstrap.sh --harden` (see `docs/SECURITY.md`), so `bin/modelctl logs`
cannot exec `docker` itself — it prints the command to run:

```bash
bin/modelctl logs qwen3.6-35b            # -> prints: sudo docker logs --tail=200 spark-ai-vllm
bin/modelctl logs qwen3.6-35b -f         # -> prints: sudo docker logs -f spark-ai-vllm

# run the printed command yourself:
sudo docker logs --tail=200 spark-ai-vllm
sudo docker logs -f spark-ai-litellm
sudo docker logs -f spark-ai-searxng
```

Container names by backend: `spark-ai-vllm` / `spark-ai-vllm-secondary`,
`spark-ai-llamacpp` / `spark-ai-llamacpp-secondary`, `spark-ai-sglang`;
core: `spark-ai-litellm`, `spark-ai-openwebui`, `spark-ai-postgres`,
`spark-ai-searxng`. Experimental backends (`ds4`, `llamacpp-fork`) run as
their own systemd units, not containers — use `journalctl -u <unit>` for
those (`modelctl logs` prints this hint too).

Per-backend metrics, when the backend exposes them: `http://127.0.0.1:<slot
port>/metrics` (loopback only; `modelctl logs` prints this line when it
detects a live `/metrics` endpoint).

## Memory budgeting on GB10

`nvidia-smi`/NVML report no usable GPU memory figures on GB10 (unified
memory) — all budgeting comes from `/proc/meminfo` `MemAvailable`, both in
`bin/modelctl` and in `bin/doctor`.

- Concurrency rule: `sum(budget_gib of active models) + host_reserve_gib <
  MemAvailable-at-baseline` (`host_reserve_gib` defaults to 10 GiB,
  `config/models.yaml` `defaults:`).
- **Capture a baseline right after core-up, before activating anything**:
  `bin/doctor --capture-baseline` writes
  `~/ai-data/state/baseline.json` from the *current* `MemAvailable`. Do this
  at idle — `doctor` warns (but still writes) if a model is already active
  when you capture it, since that would understate what's really available.
- `bin/modelctl catalog` shows `fits_now` per model against *current*
  `MemAvailable` (not the baseline) as a quick sanity check, but the real
  admission check at `activate` time uses the baseline file if one exists,
  falling back to current `MemAvailable` plus the budgets of whatever's
  already active.
- `bin/doctor`'s `mem_baseline` check reports the delta between the captured
  baseline and current `MemAvailable` — a growing negative delta with no
  models active suggests a leak worth investigating outside this stack.

## Monitoring profile

```bash
bin/sparkctl monitoring up      # sudo spark-ai-ctl monitoring-up -- prometheus :9090,
                                 # node_exporter :9100, grafana :3001
bin/sparkctl monitoring down
```

Not started by default and not a core dependency. Grafana comes up
pre-provisioned with a Prometheus datasource and one dashboard (`GB10 Stack`,
uid `gb10-stack`) — no manual setup. Reachable on the tailnet at `/grafana`
once `bin/sparkctl tailscale-configure --yes` has run *and* monitoring is up
(the dry-run/apply script probes `127.0.0.1:3001` before adding that mount).
Login is Grafana's own built-in `admin`/`admin` unless
`GRAFANA_ADMIN_PASSWORD` has been set in `secrets.env` — change it on first
login either way. See `monitoring/README.md` for scrape-target and dashboard
maintenance, and the "no NVML on GB10" note that shapes the memory panel
(GPU utilization/power/clocks are `nvidia-smi`-only; there's no exporter for
them here — `watch -n1 nvidia-smi` on the host for that).

## Utilities profile

```bash
bin/sparkctl utilities up       # sudo spark-ai-ctl utilities-up
bin/sparkctl utilities down
```

- `embeddings` (`:8011`) reuses the llamacpp image in `--embedding` mode. It
  is a **placeholder**: the compose skeleton points at
  `/models/gguf/embeddings.gguf`, which does not ship with this repo — put an
  actual embedding GGUF at `~/ai-data/models/gguf/embeddings.gguf` (a copy or
  symlink) before bringing utilities up, or the container will fail to start.
- `whisper` (`:8010`) is a **TODO stub** — fully commented out in
  `compose/compose.yml` as of this writing because no confirmed aarch64 +
  CUDA 13 speech-to-text image is pinned yet. `utilities-up`/`down` silently
  skip it until a base image is chosen and the service is uncommented (see
  the comment block in `compose/compose.yml` for the exact TODO).

## SearXNG usage by the t640's LiteLLM

Port 8888 was deliberately kept from the legacy stack so the only change the
t640's LiteLLM needs is a URL, not a port:

```
http://127.0.0.1:8888/search?format=json          # local (from this box)
https://gx10-b210.<tailnet>.ts.net/search/?format=json   # from the t640, over the tailnet
```

`config/searxng-settings.yml` enables the `json` output format specifically
for this consumer (`html` is also enabled, for interactive browser use via
`/search` through OpenWebUI's web-search integration). See
`docs/MIGRATION-T640.md` for the exact cutover-time URL change on the t640
side.

## lm-eval runs and compare

```bash
bin/modelctl eval qwen3.6-35b --tasks arc_easy,gsm8k_cot --limit 50
# equivalent to: evals/lm-eval/run.sh qwen3.6-35b 8001 --tasks arc_easy,gsm8k_cot --limit 50

python3 evals/lm-eval/compare.py qwen3.6-35b            # newest vs. previous archived run
python3 evals/lm-eval/compare.py qwen3.6-35b --last 5   # compare across the last 5 runs
```

On-demand and human-triggered only — never run automatically post-activation
(that's what `evals/sanity/` is for). Runs the pinned `lm-evaluation-harness`
container via `sudo docker` by default (the post-hardening user has no
`docker` group membership, and eval runs are not in the NOPASSWD sudoers
list — this is an accepted tradeoff, not a bug). To avoid docker/sudo
entirely: `pip install 'lm_eval[api]==0.4.12'` in your own venv, then
`LM_EVAL_LOCAL=1 evals/lm-eval/run.sh <model> <port> ...`. Results archive to
`~/ai-data/state/evals/<model>/<UTC timestamp>.json`. Scores are only
meaningful compared against a *previous run of the same model on this same
stack* — never against upstream leaderboard numbers (different weights,
quantization, and harness config). See `evals/lm-eval/README.md` for task
suggestions and runtime expectations (`arc_easy`: minutes; `gsm8k_cot
--limit 50`: several minutes; unlimited generation tasks: 30+ minutes).
