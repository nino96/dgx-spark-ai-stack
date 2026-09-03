#!/usr/bin/env python3
"""
evals/sanity/run.py -- post-activation sanity gate for spark-ai-stack model backends.

Implements CONTRACTS.md §10 exactly. stdlib only (urllib, json, argparse, base64, time) --
no third-party dependencies, so it can run unprivileged from modelctl without a venv.

Usage:
    run.py --api-base http://127.0.0.1:8001/v1 --model qwen3.6-35b [--role text|vision]
           [--long-context] [--json] [--probes basic,template,repetition,stop,tool_call]

Exit codes:
    0  all selected probes passed
    1  ran to completion, at least one probe failed its pass criteria
    2  error: could not complete the run (transport failure after retry, bad arguments,
       overall time budget exceeded before all selected probes ran)
"""

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

FAST_PROBES = ["basic", "template", "repetition", "stop", "tool_call"]
ALL_PROBE_NAMES = FAST_PROBES + ["long_context", "vision"]

DEFAULT_PROBE_TIMEOUT_S = 60
LONG_CONTEXT_PROBE_TIMEOUT_S = 300
DEFAULT_FAST_BUDGET_S = 180
LONG_CONTEXT_BUDGET_BONUS_S = 400
VISION_BUDGET_BONUS_S = 60

RAW_TEMPLATE_TOKENS = [
    "<|im_start|>",
    "<|im_end|>",
    "<|endoftext|>",
    "[INST]",
    "</s>",
    "<|assistant|>",
]

# Minimal valid 32x32 solid-red PNG (8-bit RGB, no filter), generated once with
# zlib/struct and embedded as a constant per §10 vision-probe spec.
RED_32X32_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAJ0lEQVR42u3NsQkAAAjAsP7/"
    "tF7hIASyp6lTCQQCgUAgEAgEgi/BAjLD/C5w/SM9AAAAAElFTkSuQmCC"
)

LONG_CONTEXT_NEEDLE = "SPARKLE7429"
# ~7 chars/word incl. space; aim for roughly 32k tokens (~4 chars/token) of filler text.
LONG_CONTEXT_TARGET_CHARS = 32000 * 4
FILLER_SENTENCE = (
    "The quick brown fox jumps over the lazy dog near the riverbank at dawn. "
)


class TransportError(Exception):
    """Raised when an HTTP request could not be completed after one retry."""


# --------------------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------------------


def _post_json(url, payload, timeout, headers=None):
    """POST JSON, one retry on connection-level errors only (never on HTTP error
    responses or on assertion failures -- those are meaningful signal, not flakiness)."""
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)

    attempts = 0
    last_conn_err = None
    while attempts < 2:
        attempts += 1
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Server responded (even if with an error status) -- not a connection
            # failure, so surface it as-is without retrying.
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(e)
            raise RuntimeError("HTTP %d: %s" % (e.code, detail[:500]))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_conn_err = e
            if attempts < 2:
                time.sleep(0.5)
                continue
            raise TransportError("connection error after retry: %s" % e)
    raise TransportError("connection error: %s" % last_conn_err)


def chat_completion(api_base, payload, timeout, api_key=None):
    headers = {}
    if api_key:
        headers["Authorization"] = "Bearer %s" % api_key
    url = api_base.rstrip("/") + "/chat/completions"
    return _post_json(url, payload, timeout, headers)


def first_message(resp):
    return resp["choices"][0]["message"]


def finish_reason(resp):
    return resp["choices"][0].get("finish_reason")


# --------------------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------------------


def probe_basic(ctx):
    payload = {
        "model": ctx.model,
        "messages": [
            {"role": "user", "content": "What is 2+2? Answer with just the number."}
        ],
        "temperature": 0,
        "max_tokens": 512,
    }
    resp = chat_completion(ctx.api_base, payload, ctx.probe_timeout, ctx.api_key)
    msg = first_message(resp)
    content = (msg.get("content") or "").strip()
    fr = finish_reason(resp)
    ok = bool(content) and "4" in content and fr == "stop"
    detail = "content=%r finish_reason=%r" % (content[:120], fr)
    return ok, detail


def probe_template(ctx):
    prompt = (
        "Repeat the phrase 'the quick brown fox' once, then say hello in one short "
        "sentence."
    )
    payload = {
        "model": ctx.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 256,
    }
    # Best-effort: many chat-template-aware backends (vLLM/Qwen3 reasoning parser)
    # accept chat_template_kwargs to disable thinking. If the server rejects the
    # field outright, fall back to a plain request -- this is a capability probe,
    # not a network retry.
    try:
        payload_with_toggle = dict(payload)
        payload_with_toggle["chat_template_kwargs"] = {"enable_thinking": False}
        resp = chat_completion(
            ctx.api_base, payload_with_toggle, ctx.probe_timeout, ctx.api_key
        )
    except TransportError:
        raise
    except RuntimeError:
        resp = chat_completion(ctx.api_base, payload, ctx.probe_timeout, ctx.api_key)

    msg = first_message(resp)
    content = msg.get("content") or ""

    leaked_tokens = [t for t in RAW_TEMPLATE_TOKENS if t in content]
    has_think_leak = "<think>" in content or "</think>" in content
    echoed_prompt = prompt.strip().lower() in content.strip().lower()

    ok = not leaked_tokens and not has_think_leak and not echoed_prompt
    detail = "content=%r leaked_tokens=%s think_leak=%s echoed_prompt=%s" % (
        content[:120],
        leaked_tokens,
        has_think_leak,
        echoed_prompt,
    )
    return ok, detail


def _max_repeated_12gram(content):
    tokens = content.lower().split()
    n = 12
    if len(tokens) < n:
        return 0
    counts = Counter()
    for i in range(len(tokens) - n + 1):
        counts[tuple(tokens[i : i + n])] += 1
    return max(counts.values()) if counts else 0


def probe_repetition(ctx):
    payload = {
        "model": ctx.model,
        "messages": [
            {
                "role": "user",
                "content": "Write a 200-word story about a lighthouse keeper.",
            }
        ],
        "temperature": 0.8,
        "max_tokens": 200,
    }
    resp = chat_completion(ctx.api_base, payload, ctx.probe_timeout, ctx.api_key)
    msg = first_message(resp)
    content = msg.get("content") or ""
    max_repeat = _max_repeated_12gram(content)
    ok = max_repeat < 4
    detail = "max_12gram_repeat_count=%d content_len=%d" % (max_repeat, len(content))
    return ok, detail


def probe_stop(ctx):
    # Sub-check 1: stop sequence honored.
    payload_stop = {
        "model": ctx.model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Say the single word HELLO, then immediately say the word STOP, "
                    "then continue with several more sentences of unrelated text."
                ),
            }
        ],
        "temperature": 0,
        "max_tokens": 256,
        "stop": ["STOP"],
    }
    resp1 = chat_completion(ctx.api_base, payload_stop, ctx.probe_timeout, ctx.api_key)
    msg1 = first_message(resp1)
    content1 = msg1.get("content") or ""
    fr1 = finish_reason(resp1)
    stop_ok = "STOP" not in content1 and fr1 == "stop"

    # Sub-check 2: max_tokens honored -> finish_reason == length when clipped.
    payload_len = {
        "model": ctx.model,
        "messages": [
            {
                "role": "user",
                "content": "Write a detailed 300-word essay about the ocean.",
            }
        ],
        "temperature": 0,
        "max_tokens": 16,
    }
    resp2 = chat_completion(ctx.api_base, payload_len, ctx.probe_timeout, ctx.api_key)
    fr2 = finish_reason(resp2)
    length_ok = fr2 == "length"

    ok = stop_ok and length_ok
    detail = (
        "stop_sequence_honored=%s (finish_reason=%r content=%r) "
        "max_tokens_honored=%s (finish_reason=%r)"
        % (stop_ok, fr1, content1[:80], length_ok, fr2)
    )
    return ok, detail


def probe_tool_call(ctx):
    payload = {
        "model": ctx.model,
        "messages": [
            {"role": "user", "content": "What is the weather in Paris right now?"}
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "tool_choice": "required",
        "max_tokens": 256,
    }
    resp = chat_completion(ctx.api_base, payload, ctx.probe_timeout, ctx.api_key)
    msg = first_message(resp)
    tool_calls = msg.get("tool_calls") or []

    ok = False
    detail = "no tool_calls in response"
    for tc in tool_calls:
        fn = tc.get("function", {})
        args_raw = fn.get("arguments", "")
        try:
            args = json.loads(args_raw)
        except (TypeError, ValueError):
            detail = "tool_calls present but arguments not valid JSON: %r" % (
                args_raw,
            )
            continue
        city = args.get("city")
        if isinstance(city, str) and city:
            ok = True
            detail = "tool_calls[0].function.arguments=%r" % args_raw
            break
        detail = "tool_calls present but 'city' missing/not a string: %r" % (args,)

    return ok, detail


def probe_long_context(ctx):
    filler_repeats = (LONG_CONTEXT_TARGET_CHARS // len(FILLER_SENTENCE)) + 1
    haystack_parts = [FILLER_SENTENCE] * filler_repeats
    needle_index = int(len(haystack_parts) * 0.6)
    needle_sentence = (
        "The secret code word for this exercise is: %s. Remember it. "
        % LONG_CONTEXT_NEEDLE
    )
    haystack_parts.insert(needle_index, needle_sentence)
    haystack = "".join(haystack_parts)

    prompt = (
        haystack
        + "\n\nBased only on the text above, what is the secret code word? "
        "Answer with just the code word."
    )
    payload = {
        "model": ctx.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 64,
    }
    resp = chat_completion(
        ctx.api_base, payload, LONG_CONTEXT_PROBE_TIMEOUT_S, ctx.api_key
    )
    msg = first_message(resp)
    content = msg.get("content") or ""
    ok = LONG_CONTEXT_NEEDLE in content
    detail = "haystack_chars=%d content=%r" % (len(haystack), content[:120])
    return ok, detail


def probe_vision(ctx):
    data_uri = "data:image/png;base64," + RED_32X32_PNG_B64
    payload = {
        "model": ctx.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the single dominant color of this image? "
                        "Answer with just the color name.",
                    },
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 64,
    }
    resp = chat_completion(ctx.api_base, payload, ctx.probe_timeout, ctx.api_key)
    msg = first_message(resp)
    content = (msg.get("content") or "").strip().lower()
    ok = "red" in content
    detail = "content=%r" % content[:120]
    return ok, detail


PROBE_FUNCS = {
    "basic": probe_basic,
    "template": probe_template,
    "repetition": probe_repetition,
    "stop": probe_stop,
    "tool_call": probe_tool_call,
    "long_context": probe_long_context,
    "vision": probe_vision,
}


# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------


class Context:
    def __init__(self, api_base, model, api_key, probe_timeout):
        self.api_base = api_base
        self.model = model
        self.api_key = api_key
        self.probe_timeout = probe_timeout


def normalize_probe_name(name):
    return name.strip().replace("-", "_")


def resolve_probe_list(args):
    if args.probes:
        names = [normalize_probe_name(n) for n in args.probes.split(",") if n.strip()]
        unknown = [n for n in names if n not in PROBE_FUNCS]
        if unknown:
            raise SystemExit(
                "unknown probe name(s): %s (valid: %s)"
                % (", ".join(unknown), ", ".join(sorted(PROBE_FUNCS)))
            )
        return names

    names = list(FAST_PROBES)
    if args.long_context:
        names.append("long_context")
    if args.role == "vision":
        names.append("vision")
    return names


def run(args):
    probes = resolve_probe_list(args)

    budget = args.budget
    if budget is None:
        budget = DEFAULT_FAST_BUDGET_S
        if "long_context" in probes:
            budget += LONG_CONTEXT_BUDGET_BONUS_S
        if "vision" in probes:
            budget += VISION_BUDGET_BONUS_S

    ctx = Context(args.api_base, args.model, args.api_key, args.probe_timeout)

    results = {}
    start = time.monotonic()
    had_error = False
    budget_exceeded = False

    for i, name in enumerate(probes):
        elapsed = time.monotonic() - start
        if elapsed >= budget:
            budget_exceeded = True
            for remaining in probes[i:]:
                results[remaining] = {
                    "pass": False,
                    "detail": "skipped: overall time budget (%ds) exceeded" % budget,
                    "latency_ms": 0,
                }
            break

        probe_start = time.monotonic()
        try:
            ok, detail = PROBE_FUNCS[name](ctx)
            latency_ms = int((time.monotonic() - probe_start) * 1000)
            results[name] = {"pass": ok, "detail": detail, "latency_ms": latency_ms}
        except TransportError as e:
            latency_ms = int((time.monotonic() - probe_start) * 1000)
            results[name] = {
                "pass": False,
                "detail": "transport error: %s" % e,
                "latency_ms": latency_ms,
            }
            had_error = True
        except RuntimeError as e:
            latency_ms = int((time.monotonic() - probe_start) * 1000)
            results[name] = {
                "pass": False,
                "detail": "request error: %s" % e,
                "latency_ms": latency_ms,
            }
        except Exception as e:  # unexpected bug in a probe implementation
            latency_ms = int((time.monotonic() - probe_start) * 1000)
            results[name] = {
                "pass": False,
                "detail": "internal error: %s: %s" % (type(e).__name__, e),
                "latency_ms": latency_ms,
            }
            had_error = True

    all_pass = all(r["pass"] for r in results.values())

    if budget_exceeded or had_error:
        exit_code = 2
    elif all_pass:
        exit_code = 0
    else:
        exit_code = 1

    return results, exit_code


def print_human(results, model, api_base, exit_code):
    print("sanity: model=%s api_base=%s" % (model, api_base))
    for name, r in results.items():
        status = "PASS" if r["pass"] else "FAIL"
        print("  [%-4s] %-13s %6dms  %s" % (status, name, r["latency_ms"], r["detail"]))
    summary = {0: "PASS", 1: "FAIL", 2: "ERROR"}[exit_code]
    print("result: %s (exit %d)" % (summary, exit_code))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="spark-ai-stack sanity eval runner (CONTRACTS.md §10)"
    )
    parser.add_argument(
        "--api-base", required=True, help="OpenAI-compatible base URL, e.g. http://127.0.0.1:8001/v1"
    )
    parser.add_argument("--model", required=True, help="served model name")
    parser.add_argument(
        "--role", choices=["text", "vision"], default="text", help="model role (default: text)"
    )
    parser.add_argument(
        "--long-context", action="store_true", help="also run the long-context needle probe"
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--probes",
        help="comma-separated explicit probe list, overrides the default set derived "
        "from --role/--long-context (choices: %s)" % ", ".join(sorted(PROBE_FUNCS)),
    )
    parser.add_argument(
        "--probe-timeout",
        type=float,
        default=DEFAULT_PROBE_TIMEOUT_S,
        help="per-probe HTTP timeout in seconds (default: %d)" % DEFAULT_PROBE_TIMEOUT_S,
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="overall wall-clock budget in seconds for the whole run "
        "(default: %ds for the fast set, +%ds if --long-context, +%ds if role=vision)"
        % (DEFAULT_FAST_BUDGET_S, LONG_CONTEXT_BUDGET_BONUS_S, VISION_BUDGET_BONUS_S),
    )
    parser.add_argument(
        "--api-key", default=None, help="optional bearer token for the API base"
    )

    args = parser.parse_args(argv)

    try:
        results, exit_code = run(args)
    except SystemExit:
        raise
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print("error: %s" % e, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        print_human(results, args.model, args.api_base, exit_code)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
