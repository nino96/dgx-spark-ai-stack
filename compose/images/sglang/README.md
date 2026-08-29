# sglang image

No build here. `sglang` (compose/compose.yml, `profiles: [models]`) runs directly
from the upstream image `lmsysorg/sglang:spark`, pinned by digest in
`config/versions.lock.yaml` (`images.sglang`). This backend is experimental-tier
per docs/CONTRACTS.md §4/§7.

## Bumping the pin

1. Pull the current tag and resolve its digest:
   ```
   docker pull lmsysorg/sglang:spark
   docker inspect --format '{{index .RepoDigests 0}}' lmsysorg/sglang:spark
   ```
2. Update `images.sglang.digest` (and `tag:` if upstream moves off `spark`) in
   `config/versions.lock.yaml`.
3. If compose.yml pins the digest directly in its `image:` reference (it
   currently does not — see note below), update that too.
4. Run the sanity + acceptance suite (`bin/modelctl sanity <sglang-model>`,
   `bin/sparkctl acceptance`) against the new image before committing the bump.

## Current state

`config/versions.lock.yaml` records `digest: null` — this image has not been
pulled/verified from this repo's compose stack yet. `compose/compose.yml` pins
only the `spark` tag (no digest) for the same reason: a placeholder digest would
break `docker compose config` YAML/reference parsing. Pin the digest in both
places together once step 1 above has actually been run.
