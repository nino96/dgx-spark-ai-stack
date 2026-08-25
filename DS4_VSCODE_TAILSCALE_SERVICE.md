# DS4 server from VS Code over Tailscale

This guide records the legacy direct-DS4 setup. New clients should use the
authenticated LiteLLM endpoint managed by this repository; `bin/modelctl`
switches DS4 behind that stable URL without exposing port 8081.

## Current recommended endpoint

```text
Endpoint: https://GX10-MAGICDNS-NAME/v1
Model: deepseek-v4-flash
API key: the LITELLM client key
```

Run `bin/modelctl activate deepseek-v4-flash --default` first. The remaining
sections are retained only for diagnosing and retiring the old direct service.

## Current setup

- systemd user unit: `~/.config/systemd/user/ds4-server.service`
- launcher: `~/.local/bin/ds4-serve`
- bind address: `0.0.0.0`
- port: `8081`
- Tailscale address at setup time: intentionally not retained in Git
- Legacy OpenAI-compatible base URL: `http://GX10-TAILSCALE-IP:8081/v1`
- model ID: `deepseek-v4-flash`
- server context allocation: `69632` tokens

The `0.0.0.0` bind makes the service reachable on all interfaces, including
Tailscale and the local network. Keep port `8081` inside the tailnet and do not
forward it from the router to the public internet. For a stricter setup, bind
to the current Tailscale IP instead, but remember that a Tailscale IP can change
if the device is removed and re-added to the tailnet.

## VS Code custom model

In GitHub Copilot's **Add Models > Custom Endpoint**, use:

```text
Endpoint: http://GX10-TAILSCALE-IP:8081/v1
Model: deepseek-v4-flash
API key: not-needed
```

The endpoint can be checked from the client machine with:

```bash
curl http://GX10-TAILSCALE-IP:8081/v1/models
```

Recommended initial custom-model limits:

```text
Context window:    69632
Max input tokens:  57344
Max output tokens: 8192
```

The server advertises `69632` as both its context and maximum completion limit,
but the input and output limits share that allocation. Leaving room for tool
schemas, conversation metadata, and reasoning is more reliable than setting
both client fields to the advertised maximum. Use `49152` input and `16384`
output only when longer generated answers are more important than long history.

## Background service commands

The service is enabled and starts automatically at boot. User-service linger is
enabled so it can start even before an interactive login:

```bash
systemctl --user status ds4-server.service
systemctl --user restart ds4-server.service
systemctl --user stop ds4-server.service
journalctl --user -u ds4-server.service -f
```

A successful startup log includes lines similar to:

```text
ds4-server: listening on http://0.0.0.0:8081
persistent batch ctx ready ... ctx=69632
```

The first startup after a restart can take time while the GGUF tensors are
loaded and CUDA is prewarmed. Wait for the `listening` line before testing the
endpoint.

### Stop it now and prevent automatic startup

To stop the server immediately and keep it from starting at login or boot:

```bash
systemctl --user disable --now ds4-server.service
```

This does not delete the unit. To start it manually later:

```bash
systemctl --user start ds4-server.service
```

To re-enable automatic startup:

```bash
systemctl --user enable --now ds4-server.service
```

If you also want to turn off user-service startup for this account entirely,
disable linger. This affects other user services too, so normally leave linger
enabled and disable only DS4:

```bash
loginctl disable-linger "$USER"
```

To prevent accidental starts while diagnosing something, mask the unit:

```bash
systemctl --user mask ds4-server.service
systemctl --user unmask ds4-server.service
```

## Checking memory before increasing context

The DGX Spark uses unified memory. On this GB10, the NVIDIA 580 driver reports
device memory totals as `Not Supported`, so `memory.used` and `memory.total`
appear as `N/A` in `nvidia-smi`. That is expected. Use `btop` and `free -h` to
watch total unified-memory pressure, and use the compute-process query below to
see the memory attributed to DS4.

Start a memory monitor in one terminal:

```bash
btop
```

In `btop`, watch the memory and swap meters while the server starts and while a
large request is running. Avoid letting swap grow: swap activity means the
system is under pressure and response latency can become extreme.

In a second terminal, watch system memory and swap:

```bash
watch -n 2 free -h
```

For GPU utilization and the DS4 process's NVIDIA-attributed memory, use:

```bash
watch -n 2 'nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader; nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader'
```

On this GB10, the second command can report a value such as `110729 MiB` for
DS4 even though the aggregate `memory.used` and `memory.total` fields are
`N/A`.

Also watch the service log:

```bash
journalctl --user -u ds4-server.service -f
```

Record these baselines before changing context:

```bash
free -h
nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
ps -C ds4-server -o pid,etime,%mem,rss,vsz,cmd
systemctl --user show ds4-server.service -p MemoryCurrent -p MemoryPeak
```

Do not judge safety from idle memory alone. KV memory is demand-driven, so a
server may start successfully and still run out of memory when several long
requests arrive at once.

### Why 128K can work on the same Spark

The project's measured KV cost is approximately `9.5 KiB/token`, not a full
copy of the model per context token. That is roughly `0.63 GiB` of KV at
`69632` tokens and `1.19 GiB` at `128K`, or about `0.55 GiB` of additional KV.
The resident model and CUDA runtime dominate memory use; the context setting
also controls how many sequences can be admitted and how much batching space
is reserved.

Therefore, a reported `128K` setup may mean a configured maximum that starts
successfully, not a continuously full 128K request with multiple concurrent
sequences. It may also use a different quantization, fewer background
processes, fewer concurrent sequences, or tolerate much smaller memory
headroom. On this machine, the last measured DS4 run attributed about
`110729 MiB`, while the system had only about `7.6 GiB` available and `1.0 GiB`
of swap in use. That is why `128K` should be tested as a workload limit rather
than assumed from the advertised model context.

The SearXNG container is not the limiting factor in the current setup: its
measured usage was about `1.7 MiB`. VS Code Remote processes were also small
compared with the model. The dominant allocation is DS4 itself.

## A measured context ramp

The current known-good value is `69632`. Increase in steps and test each step;
do not jump directly to the model's theoretical maximum.

1. Stop the service:

   ```bash
   systemctl --user stop ds4-server.service
   ```

2. Create a temporary systemd override:

   ```bash
   systemctl --user edit ds4-server.service
   ```

   Put this in the editor, replacing `98304` with the next value you want to
   test. The empty `ExecStart=` clears the original command before setting the
   replacement:

   ```ini
   [Service]
   ExecStart=
   ExecStart=%h/.local/bin/ds4-serve --host 0.0.0.0 --port 8081 -c 98304
   ```

3. Reload and start it:

   ```bash
   systemctl --user daemon-reload
   systemctl --user start ds4-server.service
   journalctl --user -u ds4-server.service -f
   ```

4. Confirm the requested context was allocated:

   ```bash
   curl http://127.0.0.1:8081/v1/models
   ```

   The response's `context_length` should match the tested value. The journal
   should also report `persistent batch ctx ready ... ctx=98304` and a listening
   line.

5. With `btop`, `nvidia-smi`, and the journal still visible, exercise the
   workload that matters: long Copilot history, tool use, multiple requests,
   or a large pasted file. Watch for CUDA allocation failures, an exited
   service, system OOM messages, swap growth, or severe latency.

Reasonable test points are `81920`, `98304`, and then `114688`. Keep the last
value that starts cleanly and survives a realistic workload. If a value fails,
return to the previous value immediately:

```bash
systemctl --user stop ds4-server.service
systemctl --user revert ds4-server.service
systemctl --user daemon-reload
systemctl --user start ds4-server.service
```

If `systemctl --user revert` is unavailable on the installed systemd version,
remove the override with:

```bash
systemctl --user edit ds4-server.service
```

Then delete the override contents, save, and run the same `daemon-reload` and
`start` commands.

A context increase is safe only if the server starts, reports the intended
context, and handles the target workload without exhausting memory. Leave
substantial headroom for the desktop, Docker containers, CUDA allocations, and
concurrent requests; there is no single `btop` percentage that guarantees a
crash-free ceiling.

## Recovery and diagnostics

If the service is repeatedly restarting:

```bash
systemctl --user status ds4-server.service
journalctl --user -u ds4-server.service -n 100 --no-pager
journalctl -k -n 100 --no-pager | grep -Ei 'oom|out of memory|killed process|nvrm|cuda'
```

Return to the known-good configuration by removing any context override and
using the unit's original `-c 69632` setting. The server's HTTP health check is:

```bash
curl http://127.0.0.1:8081/v1/models
```
