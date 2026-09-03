# Hermes agent — future-phase integration stub

Not implemented. This document records the intended integration points for a
future phase that runs [NousResearch's Hermes Agent](https://github.com/NousResearch)
against this stack, so the shape is settled before anyone starts wiring it
up. Nothing described here exists yet in `compose/`, `config/`, or
`security/` — no code in this repo currently references Hermes.

## Where it runs

Hermes runs as a **separate service** — most likely its own `docker compose`
project (or a single container/systemd unit; not yet decided), independent
of the `spark-ai` compose project this repo defines. It is a *consumer* of
this stack's endpoints, not a component of it. It should **not** be added to
`compose/compose.yml` or brought under `spark-ai-ctl`'s verb surface — that
wrapper's whole design (`docs/CONTRACTS.md` §2, `docs/SECURITY.md`) assumes a
small, enumerated, line-reviewed set of privileged operations, and Hermes
(an external, actively-developed agent codebase) does not belong inside that
trust boundary.

## Integration points

| What | Where | Notes |
|---|---|---|
| LLM gateway | `http://127.0.0.1:4000/v1` (LiteLLM) | Loopback only — Hermes must run on the same box, or reach this over the tailnet at `/v1` per `docs/CONTRACTS.md` §8 if it runs elsewhere. |
| Auth | A **dedicated LiteLLM virtual key**, not `LITELLM_MASTER_KEY` | See "Must not get the master key" below. |
| Web research | `http://127.0.0.1:8888/search?format=json` (SearXNG) | Same instance the t640's LiteLLM already consumes (`docs/MIGRATION-T640.md`); loopback only unless reached via the tailnet `/search` mount. |
| Knowledge-base output | `~/ai-data/kb` | Already reserved for this in `docs/CONTRACTS.md` §1 (`~/ai-data/kb — knowledge-base dir (future Hermes hook)`). Directory exists (created by `bootstrap.sh`) but nothing writes to it yet. |
| User-facing channel | Telegram bot | Chosen as the first channel. WhatsApp is harder to integrate (business API requirements, etc.) — revisit later, not blocking. |

## Must NOT get `LITELLM_MASTER_KEY`

Hermes must authenticate to the gateway with a **scoped LiteLLM virtual key**
(LiteLLM's own virtual-key feature — a key that maps to a restricted budget
and/or model subset, created and revocable independently of the master key),
never the master key itself. Rationale, consistent with this repo's whole
security posture (`docs/SECURITY.md`): the master key is the credential
`env.d/litellm.env` and `env.d/openwebui.env` are built around, and it's
treated as maximally sensitive precisely because it's the top of the trust
chain for this stack's one public-facing endpoint. An externally-developed
agent codebase — especially one that will eventually hold a Telegram bot
token and make outbound tool calls — is exactly the kind of component that
should hold the least-privileged credential available, not the most.

Virtual-key creation is a LiteLLM admin API/UI operation
(`/key/generate` against the running gateway, authenticated with the master
key) done once by a human at setup time — not something `spark-ai-ctl` needs
to grow a verb for.

## Trust boundary

Hermes and its configuration (its own compose project or unit files, its
Telegram bot token, its virtual LiteLLM key) live **outside**
`/etc/spark-ai-stack/` entirely. It is not covered by `sudo spark-ai-ctl
sync`'s trust gate, is not started by `spark-ai-core.service`, and is not a
dependency of anything in this repo's core stack. This stack's job stops at
handing Hermes a scoped API key and two loopback (or tailnet) URLs; anything
Hermes does with them is out of scope for `docs/CONTRACTS.md` and
`docs/SECURITY.md`.

## Open questions (for whoever implements this phase)

- Exact deployment shape: separate compose project vs. a single container
  vs. a native systemd unit (mirrors the `ds4`/`llamacpp-fork` "own systemd
  unit, outside compose" pattern already used for experimental backends).
- Which model(s) Hermes's virtual key should be scoped to — likely the boot
  model (`qwen3.6-35b`) plus `local/default`, not every catalog entry.
- Whether `~/ai-data/kb` needs its own retention/backup policy once
  something actually writes to it (see `docs/RECOVERY.md`'s backup list —
  it isn't in there yet because nothing populates it today).
- WhatsApp as a second channel, if/when the Telegram integration proves out.
