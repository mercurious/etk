#!/usr/bin/env bash
# ==========================================================
# local_node.sh -- the build node on this Mac (etk-local)
# ==========================================================
# A Lima VM shaped like etk-cloud (etk-local.lima.yaml), reached through the `etk-local` ssh alias:
# the forge drives it the way it drives the Oracle node (./forge.sh --local), and
# FORGE_HOST=etk-local tools/forge/provision_node.sh [phase ...] fills it.
#
# This tool only manages the VM. Filling it or building in it stays a mint (TRACK_MANUAL §1.1).
#
# Usage:  tools/forge/local_node.sh [status|up|down]      (default: status)
#   status   VM state, resources, disk used on this Mac
#   up       create or start the VM, write the ssh alias, wait for docker
#   down     stop the VM (a mint in flight dies; the disk and containers stay)
#
# Needs Lima: brew install lima
# ==========================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM="${ETK_LOCAL_VM:-etk-local}"
TEMPLATE="$HERE/etk-local.lima.yaml"
SSH_CONFIG="${ETK_LOCAL_SSH_CONFIG:-$HOME/.ssh/config}"

command -v limactl >/dev/null || { echo "limactl not found: brew install lima" >&2; exit 1; }

vm_state() { limactl list --format '{{.Status}}' "$VM" 2>/dev/null | head -1; }

write_alias() {
    grep -q "^Host $VM\$" "$SSH_CONFIG" 2>/dev/null && return 0
    local port
    port=$(sed -n 's/^ *localPort: *\([0-9]*\).*/\1/p' "$TEMPLATE" | head -1)
    cat >> "$SSH_CONFIG" <<EOF

# ETK local build node (tools/forge/local_node.sh): a Lima VM on this Mac, shaped like etk-cloud.
# Loopback only, and a recreated VM has new host keys, so nothing is pinned.
Host $VM
    HostName 127.0.0.1
    Port $port
    User ubuntu
    IdentityFile ~/.lima/_config/user
    IdentitiesOnly yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
EOF
    echo "ssh alias '$VM' added to $SSH_CONFIG"
}

case "${1:-status}" in
    up)
        case "$(vm_state)" in
            Running) echo "$VM already running" ;;
            "")      limactl start --tty=false --name="$VM" "$TEMPLATE" ;;
            *)       limactl start --tty=false "$VM" ;;
        esac
        write_alias
        ssh -o BatchMode=yes -o ConnectTimeout=15 "$VM" \
            'echo "$(hostname) · $(uname -m) · page $(getconf PAGESIZE) · $(nproc) cores · $(free -g | awk "/Mem/{print \$2}") GB · $(df -Ph ~ | awk "NR==2{print \$4}") free · docker $(docker version -f "{{.Server.Version}}")"'
        ;;
    down)
        limactl stop "$VM"
        ;;
    status)
        state=$(vm_state)
        [ -n "$state" ] || { echo "$VM: not created (tools/forge/local_node.sh up)"; exit 0; }
        limactl list "$VM"
        echo "on this Mac: $(du -sh "$HOME/.lima/$VM" 2>/dev/null | cut -f1)"
        ;;
    *)
        echo "usage: $0 [status|up|down]" >&2; exit 2
        ;;
esac
