# OpenRouter cloud model portfolio

## Stable client aliases

The repository uses intent-based aliases instead of exposing provider model
names to clients. The underlying models are a reviewed snapshot of the live
OpenRouter catalog on 2026-08-26:

| Alias | OpenRouter model | Input / output per million tokens | Use it for |
|---|---|---:|---|
| `cloud/economy` | `deepseek/deepseek-v4-flash-0731` | $0.04 / $0.08 | Cheapest capable text, reasoning, coding, and agents |
| `cloud/general` | `openai/gpt-5.6-luna` | $0.20 / $1.20 | Fast general work, files, images, and light agents |
| `cloud/multimodal` | `google/gemini-3.7-flash` | $0.375 / $1.875 | Stronger reasoning plus image, audio, video, and files |
| `cloud/coding` | `bytedance-seed/seed-2.0-code` | $0.50 / $3.00 | Agentic coding and multilingual software work |

All four advertised models supported tools, tool choice, and structured output
when selected. The portfolio deliberately avoids free endpoints, which can
have unpredictable availability, and models above $1/M input or $5/M output.
Prices and capabilities can change at any time; the checked-in values are not
a billing guarantee.

The aliases appear in LiteLLM only when `OPENROUTER_API_KEY` is non-empty in
`~/.config/spark-ai-stack/secrets.env`. The HPT640 import preserves that key
but deliberately does not preserve its outdated model aliases.

## Review and update

Show the checked-in selection:

```bash
bin/cloud-models list
```

Compare the selection with OpenRouter's current catalog:

```bash
bin/cloud-models check
```

`check` reports a missing model, changed canonical release, changed token
prices, or a regression in tool/structured-output support. It does not modify
configuration. A price change is informational and exits successfully; a
missing model exits non-zero.

To change the portfolio, edit `config/cloud-models.yaml`, keep the stable alias
names unless their intent changes, then run:

```bash
bin/cloud-models list
bin/cloud-models check
tests/static.sh
bin/stack apply
```

Test each changed alias through LiteLLM before relying on it. Review one cloud
portfolio change per commit so the old routing file remains easy to restore.

## Selection source

- [OpenRouter live model catalog](https://openrouter.ai/api/v1/models)

