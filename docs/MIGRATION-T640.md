# Migration: legacy cutover + t640 retirement

Context: this box (`gx10-b210`) currently runs a legacy, non-compose stack —
a `vllm-server` container on `0.0.0.0:8000` and a `searxng` container on
`0.0.0.0:8888` (both LAN/world-bound, not loopback-only), plus a
`ds4-server.service` systemd unit for DeepSeek V4 Flash. An HP t640 elsewhere
on the tailnet runs OpenWebUI + LiteLLM + Postgres, whose LiteLLM instance
consumes this box's legacy `vllm-server` and `searxng` (the latter at
`format=json`). This document is the cutover from that legacy stack to this
repo's compose-based stack, and the follow-on retirement of the t640's own
OpenWebUI/LiteLLM/Postgres once the new stack has soaked.

**Legacy containers/units are stopped, never deleted, until the very last
step of each stage** — this is what makes every stage below reversible.

## Pre-cutover checklist

- [ ] `git clone` this repo (or already present) at
      `/home/niyam-gb10/code/spark-ai-stack` — `bootstrap.sh` is hardcoded to
      this path (`docs/CONTRACTS.md` §1) and refuses to run from anywhere else.
- [ ] `nvidia-smi`, `nvcc --version`, `uname -m` confirm GB10/CUDA 13/aarch64.
- [ ] `tailscale status` shows this node logged in with a MagicDNS name.
- [ ] Note the legacy `vllm-hf-cache` docker volume exists:
      `docker volume inspect vllm-hf-cache`.
- [ ] Note any local GGUFs under `~/gguf` that `config/models.yaml` /
      `config/experimental.yaml` expect (`deepseek-v4-flash` + its drafter) —
      `sparkctl migrate` hard-links these, it does not fetch them fresh.
- [ ] Record the t640's current LiteLLM SearXNG URL (`http://<this-box>:8888/search?format=json`
      or similar) — you'll change this at step 6.
- [ ] Confirm you (a human) can `sudo` on this box with a password — `sync`
      and `secrets-*` are deliberately password-gated (`docs/SECURITY.md`).

## Cutover sequence

Run these **in order**. Each step names its own rollback.

### 1. Adopt legacy artifacts — `sparkctl migrate` (BEFORE bootstrap/hardening)

```bash
bin/sparkctl migrate
```

Hard-links (or copies, if hard-linking across filesystems fails) known GGUFs
from `~/gguf` into `~/ai-data/models/gguf`, verifying each against the
sha256 already declared in `config/models.yaml`/`config/experimental.yaml`;
copies the `vllm-hf-cache` docker volume's contents into
`~/ai-data/huggingface` via a one-time `sudo docker run` (explicitly *not*
through `spark-ai-ctl` — this is documented, pre-hardening-only exception in
`bin/sparkctl`'s own `migrate` implementation, since the operator user still
has `docker` group access at this point). **Deletes and stops nothing.**
Run this *before* `bootstrap.sh --harden` specifically because it needs
direct `docker volume`/`docker run` access that hardening removes.

Rollback: no-op to roll back — nothing was removed, and re-running `migrate`
again is idempotent (already-adopted files with a matching checksum are
skipped).

### 2. `sudo ./bootstrap.sh` (no `--harden` yet)

```bash
sudo ./bootstrap.sh
```

Installs `spark-ai-ctl`, the sudoers drop-in, systemd units (enabled, not
started), runs `secrets-init` and an initial `sync --yes`. Does not touch
the legacy stack. `niyam-gb10` still has `docker` group membership.

Rollback: nothing legacy was touched; this step only adds files under
`/etc/spark-ai-stack`, `/usr/local/sbin`, `/etc/systemd/system`. Safe to
re-run or ignore.

### 3. Restore `HF_TOKEN`, then `sudo spark-ai-ctl sync`

```bash
sudoedit /etc/spark-ai-stack/secrets.env    # set HF_TOKEN
sudo spark-ai-ctl secrets-init
sudo spark-ai-ctl sync                      # review the diff, confirm
```

Rollback: same as step 2 — nothing legacy touched.

### 4. Stop legacy `searxng`, then `core up` (seconds of downtime on :8888)

```bash
sudo docker stop searxng          # legacy container name; frees host port 8888
bin/sparkctl core up              # brings up spark-ai-searxng (and litellm/openwebui/postgres)
                                    # bound to 127.0.0.1:8888, replacing the legacy listener
bin/sparkctl core status
curl -fsS 'http://127.0.0.1:8888/search?q=test&format=json' | grep -q results && echo ok
```

This is the only step with real downtime — the gap between `docker stop
searxng` and the new `spark-ai-searxng` container reporting healthy on the
same port. Expect seconds, not minutes. The legacy `vllm-server` on `:8000`
is left running throughout this step (different port, no conflict) so the
t640's model traffic is unaffected until step 8.

Rollback: `bin/sparkctl core down` (or just stop `spark-ai-searxng`), then
`sudo docker start searxng` to bring the legacy container back on `:8888`.

### 5. `tailscale-configure`

```bash
bin/sparkctl tailscale-configure --yes
tailscale serve status
```

Publishes `/`, `/v1`, `/search` (and `/grafana` once monitoring is up) per
`docs/CONTRACTS.md` §8. This does not affect the t640's *direct* access to
this box's ports — the t640 is not required to go through `tailscale serve`
to reach `:8888`/`:8000`/`:4000` if it's already on the same tailnet, but
publishing `serve` here is what will let it (and everything else) address
this box consistently going forward, and is a prerequisite for step 6's URL.

Rollback: `tailscale serve reset` restores no Serve config; legacy direct
port access is unaffected either way.

### 6. Update the t640's LiteLLM SearXNG URL

On the t640, change the SearXNG URL its LiteLLM config points at from the
legacy direct address to the new tailnet path:

```
old: http://<this-box-tailnet-ip-or-name>:8888/search?format=json
new: https://gx10-b210.<tailnet>.ts.net/search/?format=json
```

Restart the t640's LiteLLM so the new URL takes effect. Verify a search
still returns results through the new path before continuing.

Rollback: revert the URL on the t640 and restart its LiteLLM; the legacy
`searxng` container is still stopped (not removed) from step 4, so restoring
it (`sudo docker start searxng` here) plus the URL revert on the t640 fully
undoes this and step 4 together.

### 7. Activate the boot model

```bash
bin/modelctl fetch qwen3.6-35b       # if not already fetched (sparkctl migrate only adopts GGUFs)
bin/modelctl activate qwen3.6-35b
bin/modelctl status
```

This brings up `spark-ai-vllm` on `127.0.0.1:8001`, loopback only — this is
a **different port** than the legacy `vllm-server`'s `:8000`, so both can
run simultaneously through this step. The t640's LiteLLM is not yet pointed
at the new stack for chat completions at this stage — only SearXNG moved
(step 6). Moving the t640's model traffic over to this box's new gateway
(`:4000`) is a separate, later decision outside this document's scope (the
t640's own OpenWebUI/LiteLLM keep working against the legacy `vllm-server`
until the soak period in the next section concludes and the t640 itself is
retired).

Rollback: `bin/modelctl deactivate qwen3.6-35b`; legacy `vllm-server` on
`:8000` was never touched.

### 8. Acceptance

```bash
export LITELLM_MASTER_KEY='<value>'    # docs/OPERATIONS.md has the sudo cat recipe
bin/sparkctl acceptance
```

Confirms the new stack's gateway, chat completions (sync + streaming +
cancellation), tool calls, and SearXNG-via-format=json all work end to end
before anything legacy is removed.

### 9. Only now: remove the legacy `vllm-server`

```bash
bin/cleanup-legacy                        # dry-run by default; prints the retirement scope
bin/cleanup-legacy --execute --i-confirm-parity
```

Stops (does not delete) `ds4-server.service`, and the `vllm-server` and
`searxng` containers (the latter is already stopped from step 4; this is
idempotent). Explicitly retains `~/Documents`, `~/code/ds4`,
`~/code/ds4-on-spark`, `~/gguf`, the `vllm-hf-cache` volume, and every
Docker image and stopped container — nothing is deleted by this tool, ever.

Rollback: `sudo docker start vllm-server` (image and container still exist,
just stopped); `systemctl --user enable --now ds4-server.service` if DS4 was
in use.

### 10. `bootstrap.sh --harden` last

```bash
sudo ./bootstrap.sh --harden
# log out and back in, or reboot
```

Removes `niyam-gb10` from the `docker` group — the last step, run only once
every prior step has been verified working, since this closes off the
direct-`docker` escape hatch used for rollback commands above (`sudo docker
start ...` still works after hardening, since it goes through `sudo`; what
stops working is *unprivileged* `docker ...` without `sudo`).

## Soak-period plan

Before retiring the t640's own OpenWebUI/LiteLLM/Postgres:

1. Run the new stack as the sole path for at least one full soak period
   (align with `tests/soak/README.md`'s bar: no OOM/CUDA/NVRM errors, no
   unexpected service restarts, `MemAvailable` never crosses
   `host_reserve_gib`, swap doesn't grow).
2. Confirm `bin/doctor` and `bin/sparkctl acceptance` stay clean across that
   period, not just at cutover time.
3. Confirm the t640's SearXNG traffic (step 6) has been stable with zero
   fallback to the old URL.
4. Only after the soak period is judged clean: proceed to t640 retirement.

## t640 retirement

**Retained on the t640**: Tailscale (it stays a tailnet member for whatever
else runs there), Pi-hole.

**Removed from the t640**: OpenWebUI, LiteLLM, Postgres — these are the
services this repo's `openwebui`/`litellm`/`postgres` (running on
`gx10-b210` now) replace. Stop and remove them on the t640's own terms
(outside this repo's scope — the t640 is not managed by this stack). Do not
remove them until the soak period above has concluded; until then, the t640
services are the fallback if the new stack needs to be rolled back to.

## Rollback summary

| Stage | What's reversible, and how |
|---|---|
| 1 (migrate) | Nothing removed; re-run is idempotent. |
| 2–3 (bootstrap, secrets, sync) | Only adds root-owned files; ignore or re-run. |
| 4 (stop legacy searxng, core up) | `bin/sparkctl core down`; `sudo docker start searxng`. |
| 5 (tailscale-configure) | `tailscale serve reset`. |
| 6 (t640 SearXNG URL) | Revert the URL + restart t640 LiteLLM; pairs with restarting legacy searxng from step 4. |
| 7 (activate boot model) | `bin/modelctl deactivate qwen3.6-35b`; legacy vllm-server untouched. |
| 8 (acceptance) | Read-only; nothing to roll back. |
| 9 (remove legacy vllm-server) | `sudo docker start vllm-server`; nothing was deleted. |
| 10 (`--harden`) | Re-add `niyam-gb10` to the `docker` group (`sudo gpasswd -a niyam-gb10 docker`) if this needs reverting; do this only as a deliberate, temporary rollback, not routine operation. |
| Soak / t640 retirement | Don't retire the t640's OpenWebUI/LiteLLM/Postgres until the soak period passes — they're the whole-stack fallback until then. |
