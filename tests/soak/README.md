# Qwen plus llama.cpp concurrency gate

This combination ships as `pending`. Do not edit the allowlist until this test
runs on the target GX10/Spark.

1. Record `free -b`, swap used, service status, and the current commit.
2. Activate Qwen, temporarily mark the exact pair `passed` in a worktree, then
   activate llama.cpp so both are admitted.
3. Run `tests/soak/run.sh` for at least 30 minutes with representative long and
   short prompts from two clients.
4. Watch `MemAvailable`, swap, the kernel journal, both backend logs, and
   cancellation behavior.
5. Fail on any service restart, OOM/CUDA/NVRM error, swap growth, or a drop
   below the 8 GiB reserve.
6. Save the timestamped report and workload description in the promotion PR.
   Only then commit the allowlist status as `passed`.

Revert the temporary policy edit immediately if the gate fails. `modelctl`
will otherwise treat the profiles exclusively.
