#!/usr/bin/env bash
# evals/lm-eval/run.sh -- on-demand lm-evaluation-harness benchmark run against a locally
# served OpenAI-compatible model. See docs/CONTRACTS.md §11 and evals/lm-eval/README.md.
#
# Usage:
#   run.sh <served_model_name> <port> [--tasks gsm8k_cot,arc_easy] [--limit N]
#
# Examples:
#   evals/lm-eval/run.sh qwen3.6-35b 8001 --tasks arc_easy --limit 50
#   evals/lm-eval/run.sh qwen3.6-27b 8002 --tasks gsm8k_cot,arc_easy
#
# By default this runs the pinned lm-eval container via `sudo docker run` (the post-hardening
# user has no docker group membership -- see docs/CONTRACTS.md §2 -- so eval runs are an
# on-demand, human-triggered, password-gated action; that's an accepted tradeoff, not a bug).
#
# Set LM_EVAL_LOCAL=1 to instead invoke a host `lm_eval` binary directly (e.g. from a venv you
# manage yourself) with no docker/sudo involved:
#   LM_EVAL_LOCAL=1 evals/lm-eval/run.sh qwen3.6-35b 8001 --tasks arc_easy --limit 50

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_TAG="spark-ai/lm-eval:local"

usage() {
  echo "usage: $(basename "$0") <served_model_name> <port> [--tasks t1,t2,...] [--limit N]" >&2
  exit 2
}

[ $# -ge 2 ] || usage

MODEL="$1"; shift
PORT="$1"; shift

TASKS="arc_easy"
LIMIT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --tasks)
      TASKS="$2"; shift 2 ;;
    --tasks=*)
      TASKS="${1#--tasks=}"; shift ;;
    --limit)
      LIMIT="$2"; shift 2 ;;
    --limit=*)
      LIMIT="${1#--limit=}"; shift ;;
    -h|--help)
      usage ;;
    *)
      echo "unknown argument: $1" >&2
      usage ;;
  esac
done

case "$PORT" in
  ''|*[!0-9]*) echo "port must be numeric, got: $PORT" >&2; exit 2 ;;
esac

STATE_ROOT="${SPARK_EVAL_STATE_ROOT:-$HOME/ai-data/state/evals}"
OUT_DIR="$STATE_ROOT/$MODEL"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$OUT_DIR/$STAMP.json"

mkdir -p "$OUT_DIR"

SCRATCH="$(mktemp -d)"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

MODEL_ARGS="base_url=http://127.0.0.1:${PORT}/v1/completions,model=${MODEL},tokenized_requests=False"

LM_EVAL_ARGS=(
  --model local-completions
  --model_args "$MODEL_ARGS"
  --tasks "$TASKS"
  --output_path /out
)
if [ -n "$LIMIT" ]; then
  LM_EVAL_ARGS+=(--limit "$LIMIT")
fi

echo "== lm-eval: model=$MODEL port=$PORT tasks=$TASKS limit=${LIMIT:-<none>}" >&2

if [ "${LM_EVAL_LOCAL:-0}" = "1" ]; then
  echo "== running host-installed lm_eval (LM_EVAL_LOCAL=1, no docker/sudo)" >&2
  command -v lm_eval >/dev/null 2>&1 || {
    echo "lm_eval not found on PATH -- install with: pip install 'lm_eval[api]==0.4.12'" >&2
    exit 2
  }
  # Local mode: point --output_path straight at the scratch dir (same as container mode) so
  # the result-file discovery logic below is identical for both paths.
  LM_EVAL_ARGS[$(( ${#LM_EVAL_ARGS[@]} - 1 ))]="$SCRATCH"
  lm_eval "${LM_EVAL_ARGS[@]}"
else
  echo "== running containerized lm-eval via sudo docker (eval runs require sudo post-hardening)" >&2
  if ! sudo docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
    echo "== image $IMAGE_TAG not found locally, building from $SCRIPT_DIR/Dockerfile" >&2
    sudo docker build -t "$IMAGE_TAG" -f "$SCRIPT_DIR/Dockerfile" "$SCRIPT_DIR"
  fi
  sudo docker run --rm \
    --network host \
    -v "$SCRATCH:/out" \
    "$IMAGE_TAG" \
    "${LM_EVAL_ARGS[@]}"
fi

RESULT_JSON="$(find "$SCRATCH" -type f -name '*.json' ! -name '*README*' | sort | head -n1)"
if [ -z "$RESULT_JSON" ]; then
  echo "error: lm_eval did not produce a results JSON file under $SCRATCH" >&2
  exit 1
fi

cp "$RESULT_JSON" "$TARGET"
echo "== results archived to: $TARGET" >&2
