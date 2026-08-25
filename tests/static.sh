#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m py_compile bin/modelctl bin/doctor bin/cloud-models tests/test_modelctl.py tests/acceptance.py
bash -n bootstrap bin/stack bin/update bin/dnsctl bin/migrate-hpt640 bin/cleanup-legacy tests/static.sh tests/soak/run.sh

python3 - <<'PY'
import json
from pathlib import Path
import re

root = Path(".")
for path in sorted((root / "config").glob("*.yaml")):
    json.loads(path.read_text())
for path in root.rglob("*"):
    if not path.is_file() or {".git", ".venv", "__pycache__"} & set(path.parts):
        continue
    assert path.stat().st_size < 1_000_000, f"large tracked candidate: {path}"
    assert path.suffix not in {".gguf", ".safetensors", ".pt", ".pth", ".onnx"}, path
    content = path.read_text(encoding="utf-8", errors="ignore")
    assert not re.search(r"\bhf_[A-Za-z0-9]{20,}\b", content), f"possible HF token: {path}"
    tailnet_ip = re.search(r"\b100(?:\.\d{1,3}){3}\b", content)
    if tailnet_ip:
        line = content.count("\n", 0, tailnet_ip.start()) + 1
        raise AssertionError(f"tailnet IP: {path}:{line}")
assert not list(root.rglob("secrets.env"))
compose = (root / "compose" / "compose.yml").read_text()
for service in ("postgres", "litellm", "open-webui", "searxng"):
    assert re.search(rf"^  {re.escape(service)}:\n(?:.*\n)*?    image: [^\n]+@sha256:[0-9a-f]{{64}}$", compose, re.MULTILINE), service
dns_compose = (root / "compose" / "dns.yml").read_text()
for service in ("pihole", "adguard"):
    assert re.search(rf"^  {re.escape(service)}:\n(?:.*\n)*?    image: [^\n]+@sha256:[0-9a-f]{{64}}$", dns_compose, re.MULTILINE), service
assert '"${DNS_LAN_IP}:53:53/tcp"' in dns_compose
assert '"${DNS_TAILSCALE_IP}:53:53/udp"' in dns_compose
assert '"0.0.0.0:53:53' not in dns_compose
assert '"127.0.0.1:8088:80/tcp"' in dns_compose
assert '"127.0.0.1:8089:80/tcp"' in dns_compose
for command in ("cloud-models", "dnsctl", "migrate-hpt640", "update"):
    assert (root / "bin" / command).stat().st_mode & 0o111, f"not executable: bin/{command}"
PY

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck bootstrap bin/stack bin/update bin/dnsctl bin/migrate-hpt640 \
        bin/cleanup-legacy tests/static.sh tests/soak/run.sh
fi
if command -v yamllint >/dev/null 2>&1; then
    yamllint -c tests/yamllint.yml .
fi
if [[ -x .venv/bin/ansible-playbook ]]; then
    .venv/bin/ansible-playbook --syntax-check -i ansible/inventory/hosts.yml ansible/site.yml
fi

temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
mkdir -p "$temporary/data/state" "$temporary/data/cache/searxng" "$temporary/data/postgres" "$temporary/data/open-webui"
printf 'model_list: []\ngeneral_settings:\n  master_key: test\n' >"$temporary/data/state/litellm-config.yaml"
printf 'server:\n  secret_key: test\n' >"$temporary/data/state/searxng-settings.yml"
printf 'LITELLM_MASTER_KEY=test\n' >"$temporary/data/state/litellm.env"
printf 'SEARXNG_BASE_URL=http://127.0.0.1:8888/search/\n' >"$temporary/data/state/searxng.env"
printf 'HF_TOKEN=\n' >"$temporary/data/state/vllm.env"
printf 'POSTGRES_USER=sparkai\nPOSTGRES_PASSWORD=test\n' >"$temporary/data/state/postgres.env"
printf 'CREATE DATABASE openwebui;\nCREATE DATABASE litellm;\n' >"$temporary/data/state/postgres-init.sql"
printf 'WEBUI_URL=http://127.0.0.1:3000\nWEBUI_SECRET_KEY=test\nHOST=127.0.0.1\nPORT=3000\nDATABASE_URL=postgresql://sparkai:test@127.0.0.1:5432/openwebui\nOPENAI_API_BASE_URL=http://127.0.0.1:4000/v1\nOPENAI_API_KEY=test\n' >"$temporary/data/state/open-webui.env"
printf 'LITELLM_MASTER_KEY=test\nPG_SUPERUSER=sparkai\nPG_SUPERPASS=test\nWEBUI_SECRET_KEY=test\nSEARXNG_SECRET=test\nSEARXNG_BASE_URL=http://127.0.0.1:8888/search/\nHF_TOKEN=\n' >"$temporary/secrets.env"
SPARK_AI_DATA_ROOT="$temporary/data" \
SPARK_AI_SECRETS_FILE="$temporary/secrets.env" \
docker compose --project-directory compose --file compose/compose.yml config --quiet

printf 'DNS_LAN_IP=192.0.2.10\nDNS_LAN_CIDR=192.0.2.0/24\nDNS_TAILSCALE_IP=192.0.2.11\nDNS_TIMEZONE=UTC\nPIHOLE_PASSWORD=test\n' >"$temporary/dns.env"
SPARK_AI_DATA_ROOT="$temporary/data" \
docker compose --project-directory compose --env-file "$temporary/dns.env" --file compose/dns.yml --profile pihole --profile adguard config --quiet

python3 -m unittest -v tests/test_modelctl.py
echo "Static validation passed."
