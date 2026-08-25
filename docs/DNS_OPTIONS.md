# Optional Pi-hole or AdGuard Home

## Decide before enabling DNS

DNS is not part of the core AI stack. This repository can run either Pi-hole
or AdGuard Home on the GX10, but never both at once because both need port 53.
Neither service is started by `bin/stack apply` or at boot unless you explicitly
start it with `bin/dnsctl`.

Running DNS on the GX10 is convenient and makes the HPT640 removable, but every
GX10 reboot or maintenance window also becomes a household DNS outage. A small
dedicated host is the better availability boundary if DNS must remain up while
the AI system is serviced.

Pi-hole is the easier migration target if you want to restore an existing
Pi-hole Teleporter export. AdGuard Home offers an integrated first-run setup
and a different filtering UI. Both use pinned ARM64 image digests and retain
their data under `~/ai-data/dns`.

DHCP is intentionally not included. Leave DHCP on the router. If the HPT640 is
currently a DHCP server, migrate DHCP first and never run two DHCP servers on
the same LAN.

## Prepare the GX10

On the GX10, initialize a private configuration file from its current network
addresses:

```bash
cd ~/code/spark-ai-stack
bin/dnsctl init
${EDITOR:-nano} ~/.config/spark-ai-stack/dns.env
bin/dnsctl preflight
```

`init` detects the default-route LAN address, Tailscale IPv4 address, LAN CIDR,
and timezone, and creates a random Pi-hole password. It writes mode `0600` and
does not print the secret. Reserve `DNS_LAN_IP` for the GX10 in the router so it
does not change.

The Compose file publishes DNS TCP/UDP only on the two configured addresses.
It does not claim `0.0.0.0:53` or Ubuntu's `127.0.0.53`, so do not disable or
rewrite `systemd-resolved`. The preflight refuses missing addresses and common
port-53 conflicts.

Docker-published ports are not reliably governed by ordinary UFW input rules.
The exact-address bindings limit exposure to the LAN and tailnet interfaces;
also use Tailscale grants/ACLs if only selected tailnet devices should query
DNS. The web consoles are never published on either private interface.

## Option A: Pi-hole

Start it explicitly:

```bash
bin/dnsctl up pihole
bin/dnsctl status
```

Reach the loopback-only console with an SSH tunnel from your workstation:

```bash
ssh -L 8088:127.0.0.1:8088 YOUR_USER@GX10-MAGICDNS-NAME
```

Open `http://127.0.0.1:8088/admin/`. The password is the
`PIHOLE_PASSWORD` value in the private DNS environment file. For a migration,
use Pi-hole's Teleporter export on the HPT640 and import it through this UI.
A fresh configuration is safer if you do not need old lists and local records.

## Option B: AdGuard Home

Start it explicitly:

```bash
bin/dnsctl up adguard
bin/dnsctl status
```

For first-run setup, tunnel the loopback-only wizard:

```bash
ssh -L 8090:127.0.0.1:8090 YOUR_USER@GX10-MAGICDNS-NAME
```

Open `http://127.0.0.1:8090`. In the wizard, keep DNS listening on port 53 and
the web UI on port 80 inside the container. After setup, use the normal console
through a separate tunnel:

```bash
ssh -L 8089:127.0.0.1:8089 YOUR_USER@GX10-MAGICDNS-NAME
```

Then open `http://127.0.0.1:8089/`.

## Test before client cutover

Keep the router and Tailscale admin DNS settings unchanged while testing. From
a LAN client and then a permitted tailnet client:

```bash
dig @GX10-LAN-IP example.com
dig @GX10-TAILSCALE-IP example.com
```

Also test filtered and intentionally allowed domains, local names if used, and
several hours of normal browsing. Keep `accept-dns=false` on the DNS server so
it does not recursively configure itself through tailnet DNS.

Only after those tests pass:

1. Point the router's DHCP-provided DNS address at the reserved GX10 LAN IP.
2. If desired, set the GX10 Tailscale IP as the tailnet nameserver and scope it
   with split DNS or Tailscale grants as appropriate.
3. Renew a test client's DHCP lease and verify both resolution and filtering.
4. Keep the HPT640 resolver available during the rollback window.

If clients fail, restore the old/upstream resolver in the router and Tailscale
admin console before stopping the new service.

## Operations, backup, and switching

```bash
bin/dnsctl status
bin/dnsctl logs pihole        # or: adguard
bin/dnsctl backup
bin/dnsctl update-active
bin/dnsctl down
```

`backup` briefly stops the active resolver for a consistent archive, then
restarts it. Archives and hashes are written under `~/ai-data/backups/dns` and
contain secrets; copy them to encrypted off-host storage. `bin/update` also
backs up and recreates the active optional resolver at the repository's pinned
image. If no resolver is initialized or active, it is a no-op.

To switch products, first restore an upstream DNS path for clients, then:

```bash
bin/dnsctl down
bin/dnsctl up adguard          # or: pihole
```

Both data directories are retained. Do not treat `down` as a client rollback;
change client/router DNS first so the household does not lose resolution.

## Official references

- [Pi-hole official Docker image and configuration](https://github.com/pi-hole/docker-pi-hole)
- [AdGuard Home official Docker guide](https://github.com/AdguardTeam/AdGuardHome/wiki/Docker)
- [AdGuard Home systemd-resolved guidance](https://github.com/AdguardTeam/AdGuardHome/wiki/FAQ)

