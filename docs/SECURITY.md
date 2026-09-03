# Security model

This document is the prose companion to `docs/CONTRACTS.md` §2 (the binding,
load-bearing spec). If the two ever disagree, CONTRACTS.md wins and this file
is stale and should be fixed.

## Threat model

AI coding agents (and interactive shells) on this machine run as the
unprivileged user `niyam-gb10`. That user:

- has full read/write access to this repository (`/home/niyam-gb10/code/spark-ai-stack`),
  which is therefore **untrusted input** from the perspective of any
  privileged code — an agent, a bad merge, or a compromised dependency could
  put arbitrary content into any file here;
- is **removed from the `docker` group** at cutover (`bootstrap.sh --harden`),
  so it cannot reach the Docker daemon directly (unrestricted root-equivalent
  access) or through an ad-hoc `sudo docker ...`;
- reaches root for stack operations only through one door: `sudo spark-ai-ctl
  <verb>`, gated by `/etc/sudoers.d/spark-ai-stack`.

The goal is that a coding agent (or a bug, or a supply-chain problem in a
dependency it pulled in) operating as `niyam-gb10` cannot use the stack's
tooling to gain arbitrary root, read secrets, or open the loopback-only
services to the network — even though the same agent can edit any file in the
repo, including `bootstrap.sh` and everything under `security/`.

## Invariants (docs/CONTRACTS.md §2)

1. **Privileged code never reads user-writable files.** `spark-ai-ctl` and the
   systemd units it's paired with only ever open files under
   `/etc/spark-ai-stack/`, `/var/lib/spark-ai-stack/`, and
   `/run/spark-ai-stack/` — all root:root. The one exception, `sync`, is
   covered below.
2. **The repo reaches `/etc` only via `sudo spark-ai-ctl sync`.** That verb
   reads the repo's compose files, model catalog, litellm template, and
   searxng settings, prints a unified diff against the current
   `/etc/spark-ai-stack` copy (or announces a new file), and only copies
   after an explicit `y` at an interactive prompt or an explicit `--yes`. This
   diff-then-confirm step is the **human trust gate**: nothing an agent edits
   in the repo takes effect on the privileged side until a human (or a
   deliberately-scripted `--yes`) looks at it, or accepts responsibility for
   not looking.
3. **`spark-ai-ctl` accepts only enumerated verbs and constrained arguments.**
   It's built with `argparse`: every verb is a fixed subcommand name, and
   arguments that have a static domain (`secrets-rotate <KEY>`, `status
   --json`) use `choices=`. Model-id arguments are validated against the
   root-owned catalog inside the handler (with specific error messages) since
   their domain depends on catalog content that may not exist yet the first
   time the parser is built; nothing ever reaches `subprocess` unresolved
   against that catalog. There is no verb that takes a free-form path, a
   `-f`/`--file` style flag, or forwards caller-supplied environment
   variables into a container. Every `subprocess` call in `spark-ai-ctl` uses
   an explicit argv list with `shell=False` — there is no shell
   interpolation anywhere in the wrapper.
4. **Rendered runtime files are narrow by construction.** `spark-ai-ctl` is
   the *only* author of `/run/spark-ai-stack/override.yaml` and
   `/run/spark-ai-stack/litellm-config.yaml`. The override renderer
   (`render_override` in `security/spark-ai-ctl`) only ever emits
   `services.<backend>.command`, `services.<backend>.ports` (always
   `127.0.0.1:<slot-port>:<container-port>`), and an allowlisted
   `services.<backend>.environment` (`HF_HUB_OFFLINE`, `VLLM_*`, `SGLANG_*`
   only). Catalog `args:` entries are rejected outright (exit 3) if they
   start with `-v`, `--volume`, or `--mount`, or if they're a non-scalar
   (structured) YAML value — the only way a model's catalog entry could
   otherwise try to smuggle an extra bind mount or a sibling compose key.
5. **Secrets reach containers only via root-owned, per-service env files.**
   `/etc/spark-ai-stack/secrets.env` (0600, root:root) holds the fixed set of
   five keys; `spark-ai-ctl` derives one env file per service under
   `/etc/spark-ai-stack/env.d/` (also 0600), each carrying only the keys that
   service needs — see the exposure table below. Postgres gets its password
   via a Docker `secrets:` file mount
   (`/etc/spark-ai-stack/env.d/postgres_password`, 0600), not an env var.
6. **Every service binds loopback only.** All published ports in
   `compose/compose.yml` and the wrapper-rendered override are
   `127.0.0.1:<port>:<container port>`. Tailnet exposure is a separate,
   explicit `tailscale serve` configuration (docs/CONTRACTS.md §8), never a
   compose-level bind change.
7. **`HF_TOKEN` never reaches litellm.** Only `vllm.env`, `llamacpp.env`, and
   `sglang.env` carry `HF_TOKEN`. This is defense in depth: litellm's routes
   file is root-rendered anyway, but keeping the token out of that
   container's environment means a bug or future change in litellm's config
   handling can't turn into a token leak.

## Secrets exposure table

| Secret key            | litellm | openwebui | searxng | vllm/llamacpp/sglang | postgres |
|------------------------|:---:|:---:|:---:|:---:|:---:|
| `LITELLM_MASTER_KEY`   | yes (+ as `OPENAI_API_KEY` on openwebui) | yes | – | – | – |
| `POSTGRES_PASSWORD`    | via `DATABASE_URL` | – | – | – | file secret |
| `WEBUI_SECRET_KEY`     | – | yes | – | – | – |
| `SEARXNG_SECRET`       | – | – | yes | – | – |
| `HF_TOKEN`             | – | – | – | yes | – |

Derived files (all 0600, root:root, rewritten by `secrets-init`,
`secrets-rotate`, and `sync` — always idempotent):

```
/etc/spark-ai-stack/env.d/litellm.env      LITELLM_MASTER_KEY, DATABASE_URL
/etc/spark-ai-stack/env.d/openwebui.env    WEBUI_SECRET_KEY, OPENAI_API_KEY, OPENAI_API_BASE_URL
/etc/spark-ai-stack/env.d/searxng.env      SEARXNG_SECRET
/etc/spark-ai-stack/env.d/vllm.env         HF_TOKEN, HF_HOME
/etc/spark-ai-stack/env.d/llamacpp.env     HF_TOKEN, HF_HOME
/etc/spark-ai-stack/env.d/sglang.env       HF_TOKEN, HF_HOME
/etc/spark-ai-stack/env.d/postgres_password  (raw password, single line, Docker secrets file source)
```

## The sudoers surface

`/etc/sudoers.d/spark-ai-stack` (0440, root:root, installed by `bootstrap.sh`
after a `visudo -cf` syntax check) grants `niyam-gb10` **NOPASSWD** access to
exactly these `spark-ai-ctl` invocations:

```
core-up  core-down  core-restart  core-ps
backend-start <anything>      # spark-ai-ctl's own argparse validates the model id
backend-stop <anything>       # spark-ai-ctl's own argparse validates the target
gateway-reload
monitoring-up  monitoring-down
utilities-up  utilities-down
status  status <anything>     # covers `status --json`
```

`sync` and every `secrets-*` verb are **deliberately absent**. Running them
falls through to the machine's normal `sudo` policy, which requires a
password (assuming the default Ubuntu policy of "members of `sudo` need a
password" hasn't itself been weakened — that's out of scope for this
drop-in). This is intentional: those verbs either rewrite root-owned config
from the untrusted repo (`sync`) or touch secret material
(`secrets-init`/`secrets-status`/`secrets-rotate`), and both categories
should require a human to actively authenticate, not just be logged in as
`niyam-gb10`.

Every NOPASSWD line names `spark-ai-ctl` by its absolute installed path
(`/usr/local/sbin/spark-ai-ctl`) — sudoers command matching is path-based, so
a same-named script elsewhere on `$PATH` cannot be substituted. Lines with a
trailing `*` (for `backend-start`, `backend-stop`, `status`) rely on
`spark-ai-ctl`'s own `argparse` to be the actual gate on argument content;
sudoers itself cannot express "argument must be a valid model id."

## `sync` as the human trust gate

`sync` is the only place a byte from this repository crosses into
`/etc/spark-ai-stack`. Every other verb reads only root-owned files that were
already copied in by a previous `sync`. Running `sync`:

1. reads each of `compose/compose.yml`, `compose/monitoring.yml`,
   `config/models.yaml`, `config/experimental.yaml`,
   `config/litellm.template.yaml`, and `config/searxng-settings.yml` from the
   hardcoded repo path;
2. prints a unified diff against the current `/etc/spark-ai-stack` copy (or
   `new file` if there isn't one yet);
3. refuses to apply anything without an explicit `y` at a tty prompt, or an
   explicit `--yes` (and refuses outright, non-interactively, if neither is
   available);
4. only then copies the files in (0644, root:root) and re-derives `env.d/`.

**This is a rubber-stamp risk, stated honestly.** A human (or an automated
`--yes` in a script a human wrote and trusts) reviewing a diff of a large
YAML catalog or a compose file is not a rigorous audit — it's easy to skim
past a subtly wrong `hf_repo`, an added `args:` entry, or a template change.
`spark-ai-ctl`'s own validation (the args/environment allowlist, the
supported-backend check, the `choices=`-constrained argparse) is the real
backstop against a *malicious* catalog; the `sync` diff is there so a human
has the *opportunity* to notice something wrong before it becomes the active
root-owned truth, not a guarantee that they will.

## Residual risks (stated, not hidden)

- **Root is trusted.** Nothing here defends against a compromised root shell
  or a malicious systemd unit installed by some other means. This design's
  job is to keep an unprivileged, repo-editing identity (the coding-agent
  user) from being able to *become* root or read secrets through the stack's
  own tooling — not to defend the host against an attacker who already has
  root.
- **`spark-ai-ctl` is new, security-critical, privileged code.** It is
  ~1100 lines of Python that runs as root on every stack operation. It has
  been written for line-by-line review (docstring per verb handler,
  exhaustive input validation with specific error messages, no dead code,
  hardcoded paths for everything in docs/CONTRACTS.md §1) but it has not run
  in production yet. Treat it with the same suspicion as any other
  root-running daemon during initial rollout.
- **`sync` rubber-stamping**, as above — the diff review is only as good as
  the person (or script) approving it.
- **The `-v`/`--volume`/`--mount` args check is a targeted denylist, not a
  sandbox.** It stops the specific, obvious way a catalog `args:` entry could
  ask for an extra bind mount; it does not attempt to fully sandbox what a
  model-serving backend's own command-line arguments can do inside its
  already-running container. The container's own image, `no-new-privileges`,
  and lack of extra capabilities are the actual containment boundary.
- **Two catalog models that share a backend (vllm/llamacpp/sglang) can never
  run concurrently**, regardless of which slot they're assigned, because each
  backend maps to exactly one compose service/container. `backend-start`
  enforces this by stopping any other active model with the same slot *or*
  the same backend before starting a new one. If a future catalog change
  wants two vllm-backed models running in different slots at once, that
  needs a compose-level change (a second `vllm`-family service) — flagged
  here since docs/CONTRACTS.md §7's concurrency rule (text+vision pairing)
  doesn't call this constraint out explicitly.

## Verification commands

Run these as `niyam-gb10` after `bootstrap.sh` has installed everything, to
confirm the boundary actually holds:

```sh
# Secrets file must be unreadable to the unprivileged user.
cat /etc/spark-ai-stack/secrets.env
# -> Permission denied

# Without --harden applied, this still works (expected, pre-hardening).
# After `sudo ./bootstrap.sh --harden` and a fresh login, it must fail:
docker ps
# -> permission denied while trying to connect to the Docker daemon socket

# NOPASSWD verbs run without a password prompt:
sudo spark-ai-ctl status --json

# Password-gated verbs prompt (or fail non-interactively without one):
sudo spark-ai-ctl sync
sudo spark-ai-ctl secrets-status

# secrets-status only ever reports name/set/length, never values:
sudo spark-ai-ctl secrets-status

# A verb outside the enumerated set is refused by spark-ai-ctl itself,
# even if someone tries to run it directly as root:
sudo spark-ai-ctl rm-rf-everything
# -> argparse error: invalid choice

# Nothing the stack publishes should be reachable off loopback:
ss -tlnp | grep -v 127.0.0.1
# -> no spark-ai-stack ports listed
```

## Cross-references

- `docs/CONTRACTS.md` §1 (paths), §2 (this model's source of truth), §5
  (verb interface), §6 (secrets keys/exposure) — binding spec.
- `security/spark-ai-ctl` — the wrapper implementing the above.
- `security/sudoers-spark-ai-stack` — the NOPASSWD grant, with its own header
  comment.
- `bootstrap.sh` — installs all of the above; re-run any time, `--harden` to
  drop `niyam-gb10` from the `docker` group.
