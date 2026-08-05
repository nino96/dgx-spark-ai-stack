#!/usr/bin/env bash
set -euo pipefail

duration="${SOAK_SECONDS:-1800}"
interval="${SOAK_INTERVAL_SECONDS:-10}"
reserve_kib=$((8 * 1024 * 1024))
output="${SOAK_REPORT:-soak-$(date +%Y%m%dT%H%M%S).tsv}"
start="$(date +%s)"
end=$((start + duration))
initial_swap="$(awk '/SwapTotal/ {total=$2} /SwapFree/ {free=$2} END {print total-free}' /proc/meminfo)"

printf 'timestamp\tmem_available_kib\tswap_used_kib\tqwen_http\tllama_http\n' >"$output"
while (( $(date +%s) < end )); do
    available="$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
    swap_used="$(awk '/SwapTotal/ {total=$2} /SwapFree/ {free=$2} END {print total-free}' /proc/meminfo)"
    qwen="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8001/v1/models || true)"
    llama="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8002/v1/models || true)"
    printf '%s\t%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$available" "$swap_used" "$qwen" "$llama" | tee -a "$output"
    (( available >= reserve_kib )) || { echo "FAIL: memory reserve crossed" >&2; exit 1; }
    (( swap_used <= initial_swap )) || { echo "FAIL: swap grew" >&2; exit 1; }
    [[ "$qwen" == 200 && "$llama" == 200 ]] || { echo "FAIL: backend unhealthy" >&2; exit 1; }
    sleep "$interval"
done

if journalctl -k --since "@$start" --no-pager | rg -i 'oom|out of memory|killed process|nvrm|cuda.*error'; then
    echo "FAIL: kernel/GPU error detected" >&2
    exit 1
fi
echo "Soak telemetry gate passed: $output"
