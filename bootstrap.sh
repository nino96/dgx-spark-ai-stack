#!/usr/bin/env bash
# bootstrap.sh — idempotent host setup for spark-ai-stack (docs/CONTRACTS.md §1/§12 B).
#
# Safe to re-run at any time: every step below checks current state before
# acting. Must run as root (it re-execs itself via sudo if it isn't already).
#
# Usage:
#   sudo ./bootstrap.sh            # normal bootstrap / re-run
#   sudo ./bootstrap.sh --harden   # also drop niyam-gb10 from the docker group
set -euo pipefail

TARGET_USER="niyam-gb10"
TARGET_HOME="/home/${TARGET_USER}"
REPO_ROOT="${TARGET_HOME}/code/spark-ai-stack"
DATA_ROOT="${TARGET_HOME}/ai-data"
ETC_ROOT="/etc/spark-ai-stack"
VAR_LIB_ROOT="/var/lib/spark-ai-stack"
SPARK_AI_CTL_DEST="/usr/local/sbin/spark-ai-ctl"
SUDOERS_DEST="/etc/sudoers.d/spark-ai-stack"
TMPFILES_DEST="/etc/tmpfiles.d/spark-ai-stack.conf"

HARDEN=0
for arg in "$@"; do
    case "$arg" in
        --harden) HARDEN=1 ;;
        *)
            echo "bootstrap.sh: unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

# --- re-exec as root -------------------------------------------------------
if [[ "$(id -u)" -ne 0 ]]; then
    echo "bootstrap.sh: re-executing under sudo (root required)..."
    exec sudo -- "$0" "$@"
fi

log() { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

if [[ ! -d "$REPO_ROOT" ]]; then
    echo "bootstrap.sh: expected repo at $REPO_ROOT (this script is hardcoded to that path" >&2
    echo "per docs/CONTRACTS.md §1; it does not infer its own location)." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# (a) apt packages — install only what's missing; never reinstall/upgrade
#     something already present.
# ---------------------------------------------------------------------------

APT_UPDATED=0
apt_update_once() {
    if [[ "$APT_UPDATED" -eq 0 ]]; then
        log "apt-get update"
        apt-get update -qq
        APT_UPDATED=1
    fi
}

apt_install_if_missing() {
    local pkg="$1"
    if dpkg -s "$pkg" >/dev/null 2>&1; then
        log "package already installed: $pkg"
    else
        apt_update_once
        log "installing package: $pkg"
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$pkg"
    fi
}

log "checking Docker..."
if command -v docker >/dev/null 2>&1; then
    log "docker already present ($(docker --version 2>/dev/null || echo unknown version)); not reinstalling"
elif dpkg -s docker-ce >/dev/null 2>&1; then
    log "docker-ce already installed"
else
    warn "docker not found; installing docker.io from the distro repos."
    warn "For GPU workloads you likely want docker-ce + nvidia-container-toolkit instead —"
    warn "install those manually if docker.io is not what you want."
    apt_install_if_missing docker.io
fi

log "checking docker compose plugin..."
if docker compose version >/dev/null 2>&1; then
    log "docker compose plugin already present"
else
    apt_install_if_missing docker-compose-plugin
    if ! docker compose version >/dev/null 2>&1; then
        warn "docker compose plugin still not usable after install; check manually."
    fi
fi

log "checking nvidia-container-toolkit..."
if dpkg -s nvidia-container-toolkit >/dev/null 2>&1; then
    log "nvidia-container-toolkit already installed"
else
    warn "nvidia-container-toolkit is not installed. GPU-backed containers (vllm/llamacpp/sglang)"
    warn "will not work until it is. Not installing it automatically — install manually:"
    warn "  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
fi

apt_install_if_missing python3-yaml
apt_install_if_missing curl
apt_install_if_missing jq
apt_install_if_missing git
apt_install_if_missing openssl

if ! python3 -c "import yaml" >/dev/null 2>&1; then
    warn "python3 still cannot import yaml after installing python3-yaml; spark-ai-ctl will fail."
fi

# ---------------------------------------------------------------------------
# (b) platform verification — warn, do not abort (this host is expected to be
#     the ASUS Ascent GX10 per docs/CONTRACTS.md, but bootstrap should still
#     leave a usable partial install if run somewhere that doesn't match).
# ---------------------------------------------------------------------------

log "verifying platform expectations..."
if [[ "$(uname -m)" != "aarch64" ]]; then
    warn "uname -m is $(uname -m), not aarch64. This repo targets GB10 (aarch64)."
fi
if ! command -v nvidia-smi >/dev/null 2>&1; then
    warn "nvidia-smi not found. GPU backends will not run without the NVIDIA driver."
fi
if ! command -v tailscale >/dev/null 2>&1; then
    warn "tailscale is not installed. Tailnet exposure (docs/CONTRACTS.md §8) will not be available."
else
    if tailscale status >/dev/null 2>&1; then
        log "tailscale is installed and appears logged in"
    else
        warn "tailscale is installed but 'tailscale status' failed — is it logged in? Run 'tailscale up'."
    fi
fi

# ---------------------------------------------------------------------------
# (c) directories
# ---------------------------------------------------------------------------

log "creating root-owned directories..."
install -d -o root -g root -m 0755 "$ETC_ROOT"
install -d -o root -g root -m 0755 "$ETC_ROOT/compose"
install -d -o root -g root -m 0755 "$ETC_ROOT/env.d"
install -d -o root -g root -m 0755 "$VAR_LIB_ROOT"

log "creating user-owned data directories under $DATA_ROOT..."
for sub in \
    "state/generated" \
    "huggingface" \
    "models/gguf" \
    "kb" \
    "state/evals"
do
    install -d -o "$TARGET_USER" -g "$TARGET_USER" -m 0755 "${DATA_ROOT}/${sub}"
done

# ---------------------------------------------------------------------------
# (d) install spark-ai-ctl, sudoers drop-in, tmpfiles.d config
# ---------------------------------------------------------------------------

log "installing spark-ai-ctl -> $SPARK_AI_CTL_DEST"
install -o root -g root -m 0755 "${REPO_ROOT}/security/spark-ai-ctl" "$SPARK_AI_CTL_DEST"

log "validating and installing sudoers drop-in -> $SUDOERS_DEST"
SUDOERS_SRC="${REPO_ROOT}/security/sudoers-spark-ai-stack"
if command -v visudo >/dev/null 2>&1; then
    if ! visudo -cf "$SUDOERS_SRC"; then
        echo "bootstrap.sh: refusing to install an invalid sudoers file ($SUDOERS_SRC)" >&2
        exit 1
    fi
else
    warn "visudo not found; skipping syntax check of $SUDOERS_SRC before install"
fi
install -o root -g root -m 0440 "$SUDOERS_SRC" "$SUDOERS_DEST"

log "installing tmpfiles.d config -> $TMPFILES_DEST"
install -o root -g root -m 0644 "${REPO_ROOT}/security/tmpfiles-spark-ai-stack.conf" "$TMPFILES_DEST"
systemd-tmpfiles --create "$TMPFILES_DEST"

# ---------------------------------------------------------------------------
# (e) install systemd units, enable (do not start)
# ---------------------------------------------------------------------------

log "installing systemd units..."
install -o root -g root -m 0644 "${REPO_ROOT}/systemd/spark-ai-core.service" \
    /etc/systemd/system/spark-ai-core.service
install -o root -g root -m 0644 "${REPO_ROOT}/systemd/spark-ai-boot-model.service" \
    /etc/systemd/system/spark-ai-boot-model.service

systemctl daemon-reload
systemctl enable spark-ai-core.service spark-ai-boot-model.service
log "systemd units installed and enabled (not started — start them explicitly, see NEXT STEPS)"

# ---------------------------------------------------------------------------
# (f) secrets + initial sync
# ---------------------------------------------------------------------------

log "running spark-ai-ctl secrets-init..."
"$SPARK_AI_CTL_DEST" secrets-init

log "running spark-ai-ctl sync --yes (initial population of /etc/spark-ai-stack)..."
"$SPARK_AI_CTL_DEST" sync --yes

# ---------------------------------------------------------------------------
# (g) --harden: drop niyam-gb10 from the docker group
# ---------------------------------------------------------------------------

if [[ "$HARDEN" -eq 1 ]]; then
    log "applying --harden: removing ${TARGET_USER} from the docker group"
    if id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
        gpasswd -d "$TARGET_USER" docker
        warn "${TARGET_USER} removed from the docker group. This does not take effect for"
        warn "already-open sessions — log out and back in (or reboot) before relying on it."
        warn "After this, ad-hoc 'docker ...' commands as ${TARGET_USER} require sudo; the"
        warn "supported path for stack operations remains 'sudo spark-ai-ctl <verb>' / bin/sparkctl."
    else
        log "${TARGET_USER} is not in the docker group; nothing to do"
    fi
fi

# ---------------------------------------------------------------------------
# NEXT STEPS
# ---------------------------------------------------------------------------

cat <<EOF

============================================================
 spark-ai-stack bootstrap complete
============================================================

Review what 'sync' just applied to /etc/spark-ai-stack (it already ran once
above with --yes for initial population; re-run interactively to review
future changes):
    sudo spark-ai-ctl sync

Bring the core stack up:
    sudo spark-ai-ctl core-up
    sudo spark-ai-ctl status

Start the boot model + boot-model systemd path once you're ready:
    sudo systemctl start spark-ai-core.service
    sudo systemctl start spark-ai-boot-model.service

Check secrets are populated (values are never printed):
    sudo spark-ai-ctl secrets-status

EOF

if [[ "$HARDEN" -ne 1 ]]; then
    cat <<EOF
Docker-group hardening has NOT been applied yet. ${TARGET_USER} can currently
run 'docker' directly (unrestricted host access). Once you have verified the
NOPASSWD spark-ai-ctl path works end-to-end, run:
    sudo ./bootstrap.sh --harden
and log out/in (or reboot) afterward.

EOF
else
    cat <<EOF
Docker-group hardening (--harden) was applied this run. Log out and back in
(or reboot) for the group change to take effect in your shell.

EOF
fi

cat <<EOF
See docs/SECURITY.md for the full threat model and verification commands.
============================================================
EOF
