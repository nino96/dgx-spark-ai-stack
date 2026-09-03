# Upgrades — pin/bump procedures

Governing rule for every procedure below: **one component per commit**. Edit
one pin, rebuild/pull just that thing, run the sanity + acceptance gate,
*then* commit — never bundle two independent bumps into one commit, so a
regression bisects to a single change. Rollback for any of these is always
`git revert <commit>` followed by re-`sync`ing and restarting the affected
service; `config/versions.lock.yaml` and the compose files are the only
sources of truth, so reverting them and re-applying is sufficient.

All pins live in `config/versions.lock.yaml`. See `docs/COMPATIBILITY.md` for
*why* several of these pins exist before bumping past them.

## Generic image bump (litellm / openwebui / postgres / searxng)

```bash
# 1. edit the pin
$EDITOR config/versions.lock.yaml     # bump tag:, clear or update digest:
$EDITOR compose/compose.yml           # match the image: line for that service

# 2. pull/build
sudo docker pull <repository>:<new-tag>
docker inspect --format '{{index .RepoDigests 0}}' <repository>:<new-tag>
$EDITOR config/versions.lock.yaml     # fill in the resolved digest

# 3. land the change and restart just that service
sudo spark-ai-ctl sync
sudo spark-ai-ctl core-restart        # or: bin/sparkctl core restart

# 4. gate
bin/sparkctl acceptance
bin/modelctl sanity <boot-model>

# 5. commit (one component)
git add config/versions.lock.yaml compose/compose.yml
git commit -m "bump <service> to <new-tag>"
```

Rollback: `git revert <commit>`, `sudo spark-ai-ctl sync`,
`sudo spark-ai-ctl core-restart`.

## vLLM: NGC tag bump

The vLLM image is built locally from `compose/images/vllm/Dockerfile`
(`FROM nvcr.io/nvidia/vllm:<tag>`) plus a compatibility layer
(`apache-tvm-ffi==0.1.9`, `xgrammar==0.2.1`) — see `docs/COMPATIBILITY.md`
for why that layer exists. **Do not drop it without re-verifying the
xgrammar/tvm-ffi requirement against the new NGC image's shipped versions.**

```bash
# 1. bump the base tag
$EDITOR compose/images/vllm/Dockerfile          # ARG BASE_IMAGE=nvcr.io/nvidia/vllm:<new-tag>
$EDITOR config/versions.lock.yaml               # images.vllm_base.tag / .digest

# 2. build and verify the compatibility layer still applies cleanly
cd compose/images/vllm
docker build -t spark-ai/vllm:<new-tag>-xgNNN .
docker run --rm --gpus all spark-ai/vllm:<new-tag>-xgNNN \
  python3 -c "from xgrammar import StructuralTag, normalize_tool_choice; print('OK')"
# if the new NGC image already ships xgrammar >= 0.2.1 natively, this is the
# point to consider DROPPING the pip-install layer entirely -- see
# docs/COMPATIBILITY.md's removal gate before doing so.

# 3. FastAPI regression watch (docs/COMPATIBILITY.md) -- if this NGC lineage
#    still needs the FastAPI 0.136.3 pin, keep it; if testing the fix
#    (vLLM PR #45629), run WITHOUT the pin and check for HTTP 500 on every route.
$EDITOR compose/compose.yml                     # bump image: spark-ai/vllm:<new-tag>

# 4. land + restart the vllm service, then gate hard (this is the primary boot backend)
sudo spark-ai-ctl sync
bin/modelctl activate qwen3.6-35b               # re-activates on the new image; sanity gate runs
bin/modelctl sanity qwen3.6-35b                 # full probe set, incl. tool_call (the exact probe
                                                 # that would catch a tool_choice=required regression)
bin/sparkctl acceptance

# 5. commit
git add compose/images/vllm/Dockerfile compose/compose.yml config/versions.lock.yaml
git commit -m "bump vllm to <new-tag>"
```

Rollback: `git revert`, rebuild the old Dockerfile tag (or it's still a local
image if you didn't `docker rmi` it), `sudo spark-ai-ctl sync`,
`bin/modelctl activate qwen3.6-35b`.

## llama.cpp commit bump

```bash
# 1. bump the pinned commit
$EDITOR config/versions.lock.yaml               # sources.llama_cpp.ref / .commit
$EDITOR compose/images/llamacpp/Dockerfile       # ARG LLAMACPP_COMMIT=<new-sha>
$EDITOR compose/compose.yml                      # image: spark-ai/llamacpp:<new-short-sha>
                                                  # (also bump the `embeddings` service's image: --
                                                  # it reuses the same built image)

# 2. build
cd compose/images/llamacpp
docker build -t spark-ai/llamacpp:<new-short-sha> .
# watch for a cmake warning about GGML_CUDA_F16 -- it no longer exists as a
# cmake option at recent llama.cpp commits and is silently ignored (see
# docs/COMPATIBILITY.md); this is expected, not a build failure.

# 3. land + gate
sudo spark-ai-ctl sync
bin/modelctl activate deepseek-v4-flash         # or whichever llamacpp model you have fetched
bin/modelctl sanity deepseek-v4-flash
bin/sparkctl acceptance

# 4. commit
git add config/versions.lock.yaml compose/images/llamacpp/Dockerfile compose/compose.yml
git commit -m "bump llama.cpp to <new-short-sha>"
```

Rollback: `git revert`, rebuild the previous commit's image (or reuse the
still-tagged local image), `sudo spark-ai-ctl sync`, re-activate.

## sglang `:spark` digest bump

sglang is experimental-tier, pulled unbuilt from
`lmsysorg/sglang:spark` (see `compose/images/sglang/README.md`).

```bash
# 1. resolve the current digest for the moving `spark` tag
docker pull lmsysorg/sglang:spark
docker inspect --format '{{index .RepoDigests 0}}' lmsysorg/sglang:spark

# 2. record it
$EDITOR config/versions.lock.yaml      # images.sglang.digest (and .tag if upstream renames it)
# compose/compose.yml currently pins only the tag (no digest) -- update it too if/when it's
# changed to pin by digest directly (see the README's "Current state" note).

# 3. gate -- sglang has no fetched model in config/models.yaml yet; if/when one exists:
sudo spark-ai-ctl sync
bin/modelctl activate <sglang-model> --experimental   # if still experimental-tier
bin/modelctl sanity <sglang-model>
bin/sparkctl acceptance

# 4. commit
git add config/versions.lock.yaml compose/compose.yml
git commit -m "bump sglang:spark digest"
```

## Model revision bumps

```bash
# 1. fetch the new revision (do NOT overwrite the old one first -- fetch, verify, then flip the pin)
bin/modelctl fetch <model>                # if source.revision is null, this resolves + prints it
bin/modelctl verify <model>

# 2. pin it
$EDITOR config/models.yaml                # source.revision: <resolved-sha>

# 3. land + gate
sudo spark-ai-ctl sync
bin/modelctl activate <model>             # sanity gate runs automatically
bin/modelctl sanity <model>               # full probe set
bin/modelctl eval <model> --tasks arc_easy --limit 50   # optional quality regression check
python3 evals/lm-eval/compare.py <model>                # vs. the previous revision's archived run

# 4. commit
git add config/models.yaml
git commit -m "bump <model> to revision <short-sha>"
```

For a local-GGUF model (`llamacpp`), the equivalent is a new `filename` +
`sha256` (and drafter, if any) in `config/models.yaml`'s `source:` block —
`modelctl fetch` downloads and verifies the new file by its declared sha256
before you flip the catalog pin over to it.

## lm-eval version bump

```bash
$EDITOR evals/lm-eval/Dockerfile          # bump the pinned lm_eval[api] version
cd evals/lm-eval && docker build -t spark-ai/lm-eval:local -f Dockerfile .
# smoke test against an already-active model:
evals/lm-eval/run.sh <served_model_name> <slot-port> --tasks arc_easy --limit 10
git add evals/lm-eval/Dockerfile
git commit -m "bump lm_eval to <version>"
```

No sanity/acceptance gate needed here — lm-eval is a benchmarking tool
outside the serving path, not something requests flow through.

## Sanity + acceptance gate, spelled out

Every procedure above bottoms out in the same two checks; run both before
committing any bump that touches a serving path (images, llama.cpp, vLLM,
sglang, or a model revision — not needed for the lm-eval version bump):

```bash
bin/modelctl sanity <model>     # full sanity probe set against the just-changed model
bin/sparkctl acceptance         # unified endpoint + SearXNG checks end-to-end through litellm
```

If either fails, the bump does not get committed — fix or revert first.
