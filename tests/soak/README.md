# qwen3.6-35b plus deepseek-v4-flash concurrency gate

Both models declare `role: text`, so `config/models.yaml`'s `tested_pairs:` allowlist
(docs/CONTRACTS.md §7) is what modelctl's concurrency check consults -- this pair is
absent from it (`tested_pairs: []`) until this soak test passes on the target GX10/Spark.
Do not edit `tested_pairs:` until then.

1. Record `free -b`, swap used, service status, and the current commit.
2. `modelctl activate qwen3.6-35b`, then temporarily add
   `{models: [qwen3.6-35b, deepseek-v4-flash], status: passed}` to `tested_pairs:` in a
   worktree, then `modelctl activate deepseek-v4-flash --with qwen3.6-35b` so both are
   admitted.
3. Run `tests/soak/run.sh` for at least 30 minutes with representative long and
   short prompts from two clients (ports 8001/8002 per docs/CONTRACTS.md §3).
4. Watch `MemAvailable`, swap, the kernel journal, both backend logs, and
   cancellation behavior.
5. Fail on any service restart, OOM/CUDA/NVRM error, swap growth, or a drop
   below the `host_reserve_gib` reserve (10 GiB by default, see `config/models.yaml`).
6. Save the timestamped report and workload description in the promotion PR.
   Only then commit the `tested_pairs:` entry with `status: passed`.

Revert the temporary `tested_pairs:` edit immediately if the gate fails. `modelctl`
will otherwise treat the pair exclusively.
