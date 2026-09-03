#!/usr/bin/env python3
"""Unified endpoint and SearXNG acceptance checks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:4000/v1"
DEFAULT_SEARCH_URL = "http://127.0.0.1:8888/search"


def default_model() -> str | None:
    """Resolve the boot: true model from config/models.yaml, if present."""
    try:
        catalog = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8")) or {}
    except OSError:
        return None
    for name, spec in (catalog.get("models") or {}).items():
        if spec.get("boot"):
            return spec.get("served_model_name", name)
    return None


def request(url: str, key: str | None = None, body: dict | None = None) -> tuple[int, bytes]:
    headers = {}
    data = None
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def request_and_cancel(url: str, key: str, body: dict) -> tuple[int, bytes]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        data=data,
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        # Reading only the first event and closing the response exercises client
        # cancellation instead of waiting for the generation to complete.
        return response.status, response.readline()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"OpenAI-compatible base URL (default: {DEFAULT_BASE_URL}, the loopback LiteLLM gateway)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="served model name to exercise (default: the catalog's boot: true model)",
    )
    parser.add_argument(
        "--search-url",
        default=DEFAULT_SEARCH_URL,
        help=f"SearXNG search endpoint (default: {DEFAULT_SEARCH_URL})",
    )
    parser.add_argument(
        "--active-model",
        action="append",
        dest="active_models",
        help="expected active backend alias; repeat for concurrent profiles",
    )
    parser.add_argument(
        "--cloud-model",
        action="append",
        dest="cloud_models",
        help="expected configured cloud alias; repeat for multiple aliases",
    )
    parser.add_argument("--key-env", default="LITELLM_MASTER_KEY")
    parser.add_argument("--responses", action="store_true")
    args = parser.parse_args()
    if args.model is None:
        args.model = default_model()
        if args.model is None:
            print("--model was not given and no boot: true model was found in config/models.yaml", file=sys.stderr)
            return 2
    key = os.environ.get(args.key_env)
    if not key:
        print(f"{args.key_env} is empty", file=sys.stderr)
        return 2
    base = args.base_url.rstrip("/")

    status, _ = request(f"{base}/models", "invalid-key")
    assert status == 401, f"invalid key returned HTTP {status}"

    status, raw = request(f"{base}/models", key)
    assert status == 200
    listed = json.loads(raw)
    assert any(item["id"] == args.model for item in listed["data"])
    visible = {item["id"] for item in listed["data"]} - {"local/default"}
    expected = set(args.active_models or [args.model]) | set(args.cloud_models or [])
    assert visible == expected, {"visible": visible, "expected": expected}

    chat = {
        "model": args.model,
        "messages": [{"role": "user", "content": "Reply with only: OK"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    status, raw = request(f"{base}/chat/completions", key, chat)
    assert status == 200, raw[-1000:]
    assert json.loads(raw)["choices"]

    streaming = dict(chat, stream=True)
    status, raw = request(f"{base}/chat/completions", key, streaming)
    assert status == 200 and b"data:" in raw

    cancellation = dict(chat, stream=True, max_tokens=512)
    status, raw = request_and_cancel(f"{base}/chat/completions", key, cancellation)
    assert status == 200 and raw.startswith(b"data:"), raw[-1000:]

    tool_test = {
        "model": args.model,
        "messages": [{"role": "user", "content": "What is the weather in Doha?"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "weather",
                "description": "Get weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": "weather"}},
        "max_tokens": 64,
    }
    status, raw = request(f"{base}/chat/completions", key, tool_test)
    assert status == 200, raw[-1000:]
    tool_message = json.loads(raw)["choices"][0]["message"]
    assert tool_message.get("tool_calls"), raw[-1000:]

    if args.responses:
        status, raw = request(
            f"{base}/responses",
            key,
            {"model": args.model, "input": "Reply with only: OK", "max_output_tokens": 16},
        )
        assert status == 200, raw[-1000:]

    search = args.search_url.rstrip("/") + "/?q=DGX+Spark&format=json"
    status, raw = request(search)
    assert status == 200
    assert "results" in json.loads(raw)

    print("Unified endpoint and search acceptance checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
