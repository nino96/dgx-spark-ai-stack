# Monitoring

Optional Prometheus + Grafana overlay for the spark-ai-stack. It is not part
of the core stack and does not start automatically.

## Enabling it

1. `sudo sparkctl sync` (password-gated). This is what actually copies files
   out of this user-writable repo into the root-owned `/etc/spark-ai-stack/`
   tree that privileged code reads from (docs/CONTRACTS.md sect 1/2). Nothing
   in `monitoring/` takes effect until this runs -- if you edit
   `monitoring/prometheus.yml` or a dashboard JSON and don't see the change,
   sync first.

   This assumes `sparkctl sync`'s copy step mirrors this directory the same
   way it mirrors `compose/monitoring.yml` itself, i.e.:
   - `monitoring/prometheus.yml` -> `/etc/spark-ai-stack/compose/prometheus.yml`
   - `monitoring/grafana/provisioning/` -> `/etc/spark-ai-stack/compose/grafana/provisioning/`
   - `monitoring/grafana/dashboards/` -> `/etc/spark-ai-stack/compose/grafana/dashboards/`

   That flattened layout (config lands directly under `compose/`, not nested
   under a `monitoring/` subdir in `/etc`) matches what `compose/monitoring.yml`
   mounts. **This is an integration note for whoever implements the `sync`
   verb** (workstream B) -- the exact source->dest mapping for files outside
   `compose/` isn't nailed down anywhere else; if `sync` lands these files at
   different paths, update the volume mounts in `compose/monitoring.yml`
   to match, or change `sync` to match this README.

2. `sparkctl monitoring up` (-> `sudo spark-ai-ctl monitoring-up`), which runs
   `docker compose --project-name spark-ai -f
   /etc/spark-ai-stack/compose/compose.yml -f
   /etc/spark-ai-stack/compose/monitoring.yml up -d` and starts three
   containers: `spark-ai-prometheus` (127.0.0.1:9090), `spark-ai-node-exporter`
   (host network, 127.0.0.1:9100), `spark-ai-grafana` (127.0.0.1:3001).

3. `sparkctl monitoring down` to stop it.

Grafana comes up pre-provisioned with a Prometheus datasource and one
dashboard (`GB10 Stack`, uid `gb10-stack`) -- no manual setup needed.

## Tailnet path

Per docs/CONTRACTS.md sect 8, Grafana is reachable on the tailnet at
`/grafana` via `tailscale serve` proxying to `127.0.0.1:3001`. That's why
`compose/monitoring.yml` sets `GF_SERVER_ROOT_URL` to
`%(protocol)s://%(domain)s/grafana` and `GF_SERVER_SERVE_FROM_SUB_PATH=true`
-- without both, Grafana's internal links and static assets break under a
sub-path proxy. `tailscale serve` configuration itself is
`sparkctl tailscale-configure`'s job, not this file's.

## GB10 has no NVML/nvidia-smi memory stats

This is the one fact that shapes every memory panel in the dashboard. GB10 is
unified-memory hardware (121 GiB shared between CPU and GPU); `nvidia-smi`
and NVML report no usable GPU memory figures on it. There is no DCGM /
nvidia-exporter container in this monitoring profile for that reason --
shipping one that can't produce memory data on this board would be worse than
not shipping it.

Consequences:

- **Memory pressure** (the thing that determines whether another model fits)
  is read entirely from `node_exporter`'s host memory metrics --
  specifically `node_memory_MemAvailable_bytes` (`/proc/meminfo`
  `MemAvailable`). This is the exact quantity modelctl's activation precheck
  budgets against: `sum(budget_gib of active models) + host_reserve_gib <
  MemAvailable-at-baseline` (docs/CONTRACTS.md sect 7). The dashboard's
  "Unified memory" row is meant to be read side-by-side with `modelctl
  status` / `sparkctl status` -- there's a text panel there rather than
  scripted annotations, since the budget numbers live in `config/models.yaml`
  YAML, not a metric Grafana can query.
- **GPU utilization, temperature, power, and clocks ARE available**, but only
  via the `nvidia-smi` CLI on the host -- there's no exporter for them wired
  up here. The dashboard's GPU row has a text panel spelling this out plus a
  best-effort timeseries pulling whatever `node_exporter` hwmon/thermal
  sensors the kernel happens to expose (may be empty on this board). For a
  real utilization/power/clock reading, run `watch -n1 nvidia-smi` (or
  `nvidia-smi --query-gpu=... --format=csv -l 1`) directly on the host.

## Grafana admin password -- integration note

`compose/monitoring.yml` wires an *optional* env file into grafana:
`env_file: [{path: /etc/spark-ai-stack/env.d/grafana.env, required: false}]`,
which would set `GF_SECURITY_ADMIN_PASSWORD` (and any other `GF_*` var) if
present. **As of this writing, `spark-ai-ctl secrets-init` does not generate
a grafana env file or key** -- the fixed secret key set in
docs/CONTRACTS.md sect 6 (`LITELLM_MASTER_KEY`, `POSTGRES_PASSWORD`,
`WEBUI_SECRET_KEY`, `SEARXNG_SECRET`, `HF_TOKEN`) has no grafana entry.

This is deliberately not fixed in this workstream (F owns `monitoring/*` and
`compose/monitoring.yml` only, not `security/spark-ai-ctl` or the secrets
key list -- that's workstream B's `secrets-init`/`sync` code). Flagging for
the coordinator / workstream B:

- Until `/etc/spark-ai-stack/env.d/grafana.env` exists, `required: false`
  means grafana starts anyway and falls back to **Grafana's own built-in
  default, `admin` / `admin`**. Change it on first login
  (`http://127.0.0.1:3001` or `https://<tailnet-name>/grafana`) -- there is
  no automated "force change on first login" env var in grafana-oss to
  enforce this, it's a manual step.
- `GRAFANA_ADMIN_PASSWORD` is part of the fixed secret key set: `sudo
  spark-ai-ctl secrets-init` generates it and derives
  `/etc/spark-ai-stack/env.d/grafana.env` containing
  `GF_SECURITY_ADMIN_PASSWORD=<value>`. Re-run `secrets-init` (or `sync`)
  after bootstrap and the admin/admin fallback above no longer applies.

## Image pinning -- integration note

`prom/prometheus`, `prom/node-exporter`, and `grafana/grafana-oss` are
tagged (`v3.0.1` / `v1.9.0` / `11.4.0`) but **not digest-pinned** in
`config/versions.lock.yaml` (that file is owned by workstream C). Flagging
for whoever maintains the lockfile/CI digest checks: add these three images
there if/when the digest-pin discipline used for the core stack's images
should extend to the monitoring overlay.

## Adding a scrape target

1. Add a `job_name` block to `monitoring/prometheus.yml`. If the target
   listens on a container's own compose network (joins the default `spark-ai`
   bridge network, no `network_mode: host`), use its compose service name as
   the host, e.g. `targets: ["myservice:9999"]`. If it listens on the host
   network directly (like `node-exporter`) or is a host-mapped port
   (like the vLLM slots), use `host.docker.internal:<port>` -- that hostname
   only resolves inside the `prometheus` container because of the
   `extra_hosts: host.docker.internal:host-gateway` entry already present on
   that service in `compose/monitoring.yml`.
2. `sudo sparkctl sync` to land the change at
   `/etc/spark-ai-stack/compose/prometheus.yml`.
3. Restart prometheus so it re-reads its config:
   `sparkctl monitoring down && sparkctl monitoring up` (there's no dedicated
   `monitoring-reload` verb in docs/CONTRACTS.md sect 5 -- a full down/up of
   the monitoring overlay is the supported path; it does not touch the core
   stack).
4. To surface the new target on the dashboard, add a panel to
   `monitoring/grafana/dashboards/gb10-stack.json` referencing it and sync
   again -- Grafana's dashboard provisioner (`updateIntervalSeconds: 30` in
   `monitoring/grafana/provisioning/dashboards/default.yml`) picks up file
   changes without a container restart.

## Files

| Path | Purpose |
|---|---|
| `compose/monitoring.yml` | prometheus / node-exporter / grafana service definitions (extra `-f` overlay) |
| `monitoring/prometheus.yml` | Prometheus scrape config |
| `monitoring/grafana/provisioning/datasources/prometheus.yml` | auto-provisions the Prometheus datasource |
| `monitoring/grafana/provisioning/dashboards/default.yml` | auto-provisions the dashboard file provider |
| `monitoring/grafana/dashboards/gb10-stack.json` | the one hand-written dashboard (uid `gb10-stack`) |
