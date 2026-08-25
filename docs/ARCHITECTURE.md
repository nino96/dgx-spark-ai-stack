# Architecture

## Topology

```text
tailnet client
     |
     | HTTPS (LiteLLM additionally requires a bearer key)
     v
Tailscale Serve (no Funnel)
     | /                  | /v1/*                  | /search/*
     v                    v                        v
Open WebUI            127.0.0.1:4000           127.0.0.1:8888
127.0.0.1:3000        LiteLLM                  SearXNG container
     |                    |
     +---- PostgreSQL ----+
          127.0.0.1:5432  +-- 127.0.0.1:8001  vLLM / Qwen3.6 35B
                          +-- 127.0.0.1:8002  llama.cpp / Qwen3.5 4B
                          `-- 127.0.0.1:8003  DS4 / DeepSeek V4 Flash
```

LiteLLM and Open WebUI use host networking but explicitly bind to `127.0.0.1`.
PostgreSQL, vLLM, and SearXNG publish only to `127.0.0.1`, and native servers
also bind only to loopback. Tailscale Serve is the sole ingress. SearXNG has
its own `/search/` path and is not injected into model prompts by this stack.

An optional Pi-hole or AdGuard Home profile sits outside this core topology.
When explicitly enabled, DNS is published only on the configured LAN and
Tailscale host addresses while its web console remains on loopback. The two
profiles are mutually exclusive and are not dependencies of the AI services.

## Persistent layout

```text
~/ai-data/
├── models/gguf/       # checksum-verified GGUFs
├── huggingface/       # bind-mounted HF_HOME
├── src/               # locked source checkouts
├── builds/            # native build output/symlinks
├── cache/searxng/     # search cache
├── postgres/          # Open WebUI and LiteLLM databases
├── open-webui/        # uploads, vector data, and UI files
├── dns/               # optional Pi-hole and AdGuard Home state
├── backups/           # private local logical backups (copy off-host)
└── state/             # active.json, routes, locks, derived mode-0600 service env

~/.config/spark-ai-stack/
└── secrets.env        # mode 0600, never tracked
```

The Git repository contains only declarative config and controller code. Large
weights, credentials, generated routes, logs, and state are excluded.

## Stable model names

| Client model | Backend | Policy |
|---|---|---|
| `qwen3.6-35b` | vLLM | Boot/default profile |
| `qwen3.5-4b-gguf` | llama.cpp | Manual, pending concurrency gate |
| `deepseek-v4-flash` | DS4 | Always exclusive |
| `local/default` | Generated alias | Qwen normally, DS4 while DS4 is active |

Cloud aliases are declarative in `config/cloud-models.yaml` and are appended to
the generated route file only when their provider secret is configured. They
do not participate in local memory admission or model activation.

The stable cloud aliases express intent—economy, general, multimodal, and
coding—so provider models can be reviewed and replaced without changing every
client.

The route generator includes only running, healthy models. Therefore
`GET /v1/models` reports active models rather than the entire catalog.
`modelctl catalog` is the authoritative view of installed and stopped models.

## State transition

```text
lock -> verify artifact -> check budget/pair policy -> snapshot old state
     -> stop conflicts -> start backend -> health check
     -> atomically replace routes -> restart/check gateway -> save state
                                  failure |
                                          v
                 stop attempted backend <- restore old services/routes/state
```

The lock is an OS file lock, so simultaneous CLI invocations serialize. State
updates use write/fsync/rename. API traffic never participates in activation,
which prevents surprise downloads and cold-start latency.

## Memory policy

GB10 memory is unified; `nvidia-smi` aggregate memory fields may be unavailable.
The controller uses Linux `MemTotal` and `MemAvailable` with declared budgets.
It keeps an 8 GiB reserve and rejects a combination whose budgets plus reserve
exceed physical or currently available memory. The soak gate separately
requires that swap does not grow under load.

Budgets are conservative admission values, not exact allocations:

| Model | Budget | Combination |
|---|---:|---|
| Qwen3.6 35B NVFP4 | 72 GiB | Exclusive until a pair gate passes |
| Qwen3.5 4B Q4_K_M | 6 GiB | Exclusive until a pair gate passes |
| DS4 0731 + drafter | 109 GiB | Always exclusive |

`config/concurrency.yaml` contains exact combinations and an evidence link.
The Qwen+llama entry ships as `pending`; it is not usable concurrently until a
same-machine soak updates it to `passed`. Any unlisted combination is
exclusive. DS4 cannot be allowlisted with another backend.

## Trust boundaries

- The vendor owns firmware, UEFI, DGX OS, kernel, driver, and CUDA baseline.
- Ansible owns application packages, checked-out sources, native builds,
  loopback services, and Tailscale Serve configuration.
- Hugging Face and container registries supply artifacts only at locked
  revisions/digests.
- LiteLLM owns client authentication. Backend ports trust loopback only.
- Tailscale identity protects network reachability; it does not replace the
  LiteLLM bearer key.
- Optional DNS serves only the explicitly bound private interfaces. Its admin
  interface remains on loopback and is independent of Tailscale Serve.
