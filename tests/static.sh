#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m py_compile bin/modelctl bin/doctor tests/test_modelctl.py tests/acceptance.py
bash -n bootstrap bin/stack bin/cleanup-legacy tests/static.sh tests/soak/run.sh

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
    assert not re.search(r"\b100(?:\.\d{1,3}){3}\b", content), f"tailnet IP: {path}"
assert not list(root.rglob("secrets.env"))
PY

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck bootstrap bin/stack bin/cleanup-legacy tests/static.sh tests/soak/run.sh
fi
if command -v yamllint >/dev/null 2>&1; then
    yamllint -c tests/yamllint.yml .
fi
if [[ -x .venv/bin/ansible-playbook ]]; then
    .venv/bin/ansible-playbook --syntax-check -i ansible/inventory/hosts.yml ansible/site.yml
fi

temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
mkdir -p "$temporary/data/state" "$temporary/data/cache/searxng"
printf 'model_list: []\ngeneral_settings:\n  master_key: test\n' >"$temporary/data/state/litellm-config.yaml"
printf 'server:\n  secret_key: test\n' >"$temporary/data/state/searxng-settings.yml"
printf 'LITELLM_MASTER_KEY=test\n' >"$temporary/data/state/litellm.env"
printf 'SEARXNG_BASE_URL=http://127.0.0.1:8888/search/\n' >"$temporary/data/state/searxng.env"
printf 'HF_TOKEN=\n' >"$temporary/data/state/vllm.env"
printf 'LITELLM_MASTER_KEY=test\nSEARXNG_SECRET=test\nSEARXNG_BASE_URL=http://127.0.0.1:8888/search/\nHF_TOKEN=\n' >"$temporary/secrets.env"
SPARK_AI_DATA_ROOT="$temporary/data" \
SPARK_AI_SECRETS_FILE="$temporary/secrets.env" \
docker compose --project-directory compose --file compose/compose.yml config --quiet

python3 -m unittest -v tests/test_modelctl.py
echo "Static validation passed."
