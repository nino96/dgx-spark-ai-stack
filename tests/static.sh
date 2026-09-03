#!/usr/bin/env bash
# Static validation for spark-ai-stack: syntax/parse checks that don't need
# docker, sudo, or a GPU. Safe to run in CI and on a live host alike.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- Python syntax -----------------------------------------------------
# bin/modelctl and bin/doctor are workstream D's; security/spark-ai-ctl and
# evals/sanity/run.py belong to other workstreams (B, E) and may not exist
# yet mid-refactor -- warn rather than fail so this stays useful in-flight.
python3 -m py_compile bin/modelctl bin/doctor tests/test_modelctl.py tests/acceptance.py

for candidate in security/spark-ai-ctl evals/sanity/run.py; do
    if [[ -f "$candidate" ]]; then
        python3 -m py_compile "$candidate"
    else
        echo "static.sh: warning: $candidate not present yet (other workstream in-flight); skipping py_compile" >&2
    fi
done

# --- Bash syntax ---------------------------------------------------------
mapfile -t bash_scripts < <(
    { [[ -f bootstrap.sh ]] && printf '%s\n' bootstrap.sh; \
      [[ -f bootstrap ]] && printf '%s\n' bootstrap; \
      find . -name '*.sh' -not -path './.venv/*' -not -path './.git/*'; } | sort -u
)
if [[ "${#bash_scripts[@]}" -gt 0 ]]; then
    bash -n "${bash_scripts[@]}"
fi
bash -n bin/sparkctl bin/cleanup-legacy

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck bin/sparkctl bin/cleanup-legacy "${bash_scripts[@]}"
fi

# --- YAML parse: every config/*.yaml and compose/*.yml -------------------
python3 - <<'PY'
from pathlib import Path
import yaml

root = Path(".")
targets = sorted((root / "config").glob("*.yaml")) + sorted((root / "compose").glob("*.yml"))
if not targets:
    raise SystemExit("static.sh: no config/*.yaml or compose/*.yml files found to parse")
for path in targets:
    yaml.safe_load(path.read_text(encoding="utf-8"))
    print(f"parsed: {path}")
PY

if command -v yamllint >/dev/null 2>&1; then
    yamllint -c tests/yamllint.yml .
fi

# --- Tracked-file hygiene + secret-pattern scan ---------------------------
python3 - <<'PY'
from pathlib import Path
import re

root = Path(".")
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", "node_modules"}
EXCLUDED_PREFIX = ("docs/legacy",)

for path in root.rglob("*"):
    if not path.is_file():
        continue
    parts = set(path.parts)
    if EXCLUDED_DIRS & parts:
        continue
    relative = path.relative_to(root).as_posix()
    if relative.startswith(EXCLUDED_PREFIX):
        continue
    assert path.stat().st_size < 1_000_000, f"large tracked candidate: {path}"
    assert path.suffix not in {".gguf", ".safetensors", ".pt", ".pth", ".onnx"}, path
    content = path.read_text(encoding="utf-8", errors="ignore")
    assert not re.search(r"\bhf_[A-Za-z0-9]{20,}\b", content), f"possible HF token: {path}"
    assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}\b", content), f"possible API key: {path}"
    assert not re.search(r"\b100(?:\.\d{1,3}){3}\b", content), f"possible tailnet IP: {path}"
assert not list(root.rglob("secrets.env")), "secrets.env must never be tracked"
print("secret-pattern scan: clean")
PY

# --- Unit tests ------------------------------------------------------------
python3 -m unittest discover -s tests -p 'test_*.py' -v

echo "Static validation passed."
