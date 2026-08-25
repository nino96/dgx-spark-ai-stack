# HPT640 to ASUS Ascent GX10 migration

## Scope and decisions

Move these workloads to the GX10:

- PostgreSQL 16 databases `openwebui` and `litellm`;
- Open WebUI users, chats, uploads, vector data, and its stable secret;
- the LiteLLM master key and cloud-provider keys;
- private Tailscale HTTPS for the UI, API, and search;
- optionally, the HPT640 subnet-router and exit-node duties.

The core migration does not automatically install DNS. Pi-hole and AdGuard Home
are supported as separate, mutually exclusive options after the AI cutover.
Running one on the GX10 couples household DNS availability to GPU-host reboots
and maintenance, so choose that tradeoff deliberately and follow
[DNS_OPTIONS.md](DNS_OPTIONS.md).

This means the HPT640 cannot be fully powered off while either the home router's
DHCP settings or the Tailscale admin DNS page still names it as a resolver.
Before retirement, either move DNS to a separately tested host, deliberately
enable one GX10 DNS profile, or temporarily return both places to a known
upstream resolver. Do not point clients at the GX10 before its resolver passes
both LAN and tailnet tests.

The OpenRouter provider key is migrated, but the outdated HPT640 LiteLLM model
aliases are deliberately not. The current intent-based portfolio is declared
in `config/cloud-models.yaml` and documented in
[CLOUD_MODELS.md](CLOUD_MODELS.md). A cloud route is rendered only when its
provider key is non-empty. The old remote Qwen route is intentionally replaced
by the GX10's loopback local-model route.

## What was discovered on the HPT640

The AI services bind only to loopback: Open WebUI on 3000, LiteLLM on 4000,
and PostgreSQL on 5432. Tailscale Serve publishes Open WebUI on HTTPS 443 and
LiteLLM on HTTPS 8443. The node also has `accept-dns=false`, advertises
`192.168.1.0/24`, and advertises itself as an exit node.

The effective old routing intent can be reproduced with modern partial-update
syntax as:

```bash
sudo tailscale set --accept-dns=false --accept-routes=false \
  --advertise-routes=192.168.1.0/24 --advertise-exit-node \
  --snat-subnet-routes=true --operator=hpt640
```

Auto-update was also enabled on that node. Defaults such as netfilter mode were
left enabled. The GX10 helper uses its actual `$USER` instead of copying the
legacy operator name.

Do not run that blindly on the GX10. Confirm the LAN CIDR with `ip route`, use
the repository's opt-in command, and approve the new route/exit node in the
Tailscale admin console. The old operator username is deliberately not copied.

The HPT640 UFW rules require an interactive sudo password and were not exported.
Record them before retirement:

```bash
sudo ufw status verbose
sudo ufw status numbered
sudo nft list ruleset
```

The new application ports remain on `127.0.0.1`, so they need no LAN-wide UFW
allow rules. Tailscale Serve is the ingress path. DNS port 53 rules belong only
on whichever machine eventually runs Pi-hole or AdGuard Home.

## 1. Prepare both repositories

Commit and push the reviewed Spark repo changes, then clone or fast-forward the
same commit on the GX10. On the GX10:

```bash
cd ~/code/spark-ai-stack
./bootstrap
```

For this migration path, do not run `bin/stack secrets-init` first. The import
refuses to overwrite an existing target secrets file.

## 2. Make a trial export on the HPT640

From this repository checkout on the HPT640:

```bash
cd ~/Documents/dgx-spark-ai-stack
bin/migrate-hpt640 export
```

The command briefly stops legacy Open WebUI and LiteLLM, dumps both databases,
copies Open WebUI's persistent directory and the legacy `.env`, writes hashes,
and restarts the two apps. It does not stop PostgreSQL, Pi-hole, Tailscale, or
delete anything. The resulting directory is mode-private and contains secrets.

Validate the trial bundle locally. This proves the exporter and lets you inspect
its size without committing to cutover:

```bash
cd ~/hpt640-ai-export/TIMESTAMP
sha256sum --check SHA256SUMS
```

Do not import this trial and then keep using both copies; that creates database
drift. Use a new final export for the one real import.

## 3. Freeze the old apps and transfer the final bundle

When ready for the maintenance window:

```bash
cd ~/Documents/dgx-spark-ai-stack
bin/migrate-hpt640 export ~/hpt640-ai-final --leave-stopped
```

PostgreSQL, Pi-hole, and Tailscale remain up, but legacy LiteLLM and Open WebUI
remain stopped so their databases cannot drift after the dumps. Copy the final
bundle over Tailscale or removable encrypted storage. Replace the example host
with the GX10's current MagicDNS name:

```bash
rsync -a --info=progress2 ~/hpt640-ai-final/ \
  GX10-MAGICDNS-NAME:~/migration/hpt640-ai/
```

Keep the bundle out of Git, cloud-sync folders, chat, and email.

If cutover must be abandoned, restart the legacy apps from
`~/infra/ai-gateway` with `docker compose start litellm open-webui`. Do not
delete either copy.

## 4. Import on the GX10

On a fresh target with no Spark secrets or application data:

```bash
cd ~/code/spark-ai-stack
bin/migrate-hpt640 import ~/migration/hpt640-ai
bin/stack apply
bin/modelctl fetch qwen3.6-35b
bin/modelctl activate qwen3.6-35b --default
bin/stack tailscale-configure
bin/doctor --full
```

The importer verifies every bundle hash, preserves the LiteLLM and Open WebUI
secrets, creates a new SearXNG secret, restores both databases, and copies UI
files. It refuses non-empty targets. The old data and bundle remain untouched.

Open WebUI stores some settings in PostgreSQL. After the first login, verify its
WebUI URL in the admin panel matches the GX10 MagicDNS HTTPS URL; persisted UI
settings can take precedence over environment variables.

## 5. Test before network cutover

From the GX10 and then a different tailnet device:

```bash
bin/stack status
curl -fsS http://127.0.0.1:3000/health
curl -i https://GX10-MAGICDNS-NAME/v1/models
curl -fsS -H 'Authorization: Bearer YOUR_KEY' \
  https://GX10-MAGICDNS-NAME/v1/models
```

The unauthenticated API request must return 401. Confirm users, chats, uploads,
cloud models, a local-model prompt, and a reboot before changing clients.

## 6. Optional subnet-router and exit-node replacement

If the HPT640 must be completely powered off and devices rely on its routes,
run this only after confirming the GX10 is wired, always on, and on the stated
LAN:

```bash
ip route
bin/stack tailscale-routing --lan-cidr 192.168.1.0/24 --exit-node
```

Then approve the GX10's advertised subnet and exit node in the Tailscale admin
console, select the GX10 as exit node on a test client, and test both internet
and LAN access. Route approval is control-plane state and cannot be migrated by
copying a command or Tailscale state directory.

Keep the HPT640 advertisement active until the GX10 passes. Afterwards disable
the old advertisement on the HPT640:

```bash
sudo tailscale set --advertise-routes= --advertise-exit-node=false
```

If the GX10 should stop routing later:

```bash
bin/stack tailscale-routing --disable
```

That disables advertisements but deliberately leaves kernel forwarding
settings in place because another local routing service might use them. Remove
`/etc/sysctl.d/99-spark-ai-tailscale-routing.conf` only after checking that
dependency explicitly.

## 7. Client cutover and retirement

Update Open WebUI bookmarks and API clients from the HPT640 URLs to the GX10
MagicDNS URL. Do not repoint router or tailnet DNS until the selected resolver
has passed the separate LAN and remote-client procedure in
[DNS_OPTIONS.md](DNS_OPTIONS.md).

Retain the HPT640, its `/srv/ai-gateway`, `.env`, and the final migration bundle
for an operator-chosen rollback period. Only then follow the old safe-cleanup
policy. Never use Docker volume pruning as a migration cleanup step.

## Primary references

- [Tailscale CLI (`set`, Serve, and preference inspection)](https://tailscale.com/docs/reference/tailscale-cli)
- [Tailscale subnet-router setup and route approval](https://tailscale.com/docs/features/subnet-routers/how-to/setup)
- [Open WebUI environment and PostgreSQL behavior](https://docs.openwebui.com/reference/env-configuration/)
