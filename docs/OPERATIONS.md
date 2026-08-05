# Operations

## Bootstrap and converge

```bash
./bootstrap
bin/stack secrets-init --import-hf-token   # optional import, never printed
${EDITOR:-nano} ~/.config/spark-ai-stack/secrets.env
bin/stack apply
```

The playbook is designed to be rerun. Use `bin/stack check` before a change and
`bin/stack apply` twice during acceptance; the second run must report no
changes. Ansible refuses unsupported DMI/OS/GPU/CUDA baselines and provides no
production bypass. Use vendor recovery or OTA before trying again.

`stack apply` installs user units and enables linger. The core unit starts
LiteLLM and SearXNG; the boot-model unit ensures Qwen is active after Docker is
ready, without a graphical or SSH login.

## Secrets

```bash
bin/stack secrets-init
chmod 0600 ~/.config/spark-ai-stack/secrets.env
```

The command preserves existing values and fills missing LiteLLM/SearXNG values
with cryptographically random strings. It derives separate mode-`0600`
per-service environment files under `~/ai-data/state`, so containers receive
only the credentials they need. `--import-hf-token` reads the existing
`~/.huggingface/token` only when `HF_TOKEN` is empty. Never copy the prior
SearXNG secret from `~/Documents`.

Clients send the LiteLLM value as `Authorization: Bearer <key>`. Rotate it by
editing the secrets file and running `bin/stack core-restart`.

## Existing GX10 migration

`bin/stack migrate` is non-destructive and idempotent. It:

1. Creates the `~/ai-data` layout.
2. Adopts the known DS4 base and drafter from `~/gguf` using hard links if
   possible, otherwise a copy, and then verifies both hashes.
3. Copies `vllm-hf-cache` into `~/ai-data/huggingface` through the pinned NGC
   image with the old volume mounted read-only.
4. Records a migration report without copying credentials or tailnet IPs.

It does not stop old services. `stack apply` checks port 8888: if the old
`searxng` owns it, the playbook stops that specific container immediately
before bringing up `spark-ai-searxng`. Old sources, unit files, images, model
paths, and the Docker volume remain available for rollback.

## Model lifecycle

```bash
bin/modelctl catalog [--json]
bin/modelctl fetch MODEL [--json]
bin/modelctl verify MODEL|all [--json]
bin/modelctl activate MODEL [--default] [--json]
bin/modelctl deactivate MODEL [--json]
bin/modelctl status [--json]
bin/modelctl logs MODEL
```

Activation validates the lockfile identity, artifact, available-memory floor,
declared budgets, and pair allowlist. It then performs the transactional state
transition described in `ARCHITECTURE.md`. Health timeouts are longer for the
large models. A failed transition restores services, route config, and state.

Examples:

```bash
# Normal default
bin/modelctl activate qwen3.6-35b --default

# Small model; this stops Qwen while the pair gate is pending
bin/modelctl activate qwen3.5-4b-gguf

# Exclusive DS4 switch; local/default follows it
bin/modelctl activate deepseek-v4-flash --default

# Stops DS4 and restores Qwen
bin/modelctl deactivate deepseek-v4-flash
```

## Logs and health

```bash
bin/stack status
bin/doctor --full
bin/modelctl logs qwen3.6-35b
bin/modelctl logs qwen3.5-4b-gguf
bin/modelctl logs deepseek-v4-flash
docker logs -f spark-ai-litellm
docker logs -f spark-ai-searxng
```

Loopback checks:

```bash
curl -fsS -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://127.0.0.1:4000/v1/models
curl -fsS 'http://127.0.0.1:8888/search/?q=spark&format=json'
ss -lntp
free -h
```

The host must show listeners at `127.0.0.1:4000`, `:8001`/`:8002`/`:8003` as
active, and `:8888`; never `0.0.0.0` for these ports. On GB10, monitor
`MemAvailable`, swap growth, and process RSS because aggregate GPU memory may
show `N/A`.

## Tailscale Serve

Join the tailnet interactively first; this repository does not store an auth
key. Then run:

```bash
bin/stack tailscale-configure
tailscale serve status
```

The command resets only the local Serve configuration and establishes HTTPS
proxies for `/v1` to port 4000 and `/search/` to port 8888. It never invokes
`tailscale funnel`, and it aborts if Tailscale has no MagicDNS DNS name.

Test from another tailnet node:

```bash
curl -i https://HOSTNAME/v1/models                    # must be 401
curl -fsS -H 'Authorization: Bearer KEY' \
  https://HOSTNAME/v1/models
curl -fsS 'https://HOSTNAME/search/?q=test&format=json'
```

## Concurrency soak gate

The initial Qwen+llama combination is `pending`. Follow
`tests/soak/README.md`, save the results, and require:

- both health endpoints and the gateway remain responsive;
- `MemAvailable` never crosses the 8 GiB reserve;
- swap does not grow from its pre-test baseline;
- no OOM, NVIDIA, CUDA, or service restart errors occur;
- streaming cancellation releases work promptly.

Only then change the combination status to `passed` in
`config/concurrency.yaml` in a reviewed commit.

## Upgrades

No component follows a floating tag. Upgrade one class of input per branch:

1. Change the lockfile revision, commit, or image digest.
2. Rebuild or fetch without deleting the old artifact.
3. Run static tests, signature checks, backend contract tests, and soak tests.
4. Activate transactionally and reboot-test.
5. Commit the new evidence with the lockfile change.

For an NGC vLLM upgrade, test the image *without* the FastAPI layer. FastAPI
0.137 introduced a path-less router object that older vLLM metrics middleware
did not handle, causing every request to return 500. Upstream fixed this in
[vLLM PR #45629](https://github.com/vllm-project/vllm/pull/45629). Remove the
pin only when the candidate image contains that fix and passes `/health`,
`/metrics`, `/v1/models`, chat, streaming, and 20 concurrent requests.

## Rollback and cleanup

If activation fails, `modelctl` rolls back automatically. For operator-driven
rollback:

```bash
bin/stack core-down
systemctl --user disable --now spark-ai-core.service spark-ai-boot-model.service
# Re-enable the old units/containers using the pre-migration notes.
```

Do not clean old assets until the new stack passes parity and a reboot.
`bin/cleanup-legacy` is deliberately separate, dry-runs by default, and
requires both `--execute` and `--i-confirm-parity`. It retires known old
service names but does not delete GGUFs, source trees, images, or volumes; those
remain a manual retention decision.
