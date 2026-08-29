# evals/sanity — post-activation sanity gate

`run.py` is a small, dependency-free (stdlib-only: `urllib`, `json`, `argparse`, `base64`, `time`)
HTTP client that hits an OpenAI-compatible `/v1/chat/completions` endpoint with a handful of
targeted probes. It exists so `modelctl activate <model>` can refuse to leave a broken backend
serving traffic. See `docs/CONTRACTS.md` §10 for the binding spec this implements.

## Usage

```
evals/sanity/run.py --api-base http://127.0.0.1:<port>/v1 --model <served_name> \
    [--role text|vision] [--long-context] [--json] \
    [--probes basic,template,repetition,stop,tool_call] \
    [--probe-timeout SECONDS] [--budget SECONDS] [--api-key TOKEN]
```

- Default probe set ("fast set"): `basic, template, repetition, stop, tool_call`.
- `--role vision` adds the `vision` probe to the set.
- `--long-context` adds the `long_context` probe to the set.
- `--probes a,b,c` overrides the derived set entirely with an explicit list (also accepts
  `long-context` with a hyphen, normalized internally).
- `--json` prints exactly `{probe: {pass, detail, latency_ms}}` and nothing else to stdout.
  Without `--json`, a human-readable table is printed.

Exit codes: `0` all selected probes passed, `1` ran to completion but at least one probe failed
its pass criteria, `2` error (transport failure surviving one retry, unknown probe name, or the
overall time budget was exceeded before every selected probe could run).

Budget: one connection-error retry per HTTP call (never retried on an assertion failure — a
probe that runs and gets a bad answer is real signal, not flakiness). Overall wall-clock budget
defaults to 180s for the fast set, +400s if `--long-context` is selected, +60s if the vision
probe is selected (override with `--budget`).

## What each probe catches

| Probe | Failure mode it catches | Why it matters |
|---|---|---|
| `basic` | Backend not answering coherently at all: wrong model loaded, broken sampling params, engine crash-looping | The baseline "is anything alive and correct" check — everything else assumes this passes |
| `template` | Wrong or missing chat template applied by the serving engine (raw `<|im_start|>`/`<|im_end|>`/`<|endoftext|>`/`[INST]`/`</s>`/`<|assistant|>` tokens leaking into the visible answer), the literal prompt being echoed back, or `<think>` reasoning leaking into `content` instead of routing to a separate reasoning field | A chat template mismatch (e.g. wrong tokenizer config, wrong `--chat-template` flag, or a base model served without an instruct template) is one of the most common "looks like it booted but is actually broken" failures after a model swap |
| `repetition` | Broken/greedy-degenerate sampling, missing EOS handling, or a bad `repetition_penalty`/temperature default that lets the model loop | Detected via a sliding 12-gram window over the generated text — 4+ identical repeats of the same 12 consecutive tokens is a strong degenerate-loop signal, independent of content |
| `stop` | Two independent sub-checks: (1) `stop=[...]` sequences not being honored by the serving engine (backend ignoring or mis-implementing stop-string matching), and (2) `max_tokens` not being honored (`finish_reason` should be `length` when generation is clipped) | Both are classic engine-config regressions (e.g. a serving flag change that silently drops stop-string support, or an off-by-one in token accounting) that are otherwise invisible until a downstream client (OpenWebUI, an agent loop) breaks in a confusing way |
| `tool_call` | Function-calling / tool-parser misconfiguration: wrong `--tool-call-parser`, wrong tokenizer/template for tool syntax, or the backend not honoring `tool_choice` | Forces `tool_choice="required"` against a single trivial `get_weather(city)` tool and requires the returned `tool_calls[0].function.arguments` to parse as JSON with a string `city` — if the backend's tool parser is misconfigured (which is easy to get wrong per-model), this either errors outright or returns unparsable garbage instead of failing basic chat |
| `long_context` (optional) | Context-length regressions: truncation, positional-encoding issues (e.g. RoPE scaling misconfigured), or KV-cache/attention bugs that only show up at long sequence lengths | Builds a ~32k-token haystack of filler sentences with a needle codeword inserted at ~60% depth and asks for retrieval — slow, so it's opt-in via `--long-context`, not part of the fast gate |
| `vision` (role=vision only) | Vision encoder/projector not wired up correctly, or the backend silently ignoring `image_url` content and just answering from text | Sends an in-code-generated (no external asset) solid-red 32x32 PNG and requires the model to name the dominant color |

## How `modelctl` uses this as an activation gate

Per §9 (`bin/modelctl`), after `activate <model>` brings a backend up and it reports healthy,
modelctl runs the fast probe set (1–5: `basic, template, repetition, stop, tool_call`) against
the model's slot port with `--json`. On any non-zero exit, modelctl treats the activation as
failed: it calls `spark-ai-ctl backend-stop` on the just-started backend and restores the
previous active set (rollback), so a broken model never stays live as the thing traffic gets
routed to. `modelctl sanity <model>` runs the full probe set (including `long_context` and, for
vision-role models, `vision`) on demand for a deeper check outside the activation path.

## Adding a probe

1. Write a function `probe_<name>(ctx) -> (ok: bool, detail: str)` in `run.py`. Use
   `ctx.api_base`, `ctx.model`, `ctx.api_key`, `ctx.probe_timeout`, and the `chat_completion()` /
   `first_message()` / `finish_reason()` helpers already in the module.
   - Raise nothing for a normal probe failure — just return `(False, "why")`.
   - Let `TransportError` and `RuntimeError` (raised by `chat_completion`/`_post_json` on
     connection failure or HTTP error responses, respectively) propagate; the runner already
     catches and reports them.
2. Register it in the `PROBE_FUNCS` dict.
3. Decide whether it belongs in `FAST_PROBES` (runs by default, must stay fast and cheap — this
   is on the activation-gate critical path) or should be opt-in like `long_context`/`vision`
   (add a flag or rely on `--probes` to select it explicitly).
4. Document it in the table above: what real-world failure it catches and why that failure mode
   matters.
