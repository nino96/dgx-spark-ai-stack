# Backups, upgrades, and rollback

## Routine application update

The one-command path is:

```bash
cd ~/code/spark-ai-stack
bin/update
```

It refuses a dirty worktree, fast-forwards from the configured Git upstream,
runs repository tests, creates a private application backup, pulls only the
immutable image digests in the new checkout, rebuilds the locked vLLM layer,
reruns Ansible, and runs diagnostics. It never upgrades BIOS, firmware, DGX OS,
the kernel, the NVIDIA driver, or CUDA.

After a material update, reboot once and run:

```bash
bin/doctor --full
```

No floating application tags are deployed. Seeing a newer upstream release
does not change the machine; a reviewed commit must update the tag and ARM64
digest in both `compose/compose.yml` and `config/versions.lock.yaml`.

If an optional Pi-hole or AdGuard Home profile is active, the same workflow
creates a separate consistent DNS backup and recreates that resolver at the
checked-in digest. An inactive or uninitialized DNS profile is left alone.

## Backup

Create a backup at any time with:

```bash
bin/stack backup
```

The command briefly stops Open WebUI, makes logical custom-format dumps of both
PostgreSQL databases, copies the Open WebUI data directory, saves the stable
secrets and generated LiteLLM routes, records deployed image IDs, hashes every
file, and restarts Open WebUI. Output defaults to `~/ai-data/backups/TIMESTAMP`.

The backup contains passwords and provider keys. Its local permissions are
restricted, but a backup on the same SSD is not disaster recovery. Copy it to
encrypted external storage and periodically test a restore on a disposable
target.

## Version-promotion checklist

Upgrade one class at a time:

1. Resolve the exact Linux ARM64 manifest digest and multi-architecture index
   digest; never replace a digest with `latest`, `main`, or a floating major.
2. Update the lockfile and Compose reference together.
3. Run `tests/static.sh` and review `git diff`.
4. Run `bin/stack upgrade` on the GX10. This applies the current checkout
   without doing a Git pull and is useful while testing a branch.
5. Exercise UI login, migrated chats/uploads, cloud routes, local sync and
   streaming chat, tools, cancellation, and Tailscale access.
6. Reboot and run `bin/doctor --full`; retain the previous images and backup.

For PostgreSQL patch releases within major 16, keep the same data directory and
still take a logical backup first. A PostgreSQL major-version change is a data
migration, not an ordinary image bump: create a new empty data directory and
restore logical dumps using the new major. Never point a new major directly at
the old major's files.

Open WebUI can run schema migrations at startup. Preserve `WEBUI_SECRET_KEY`:
changing it logs users out and can make encrypted values unreadable. Verify the
database dump and `/app/backend/data` backup before every Open WebUI bump.

For OpenRouter, provider releases and prices are not Docker inputs. Before a
portfolio change, run `bin/cloud-models check`, update
`config/cloud-models.yaml`, and test all four stable aliases through LiteLLM.
See [CLOUD_MODELS.md](CLOUD_MODELS.md).

For optional DNS image promotion, update `compose/dns.yml` and both platform
and release-index digests in `config/versions.lock.yaml`. Then validate the
selected resolver with LAN and tailnet UDP/TCP queries before any router or
tailnet DNS change. See [DNS_OPTIONS.md](DNS_OPTIONS.md).

## Failed upgrade

`~/ai-data/state/pending-upgrade.txt` records the previous and candidate Git
commits. The pre-upgrade backup path is printed before services change. Do not
prune images or delete data while investigating.

For an application-only rollback, check out the recorded previous commit in a
separate review step, rerun `bin/stack upgrade`, and test. If the newer app ran
an incompatible database migration, stop the core stack and restore the
pre-upgrade logical dumps and Open WebUI files into empty replacement data
directories. Keep the failed data directory by renaming it; do not overwrite or
delete it in place.

The repository intentionally does not automate destructive restore over live
data. Recovery should require the operator to identify the exact backup and
empty target explicitly.

## Primary references

- [Open WebUI updating guidance](https://docs.openwebui.com/getting-started/updating/)
- [Open WebUI backup guidance](https://docs.openwebui.com/tutorials/maintenance/backups/)
- [Docker Official PostgreSQL image and major-version data layout](https://hub.docker.com/_/postgres)
