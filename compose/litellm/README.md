# Generated LiteLLM configuration

`bin/modelctl` writes the active route set atomically to
`~/ai-data/state/litellm-config.yaml`. This directory intentionally contains no
checked-in API keys or static model routes. The gateway is restarted only after
a backend has passed its health check.
