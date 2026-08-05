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
| LiteLLM | `127.0.0.1:4000` | Stable OpenAI gateway and API-key enforcement |
| vLLM | `127.0.0.1:8001` | Default NVIDIA Qwen3.6 35B backend |
| llama.cpp | `127.0.0.1:8002` | Small GGUF backend |
| DS4 | `127.0.0.1:8003` | Exclusive DeepSeek V4 Flash backend |
| SearXNG | `127.0.0.1:8888` | Private metasearch |

Tailscale Serve publishes only `https://<magicdns-name>/v1` and
`https://<magicdns-name>/search/`. Funnel and LAN listeners are not used.

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

On this already-configured GX10, run the non-destructive migration before the
playbook starts replacement services:

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
```

DS4 always runs exclusively. Deactivating it restores Qwen. Requests never
cold-start a model. `local/default` follows Qwen normally and DS4 while DS4 is
active; explicit model aliases remain stable.

Detailed guidance:

- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Architecture and memory policy](docs/ARCHITECTURE.md)
- [Operations, migration, upgrades, and rollback](docs/OPERATIONS.md)
- [ASUS/NVIDIA recovery boundary](docs/RECOVERY.md)
- [Compatibility evidence and promotion gates](docs/COMPATIBILITY.md)
