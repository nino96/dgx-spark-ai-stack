# evals/lm-eval/Dockerfile
#
# Containerized lm-evaluation-harness for on-demand regression benchmarking of locally served
# models. Built and run manually (see run.sh) -- this is NOT part of the always-on compose stack.
#
# python:3.12-slim publishes multi-arch manifests (amd64 + arm64), so this builds fine on the
# GX10's aarch64 host without any qemu/emulation tricks.
FROM python:3.12-slim

# Pin exactly -- lm-eval's task registry and CLI flags shift between minor versions, and we want
# reproducible regression-tracking runs (see docs/CONTRACTS.md §11). Latest known-stable at the
# time this was pinned: 0.4.12 (PyPI, released 2026-05-11). Bump deliberately, not opportunistically.
ARG LM_EVAL_VERSION=0.4.12

RUN pip install --no-cache-dir "lm_eval[api]==${LM_EVAL_VERSION}"

# lm_eval writes results under whatever --output_path is passed; run.sh mounts a scratch dir at
# /out for that.
WORKDIR /work

ENTRYPOINT ["lm_eval"]
