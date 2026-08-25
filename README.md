# spark-ai-stack

Repeatable, locked local-AI infrastructure for an ASUS Ascent GX10 or NVIDIA
DGX Spark. It provides one authenticated OpenAI-compatible endpoint over
Tailscale while keeping every backend and SearXNG on loopback.

The stack deliberately does **not** manage BIOS, firmware, the kernel, the
NVIDIA driver, CUDA, or DGX OS. Recover those with the machine vendor's image
and update path, then use this repository for the application layer.

## What runs

| Component | Loopback address | Purpose |
|---|---:|---|
| Open WebUI | `127.0.0.1:3000` | Household chat UI, users, and uploads |
| LiteLLM | `127.0.0.1:4000` | Stable OpenAI gateway, virtual keys, and routing |
| PostgreSQL 16 | `127.0.0.1:5432` | Open WebUI and LiteLLM state |
| vLLM | `127.0.0.1:8001` | Default NVIDIA Qwen3.6 35B backend |
| llama.cpp | `127.0.0.1:8002` | Small GGUF backend |
| DS4 | `127.0.0.1:8003` | Exclusive DeepSeek V4 Flash backend |
| SearXNG | `127.0.0.1:8888` | Private metasearch |

Tailscale Serve publishes Open WebUI at `https://<magicdns-name>/`, the API at
`/v1`, and SearXNG at `/search/`. Funnel and LAN listeners are not used.
The intent-based OpenRouter aliases `cloud/economy`, `cloud/general`,
`cloud/multimodal`, and `cloud/coding` appear only when the provider key is
configured; local aliases continue to be managed transactionally.

Pi-hole and AdGuard Home are optional, mutually exclusive profiles. Neither is
part of the core unit or enabled by `stack apply`; see the DNS guide before
making the GPU host a household dependency.

## Fresh installation

Start with a supported vendor DGX OS baseline, join Tailscale interactively,
and clone this repository as the primary Linux user. You need internet access,
an accepted Hugging Face license for the NVIDIA model, and at least 150 GiB
free.

```bash
git clone https://github.com/YOUR-ACCOUNT/spark-ai-stack.git ~/code/spark-ai-stack
cd ~/code/spark-ai-stack
./bootstrap
bin/stack secrets-init
${EDITOR:-nano} ~/.config/spark-ai-stack/secrets.env
bin/stack apply
bin/modelctl fetch qwen3.6-35b
bin/modelctl activate qwen3.6-35b --default
bin/stack tailscale-configure
bin/doctor --full
```

`bootstrap` installs Ansible in a repository-local virtual environment. The
playbook installs missing application packages, builds the native SM121
backends, installs user services, enables linger, and refuses unsupported host
baselines. It never upgrades the vendor platform layer.

To migrate the HPT640 PostgreSQL databases, Open WebUI files, gateway secrets,
and provider keys, use the staged procedure in
[HPT640 migration and cutover](docs/HPT640_MIGRATION.md). It keeps the old host
recoverable. DNS migration is a separate, optional cutover.

On an already-configured GX10, the separate model-artifact migration remains:

```bash
bin/stack secrets-init --import-hf-token
bin/stack migrate
bin/stack apply
bin/modelctl fetch qwen3.6-35b
bin/modelctl fetch qwen3.5-4b-gguf
bin/modelctl fetch deepseek-v4-flash
bin/modelctl verify all
bin/modelctl activate qwen3.6-35b --default
```

Migration hard-links existing GGUFs when possible, copies the named Hugging
Face cache volume into `~/ai-data`, and leaves all old services and artifacts
available. It never imports the old SearXNG secret.

## Daily use

```bash
bin/modelctl status
bin/modelctl catalog
bin/modelctl activate qwen3.5-4b-gguf
bin/modelctl activate deepseek-v4-flash --default
bin/modelctl deactivate deepseek-v4-flash
bin/modelctl logs qwen3.6-35b
bin/stack status
bin/stack backup
bin/update
bin/cloud-models list
bin/cloud-models check
bin/dnsctl status              # only after optional DNS initialization
```

DS4 always runs exclusively. Deactivating it restores Qwen. Requests never
cold-start a model. `local/default` follows Qwen normally and DS4 while DS4 is
active; explicit model aliases remain stable.

Detailed guidance:

- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Architecture and memory policy](docs/ARCHITECTURE.md)
- [Operations, migration, upgrades, and rollback](docs/OPERATIONS.md)
- [HPT640 workload and Tailscale migration](docs/HPT640_MIGRATION.md)
- [OpenRouter cloud model selection](docs/CLOUD_MODELS.md)
- [Optional Pi-hole or AdGuard Home](docs/DNS_OPTIONS.md)
- [Routine upgrades, backups, and rollback](docs/UPGRADES.md)
- [ASUS/NVIDIA recovery boundary](docs/RECOVERY.md)
- [Compatibility evidence and promotion gates](docs/COMPATIBILITY.md)
