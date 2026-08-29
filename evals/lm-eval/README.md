# evals/lm-eval — on-demand benchmark regression tracking

This is a containerized [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness)
(`lm_eval[api]`, pinned in `Dockerfile`) used for on-demand, human-triggered benchmark runs
against locally served models (`modelctl eval <model> [--tasks ...]`). It is **not** part of the
always-on compose stack and is not run automatically post-activation — that's what
`evals/sanity/` is for. See `docs/CONTRACTS.md` §11.

## Why `local-completions`, not `local-chat-completions`

Sensible smoke tasks for base sampling/reasoning quality (`arc_easy`, `gsm8k_cot`, etc.) are
scored either by exact-match generation or, for several standard multiple-choice tasks, by
**comparing per-option log-probabilities** returned from `/v1/completions`. The OpenAI chat
endpoint (`/v1/chat/completions`) does not return per-token logprobs over arbitrary continuations
the way the raw completions endpoint does, so lm-eval's `local-chat-completions` model type only
supports the subset of tasks that use pure generate-and-match scoring. Using `local-completions`
against `/v1/completions` keeps the full task set available, including logprob-based tasks, and
matches how `run.sh` wires `model_args` (`base_url=.../v1/completions`, `tokenized_requests=False`
since the served backend expects raw text, not pre-tokenized input).

## Suggested smoke tasks

Keep runs short — this is a regression check, not a full evaluation suite:

- `arc_easy` — small multiple-choice reasoning set, fast, good default sanity signal for "did
  quality regress after a quant/config change."
- `gsm8k_cot` — grade-school math with chain-of-thought; run with `--limit 50` (or similar) to
  keep wall-clock reasonable, since full GSM8K generation runs are slow on a single GPU.

Example:

```
evals/lm-eval/run.sh qwen3.6-35b 8001 --tasks arc_easy --limit 50
evals/lm-eval/run.sh qwen3.6-27b 8002 --tasks gsm8k_cot,arc_easy --limit 50
```

Add more tasks from lm-eval's built-in registry as needed; keep `--limit` set for anything
generation-heavy so a routine check doesn't turn into a multi-hour run.

## Runtime expectations

- `arc_easy` (no limit): a few minutes against a local backend.
- `gsm8k_cot` with `--limit 50`: several minutes (chain-of-thought generations are long).
- Unlimited `gsm8k_cot` or similar large generation tasks: potentially 30+ minutes depending on
  backend throughput and context length — use `--limit` unless you deliberately want the full run.

Results are archived to `~/ai-data/state/evals/<model>/<UTC date-time>.json`. Use
`compare.py <model>` afterward to see metric deltas against the previous run for that model.

## sudo requirement

Per `docs/CONTRACTS.md` §2, the `niyam-gb10` user has no `docker` group membership post-hardening
(that's intentional — it closes the "docker group membership == root" escalation path for AI
coding agents running as that user). `run.sh` therefore invokes `sudo docker build`/`sudo docker
run` by default. This means:

- Every `run.sh` invocation (docker mode) will prompt for the sudo password unless you have a
  narrowly-scoped NOPASSWD rule for it yourself — evals are **not** wired into any NOPASSWD
  sudoers entry (`spark-ai-ctl`'s sudoers drop-in, §5, does not cover this).
- This is accepted as a deliberate tradeoff: benchmark runs are infrequent, human-triggered,
  and not on any hot path, so a password prompt is a reasonable price for keeping eval tooling
  out of the privileged/NOPASSWD surface.
- If you'd rather avoid docker and sudo entirely, set up your own Python venv with
  `pip install 'lm_eval[api]==0.4.12'` (match the version pinned in `Dockerfile`) and run with
  `LM_EVAL_LOCAL=1 evals/lm-eval/run.sh <model> <port> [...]` — this calls the host `lm_eval`
  binary directly, no docker/sudo involved.

## Caveat: this is regression tracking, not a leaderboard

The served backend here is typically quantized (NVFP4/GGUF quant, speculative decoding, custom
sampling defaults, a specific chat template, `--max-model-len` truncation, etc.) and lm-eval is
being pointed at the *raw completions* endpoint of that specific serving stack. Scores from these
runs are **only meaningful compared to a previous run of the same model on this same stack** —
they should not be quoted as, or compared against, published leaderboard numbers for the
upstream model, which are almost always produced with unquantized weights, different prompting/
few-shot settings, and a different (often vendor-controlled) harness configuration. Use
`compare.py` to track "did this change make things worse," not "how does this model rank."

## Files

- `Dockerfile` — `python:3.12-slim` (multi-arch, runs fine on the GX10's aarch64 host) with
  `lm_eval[api]` pinned to an exact version; entrypoint is the `lm_eval` CLI.
- `run.sh <served_model_name> <port> [--tasks t1,t2] [--limit N]` — runs the container (or, with
  `LM_EVAL_LOCAL=1`, a host-installed `lm_eval`) against `http://127.0.0.1:<port>/v1/completions`
  and archives the resulting JSON.
- `compare.py <model> [--last N]` — prints per-task/metric deltas between archived runs for a
  model, newest vs. previous, with ▲/▼ markers.
