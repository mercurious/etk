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
# TWO SHAPES, ONE ALIAS (2026-10-31). The M1 Max dual-boots:
#   macOS   -> the Lima VM above (vmType vz), user ubuntu, port 60022.
#   Linux   -> (Asahi Fedora) there is no Virtualization.framework and no docker, only rootless
#              podman -- so the node IS this host: an `etk-local` ssh alias to 127.0.0.1 as the
#              current user, this host's own key in its authorized_keys, a `docker` shim in
#              ~/.local/bin that execs podman (the forge, the lanes and the container recipes
#              speak docker verbs only), and `loginctl enable-linger` so containers outlive the
#              ssh session. The kernel container then lives in this user's podman storage, with
#              ~/rocknix-gtk and ~/etk where the lanes expect them ($HOME). Native 10 cores /
#              62 GB, btrfs (case-sensitive), SELinux enforcing (the container recipe labels its
#              bind mount :z). Fill it with rocknix-gtk scripts/provision-build-container.sh
#              (local, no ETK_BUILD_HOST) and stage_72.sh; build with ./forge.sh --local.
#
# Usage:  tools/forge/local_node.sh [status|up|down]      (default: status)
#   status   VM state, resources, disk used (macOS) · podman, shim, alias, container, compilers (Linux)
#   up       create or start the VM, write the ssh alias, wait for docker (macOS)
#            · key + alias + shim + linger, start the kernel container if it exists (Linux)
#   down     stop the VM (macOS) · stop the kernel container (Linux); a mint in flight dies
#
# Needs Lima on macOS: brew install lima. Needs podman + sshd on Linux.
# ==========================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM="${ETK_LOCAL_VM:-etk-local}"
TEMPLATE="$HERE/etk-local.lima.yaml"
SSH_CONFIG="${ETK_LOCAL_SSH_CONFIG:-$HOME/.ssh/config}"

# ---------------------------------------------------------------- Linux: the host is the node
if [ "$(uname -s)" = Linux ]; then
    CONTAINER="${ETK_LOCAL_KERNEL_CONTAINER:-rocknix-gtk-kernel-sid}"
    KEY="${ETK_LOCAL_KEY:-$HOME/.ssh/id_ed25519}"
    KNOWN="$HOME/.ssh/known_hosts.$VM"
    SHIM="$HOME/.local/bin/docker"
    say() { printf '[local_node] %s\n' "$*"; }
    die() { printf '[local_node] FATAL: %s\n' "$*" >&2; exit 1; }
    have_alias() { grep -q "^Host $VM\$" "$SSH_CONFIG" 2>/dev/null; }
    linux_alias() {
        have_alias && return 0
        [ -f "$KEY" ] || die "no ssh key at $KEY (ETK_LOCAL_KEY=... or ssh-keygen -t ed25519)"
        cat >> "$SSH_CONFIG" <<EOF

# ETK local build node (tools/forge/local_node.sh, Linux shape): this host, reached as the forge
# reaches every node. Loopback only; its host key is pinned in its own known_hosts file.
Host $VM
    HostName 127.0.0.1
    Port 22
    User $USER
    IdentityFile $KEY
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    UserKnownHostsFile $KNOWN
    LogLevel ERROR
EOF
        say "ssh alias '$VM' -> 127.0.0.1 as $USER added to $SSH_CONFIG"
    }
    linux_key() {
        [ -f "$KEY.pub" ] || die "no public key at $KEY.pub"
        touch "$HOME/.ssh/authorized_keys"; chmod 600 "$HOME/.ssh/authorized_keys"
        if ! grep -qF "$(awk '{print $2}' "$KEY.pub")" "$HOME/.ssh/authorized_keys"; then
            cat "$KEY.pub" >> "$HOME/.ssh/authorized_keys"
            say "this host's key ($(basename "$KEY").pub) added to its own authorized_keys"
        fi
    }
    linux_shim() {
        if command -v docker >/dev/null 2>&1; then say "docker: $(command -v docker)"; return 0; fi
        command -v podman >/dev/null || die "neither docker nor podman on this host"
        mkdir -p "$(dirname "$SHIM")"
        printf '#!/bin/sh\n# etk-local (tools/forge/local_node.sh): the forge speaks docker verbs; this host has podman.\nexec podman "$@"\n' > "$SHIM"
        chmod +x "$SHIM"
        say "docker shim written: $SHIM -> podman $(podman --version | awk '{print $NF}')"
    }
    container_state() { podman ps -a --filter "name=^$CONTAINER\$" --format '{{.Status}}' 2>/dev/null | head -1; }
    case "${1:-status}" in
        up)
            command -v podman >/dev/null || die "podman not found (dnf install podman)"
            systemctl is-active --quiet sshd || die "sshd is not active: sudo systemctl enable --now sshd"
            linux_key; linux_alias; linux_shim
            loginctl enable-linger 2>/dev/null && say "linger: $(loginctl show-user "$USER" -p Linger --value)" \
                || say "WARN: could not enable linger (containers may die with the ssh session)"
            st=$(container_state)
            case "$st" in
                Up*)  say "container $CONTAINER: $st" ;;
                "")   say "container $CONTAINER: not created yet -- rocknix-gtk scripts/provision-build-container.sh (local) creates it" ;;
                *)    podman start "$CONTAINER" >/dev/null && say "container $CONTAINER: started ($(container_state))" ;;
            esac
            ssh -o BatchMode=yes -o ConnectTimeout=15 "$VM" \
                'echo "$(hostname) · $(uname -m) · page $(getconf PAGESIZE) · $(nproc) cores · $(free -g | awk "/Mem/{print \$2}") GB · $(df -Ph ~ | awk "NR==2{print \$4}") free · docker=$(command -v docker || echo MISSING) · containers: $(docker ps -a --format "{{.Names}}={{.Status}}" 2>/dev/null | tr "\n" " ")"' \
                || die "ssh $VM failed -- the forge reaches the node only this way"
            ;;
        down)
            [ -n "$(container_state)" ] && podman stop "$CONTAINER" >/dev/null && say "container $CONTAINER stopped" || say "container $CONTAINER: nothing to stop"
            ;;
        status)
            say "shape: Linux host IS the node ($(hostname), $(uname -r), $(nproc) cores, $(free -g | awk '/Mem/{print $2}') GB, $(df -Ph "$HOME" | awk 'NR==2{print $4}') free on $(stat -f -c %T "$HOME"), page $(getconf PAGESIZE), SELinux $(getenforce 2>/dev/null || echo n/a))"
            say "podman : $(podman --version 2>/dev/null || echo MISSING)   docker shim: $([ -x "$SHIM" ] && echo "$SHIM" || command -v docker || echo MISSING)"
            say "sshd   : $(systemctl is-active sshd)   alias '$VM': $(have_alias && echo present || echo MISSING)   key in authorized_keys: $([ -f "$KEY.pub" ] && grep -qF "$(awk '{print $2}' "$KEY.pub")" "$HOME/.ssh/authorized_keys" 2>/dev/null && echo yes || echo no)   linger: $(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo ?)"
            st=$(container_state); say "kernel container $CONTAINER: ${st:-NOT CREATED}"
            if [ -n "$st" ]; then
                case "$st" in Up*)
                    say "  gcc-15 $(podman exec "$CONTAINER" gcc-15 -dumpfullversion 2>/dev/null || echo MISSING) · gcc-16 $(podman exec "$CONTAINER" gcc-16 -dumpfullversion 2>/dev/null || echo MISSING) · binutils $(podman exec "$CONTAINER" sh -c 'ld --version | head -1' 2>/dev/null | awk '{print $NF}') · mkbootimg $(podman exec "$CONTAINER" sh -c 'command -v mkbootimg >/dev/null && echo yes || echo no' 2>/dev/null)"
                    say "  /kernel: $(podman exec "$CONTAINER" sh -c 'ls /kernel 2>/dev/null | tr "\n" " "')"
                    say "  staging: $(podman exec "$CONTAINER" sh -c 'ls /kernel/staging 2>/dev/null | tr "\n" " "')"
                    ;;
                esac
            fi
            ;;
        *) echo "usage: $0 [status|up|down]" >&2; exit 2 ;;
    esac
    exit 0
fi

# ---------------------------------------------------------------- macOS: the Lima VM
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
