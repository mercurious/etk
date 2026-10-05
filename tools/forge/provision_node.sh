#!/usr/bin/env bash
# ==========================================================
# provision_node.sh — bring a FRESH build node to forge-ready from the repos alone
# ==========================================================
# Runs ON THE AIR; reaches the node only through the FORGE_HOST ssh alias.
#
# WHY THIS EXISTS (2026-10-04): etk-cloud was destroyed — the Oracle trial lapsed
# before the PAYG conversion landed, and the instance went with it. The audit of
# what a fresh node needs found the 2026-08-05 lesson ("four lanes' recipes lived
# only on the laptop") had recurred one layer down: the turnip trees, the
# etk-imgtool container, the kernel ground truth, the base image and the image
# seed all existed ONLY on the dead node — no script made them. This tool is
# the missing recipe, one phase per dependency, each idempotent.
#
# ⚠ MINT-CLASS (TRACK_MANUAL §1.1). Every phase except `check` builds containers
# or images on someone else's computer — the OPERATOR runs it. `check` is
# read-only ssh (the §1.1 carve-out) and is the SURFACE: a lane is not ready
# until its row reads READY here.
#
# Usage:  tools/forge/provision_node.sh [phase ...]      (default: check)
#   check        read-only readiness table, lane by lane
#   checkouts    ~/etk at the Air's HEAD, the three forks at origin/main,
#                ~/rpcs3 cloned from ARMSX3 (remote named `armsx3`) with BASE present
#   containers   turnip-rocknix · rocknix-gtk-kernel-sid · etk-imgtool
#   trees        /work/mesa-<V> fork trees in turnip-rocknix, per FORGE_TURNIP_VERS
#   inputs       base img (sha-pinned) + seed_config + the manifest's pinned
#                binaries, pushed from the Air (rebuilds are not byte-identical,
#                and the image lane verifies every input against its pin)
#   groundtruth  kernel 7.2 ground truth derived from the PUBLIC base image,
#                then stage_72.sh into the kernel container
#   toolchain    the rpcs3 LLVM-22 toolchain image — DETACHED, multi-hour
#   all          every phase above, in dependency order
#
# Knobs: the FORGE_* defaults are read out of forge.sh itself (one source), then
# etk.conf. PROVISION_BASE_SHA pins the base image (default: the 20260901 sha).
# ==========================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
# ONE source for the knobs: forge.sh's own default lines, then the operator's
# etk.conf. Copying defaults here is how two tools drift apart. (etk.conf can
# hold a PADDOCK_TOKEN — sourced, never echoed.)
eval "$(grep -E '^FORGE_[A-Z0-9_]+="\$\{FORGE_[A-Z0-9_]+:-' forge.sh)"
[ -f ./etk.conf ] && . ./etk.conf

HOST="$FORGE_HOST"
BASEDATE="$FORGE_IMAGE_BASEDATE"
BASE_GZ="ROCKNIX-SM8250.aarch64-$BASEDATE.img.gz"
BASE_URL="https://github.com/ROCKNIX/distribution/releases/download/$BASEDATE/$BASE_GZ"
# Recorded 2026-10-04 from upstream's own .sha256 asset. Pinned HERE so a re-fetch
# is checked against what we recorded, not only against a file served beside it.
PROVISION_BASE_SHA="${PROVISION_BASE_SHA:-3a4bf87ff2a45f60d6d4bc2c67177fd8e9c3e974805a7579b34d9fcd68c9667d}"
# The digest the turnip-android/chiaki/wl-mirror lanes already pin. The old
# turnip-rocknix rode the rolling ubuntu:24.04 tag; a rebuild is the moment to pin.
TURNIP_IMAGE="${TURNIP_IMAGE:-ubuntu@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea}"
# etk-imgtool's package set — recovered from the 2026-07-07 hostless-cut handoff
# plus every binary build_gtk_image_v2.sh / lane_image.sh call (grep'd 2026-10-04).
# e2fsprogs >= 1.47 is load-bearing: the recipe passes `mke2fs -O ^orphan_file`.
IMGTOOL_PKGS="parted mtools dosfstools e2fsprogs fdisk rsync gzip xz-utils python3 squashfs-tools"
GT="/home/ubuntu/gt72"          # stage_72.sh's default GT, spelled for the node

NSSH() { ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "$@"; }
# Run a stdin script on the node WITH positional args. ssh joins its argv into
# ONE command string that the remote shell re-splits, so a plain
# `NSSH bash -s -- "$LIST"` arrives as N words — the 2026-10-05 first run handed
# etk-imgtool `$1=parted` and silently dropped mtools/e2fsprogs/python3/... .
# %q-quote each arg so it survives the remote re-split as exactly one word.
NRUN() { NSSH "bash -s -- $(printf '%q ' "$@")"; }
say()  { printf '\033[36m[provision]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[provision] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

# the manifest's pinned names, parsed exactly as forge.sh does
_manifest() {  # <key> <field>
    sed -n "/\"$1\": {/,/}/p" config/gtk_stack.json | sed -n "s/.*\"$2\": \"\([^\"]*\)\".*/\1/p" | head -1
}
CERT_KNAME=$(_manifest kernel asset)
CERT_ANAME=$(_manifest rpcs3 asset)
# the cumulative driver catalog the card bakes (install.sh CERTIFIED_BUILDS)
CERT_DRIVERS=$(sed -n 's/^CERTIFIED_BUILDS="\(.*\)"$/\1/p' install.sh | head -1)

# ---------------------------------------------------------------------------
phase_checkouts() {
    local head; head=$(git rev-parse HEAD)
    git fetch -q origin main
    git merge-base --is-ancestor "$head" origin/main \
        || die "Air HEAD ${head:0:9} is not on origin/main — push first (the node clones; it cannot see local commits)"
    say "checkouts: ~/etk @ ${head:0:9}, forks @ origin/main, ~/rpcs3 @ $FORGE_RPCS3_BASE"
    NRUN "$head" "$FORGE_RPCS3_BASE" <<'REMOTE'
set -euo pipefail
HEAD="$1" BASE="$2"
# init+fetch rather than clone: the rpcs3/turnip lanes mkdir ~/etk/{emulators,drivers}
# and `git clone` refuses a non-empty target — a lane that ran first would wedge it.
repo() {  # <url> <dir>
    if [ ! -d "$2/.git" ]; then
        mkdir -p "$2"; git -C "$2" init -q; git -C "$2" remote add origin "$1"
    fi
    git -C "$2" fetch -q origin
}
on_main() {  # <dir> — fast-forward only; a node never carries local commits
    if git -C "$1" rev-parse -q --verify main >/dev/null; then
        git -C "$1" checkout -q main && git -C "$1" merge -q --ff-only origin/main
    else
        git -C "$1" checkout -q -b main origin/main
    fi
}
repo https://github.com/mercurious/etk "$HOME/etk"
# The image lane asserts node HEAD == the cut's HEAD (lane_image.sh) — detach there.
git -C "$HOME/etk" checkout -q --detach "$HEAD"
for r in rocknix-gtk etk-turnip-gtk etk-rpcs3-gtk; do
    repo "https://github.com/mercurious/$r" "$HOME/$r"; on_main "$HOME/$r"
done
# ~/rpcs3: clone ARMSX3 with its remote NAMED armsx3 (TRACK_MANUAL §A.1 mint-loop
# #1 — the lane does no fetch, and every handoff says `fetch armsx3`). Its
# history carries every RPCS3 base we have ever pinned (a1deb2921 is an ancestor).
if [ ! -d "$HOME/rpcs3/.git" ]; then
    git clone -q -o armsx3 https://github.com/ARMSX2/ARMSX3.git "$HOME/rpcs3"
fi
# --recurse-submodules=no: containers re-own .git/modules as root (Leapfrog 2026-08-20)
git -C "$HOME/rpcs3" fetch -q --recurse-submodules=no --tags armsx3
git -C "$HOME/rpcs3" cat-file -e "$BASE^{commit}" || { echo "BASE $BASE not in ~/rpcs3" >&2; exit 1; }
# Park at BASE only when clean; a dirty tree is the lane's resting state and
# preflight judges it (it banks the diff — never discard it here).
if [ -z "$(git -C "$HOME/rpcs3" status --porcelain --untracked-files=no)" ]; then
    git -C "$HOME/rpcs3" checkout -q --detach "$BASE"
fi
for d in etk rocknix-gtk etk-turnip-gtk etk-rpcs3-gtk rpcs3; do
    printf '   %-15s %s\n' "$d" "$(git -C "$HOME/$d" log -1 --format='%h %s' | cut -c1-70)"
done
REMOTE
}

# ---------------------------------------------------------------------------
phase_containers() {
    say "containers: turnip-rocknix (fork provisioner, $TURNIP_IMAGE)"
    # Run the fork's provisioner ON the node: driven from the Air it defaults to
    # SSH_KEY=~/.ssh/etk_rig, which the Asahi boot does not authorize.
    NSSH "cd ~/etk-turnip-gtk && IMAGE='$TURNIP_IMAGE' scripts/provision-build-container.sh"

    say "containers: rocknix-gtk-kernel-sid (fork provisioner)"
    # staging/ is gitignored, so a fresh clone lacks it and the provisioner dies.
    NSSH "mkdir -p ~/rocknix-gtk/staging && cd ~/rocknix-gtk && scripts/provision-build-container.sh"

    say "containers: etk-imgtool (no recipe existed anywhere until now)"
    NRUN "$IMGTOOL_PKGS" <<'REMOTE'
set -euo pipefail
PKGS="$1"
mkdir -p "$HOME/etk/os-install"
if docker ps -a --format '{{.Names}}' | grep -qx etk-imgtool; then
    docker start etk-imgtool >/dev/null
else
    # LONG-LIVED on purpose: lane_image.sh's artifact verify reads the spliced
    # /tmp/work2.img the recipe left behind in this same container.
    docker run -d --name etk-imgtool \
        -v "$HOME/etk/os-install":/work \
        -v "$HOME/etk":/etk:ro \
        -v "$HOME/rocknix-gtk":/rocknix-gtk:ro \
        -w /work debian:sid sleep infinity >/dev/null
fi
docker exec etk-imgtool bash -lc "
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq --no-install-recommends $PKGS >/dev/null
    printf '   %-10s %s\n' e2fsprogs \"\$(mke2fs -V 2>&1 | head -1)\"
    printf '   %-10s %s\n' parted \"\$(parted --version | head -1)\""
REMOTE
}

# ---------------------------------------------------------------------------
phase_trees() {
    # The forge's turnip lane builds /work/mesa-<V> in place; nothing created
    # those trees on a fresh container (prepare-fork-branch.sh defaults WORKDIR
    # to $PWD/mesa-fork-<tag>, and the container has no bind mount for the repo).
    local v
    for v in $FORGE_TURNIP_VERS; do
        case "$v" in
            *-devel-*) die "trees: $v is a devel pin — prepare it by hand per etk-turnip-gtk BUILDING.md (SKIP_PATCHES differs per main sha)" ;;
        esac
        say "trees: /work/mesa-$v (fork HEAD's full series)"
        NRUN "$v" <<'REMOTE'
set -euo pipefail
V="$1" C=turnip-rocknix
if docker exec "$C" test -f "/work/mesa-$V/src/freedreno/vulkan/tu_etk_gears.h"; then
    echo "   mesa-$V already prepared @ $(docker exec "$C" git -C "/work/mesa-$V" rev-parse --short HEAD) — kept"
    exit 0
fi
docker exec "$C" rm -rf /work/etk-turnip-gtk
docker cp "$HOME/etk-turnip-gtk" "$C:/work/etk-turnip-gtk"
docker exec "$C" bash -lc "cd /work/etk-turnip-gtk && WORKDIR=/work/mesa-$V BASE_TAG=mesa-$V ./scripts/prepare-fork-branch.sh apply"
docker exec "$C" test -f "/work/mesa-$V/src/freedreno/vulkan/tu_etk_gears.h"
echo "   mesa-$V @ $(docker exec "$C" git -C "/work/mesa-$V" log -1 --format='%h %s' | cut -c1-60)"
REMOTE
    done
}

# ---------------------------------------------------------------------------
phase_inputs() {
    say "inputs: base $BASE_GZ (sha-pinned ${PROVISION_BASE_SHA:0:12}..)"
    NRUN "$BASE_URL" "$BASE_GZ" "$PROVISION_BASE_SHA" <<'REMOTE'
set -euo pipefail
URL="$1" F="$HOME/etk/os-install/$2" SHA="$3"
mkdir -p "$(dirname "$F")"
if [ ! -f "$F" ] || [ "$(sha256sum "$F" | cut -d' ' -f1)" != "$SHA" ]; then
    curl -fL --retry 3 -o "$F.part" "$URL"
    GOT=$(sha256sum "$F.part" | cut -d' ' -f1)
    [ "$GOT" = "$SHA" ] || { echo "base img sha MISMATCH: $GOT (pinned $SHA)" >&2; rm -f "$F.part"; exit 1; }
    mv "$F.part" "$F"
fi
echo "   $(basename "$F") sha OK"
REMOTE

    [ -d os-install/build/seed_config ] || die "inputs: os-install/build/seed_config missing on the Air.
  It is a rendered rig snapshot (gitignored) — recover it from the last good card:
  GTKSTOR partition, /games-internal/roms/etk/.seed_config (debugfs rdump)."
    say "inputs: seed_config ($(find os-install/build/seed_config -type f | wc -l) files)"
    rsync -a --delete os-install/build/seed_config/ "$HOST:etk/os-install/build/seed_config/"

    # The baked binaries are the MANIFEST's bytes, not node re-mints: Mesa and
    # the kernel are not byte-reproducible across hosts, and lane_image.sh
    # refuses any input whose sha differs from gtk_stack.json / install.sh.
    local f missing=""
    for f in "$FORGE_KERNEL_ARTDIR/$CERT_KNAME" "emulators/$CERT_ANAME"; do
        [ -f "$f" ] || missing="$missing $f"
    done
    for f in $CERT_DRIVERS; do [ -f "drivers/$f" ] || missing="$missing drivers/$f"; done
    [ -z "$missing" ] || die "inputs: pinned artifacts missing on the Air:$missing"
    say "inputs: kernel $CERT_KNAME · rpcs3 $CERT_ANAME · $(echo $CERT_DRIVERS | wc -w) catalog drivers"
    NSSH "mkdir -p ~/rocknix-gtk/artifacts ~/etk/emulators ~/etk/drivers"
    rsync -a "$FORGE_KERNEL_ARTDIR/$CERT_KNAME" "$HOST:rocknix-gtk/artifacts/"
    rsync -a "emulators/$CERT_ANAME" "$HOST:etk/emulators/"
    rsync -a $(for f in $CERT_DRIVERS; do printf 'drivers/%s ' "$f"; done) "$HOST:etk/drivers/"
}

# ---------------------------------------------------------------------------
phase_groundtruth() {
    # stage_72.sh wants three rig-ground-truth inputs that lived only in the dead
    # node's ~/gt72. All three are carried by the PUBLIC base image the rig was
    # migrated to: the stock KERNEL embeds its own config (IKCONFIG — the very
    # bytes /proc/config.gz serves) and its initramfs, and the SYSTEM squashfs
    # carries /usr/lib/firmware.
    #
    # BUT THE PUBLIC KERNEL IS NOT THE ONE THE KIT WAS BUILT AGAINST (2026-10-05).
    # The rig was migrated to the 20260827 nightly and kept that build's KERNEL;
    # every certified GTK kernel (20260827-0.5, 20260901-0.5, -0.5.1) embeds THAT
    # kernel's initramfs (b1a45ea0, built 08-27), not the official 20260901 one
    # (b986ecda) — same 67 files, busybox/avfsd rebuilt. The config is identical.
    # The nightly is no longer downloadable, so the rig's own copy, pulled once
    # to the Air (~/rocknix-gtk/groundtruth/KERNEL.rig-stock-<date>, from the
    # rig's /flash/KERNEL.etk-stock), is preferred whenever it exists. Either
    # way the GATE below decides: the staged initramfs must be byte-identical to
    # what the certified kernel embeds, or a remint cannot reproduce it.
    local rigk="$(dirname "$FORGE_KERNEL_ARTDIR")/groundtruth/KERNEL.rig-stock-$BASEDATE" ksrc=public
    if [ -f "$rigk" ]; then
        ksrc=rig
        rsync -a "$rigk" "$HOST:etk/os-install/KERNEL.rig-stock-$BASEDATE"
        say "groundtruth: config/initramfs from the RIG's stock kernel ($(sha256sum "$rigk" | cut -c1-12)..); firmware from $BASE_GZ"
    else
        say "groundtruth: WARN no $rigk — deriving everything from $BASE_GZ (the gate will judge it)"
    fi
    NRUN "$BASE_GZ" "$BASEDATE" "$GT" "$ksrc" <<'REMOTE'
set -euo pipefail
GZ="$1" D="$2" GT="$3" KSRC="$4"
U="$(id -u):$(id -g)"      # extracted files stay owned by ubuntu on the host
rm -rf "$HOME/etk/os-install/.gt-tmp"; mkdir -p "$HOME/etk/os-install/.gt-tmp"
docker exec -i -u "$U" -e GZ="$GZ" -e D="$D" -e KSRC="$KSRC" etk-imgtool bash -s <<'IN'
set -euo pipefail
cd /work/.gt-tmp
gunzip -c "/work/$GZ" > base.img
OFF=$(parted -m base.img unit B print | awk -F: '/^1:/{gsub("B","",$2);print $2}')
if [ "$KSRC" = rig ]; then cp "/work/KERNEL.rig-stock-$D" KERNEL.stock
else mcopy -i "base.img@@$OFF" ::/KERNEL KERNEL.stock; fi
mcopy -i "base.img@@$OFF" ::/SYSTEM SYSTEM.stock
rm -f base.img
# IKCONFIG: gzip stream between the IKCFG_ST / IKCFG_ED markers (what the
# kernel's scripts/extract-ikconfig does, without needing a kernel tree)
python3 - <<'PY'
import zlib
b = open("KERNEL.stock", "rb").read()
s = b.find(b"IKCFG_ST"); e = b.find(b"IKCFG_ED", s)
assert s >= 0 and e > s, "no IKCONFIG in stock KERNEL"
open("config-7.2-rig.txt", "wb").write(zlib.decompress(b[s+8:e], 31))
PY
python3 /rocknix-gtk/scripts/extract_initramfs.py KERNEL.stock "initramfs-stock-$D.cpio" >/dev/null
head -c6 "initramfs-stock-$D.cpio" | grep -q 070701
FW=$(sed -n 's/^CONFIG_EXTRA_FIRMWARE="\(.*\)"$/\1/p' config-7.2-rig.txt)
[ -n "$FW" ] || { echo "CONFIG_EXTRA_FIRMWARE empty" >&2; exit 1; }
# NOT usr/lib/firmware: in the image that is a symlink to
# /run/kernel-overlays/firmware, which ROCKNIX assembles at BOOT from the
# kernel-overlays (found 2026-10-05 — the rig's live /usr/lib/firmware hides
# this). The blobs live in the base overlay. Extract that dir whole, then cp -L
# in case a blob links to a sibling. The tree stays NESTED (qcom/sm8250/…) —
# CONFIG_EXTRA_FIRMWARE names relative paths.
FWDIR=usr/lib/kernel-overlays/base/lib/firmware
rm -rf sys
unsquashfs -q -n -d sys SYSTEM.stock "$FWDIR" >/dev/null
mkdir -p "external-firmware-$D"
( cd "sys/$FWDIR" && cp -L --parents $FW "/work/.gt-tmp/external-firmware-$D/" )
rm -rf sys SYSTEM.stock
echo "   KERNEL.stock sha $(sha256sum KERNEL.stock | cut -c1-12).. · config $(wc -l < config-7.2-rig.txt) lines · $(find external-firmware-$D -type f | wc -l) firmware blobs"
IN
mkdir -p "$GT"
rm -rf "$GT/external-firmware-$D"
mv "$HOME/etk/os-install/.gt-tmp/config-7.2-rig.txt" "$HOME/etk/os-install/.gt-tmp/initramfs-stock-$D.cpio" \
   "$HOME/etk/os-install/.gt-tmp/external-firmware-$D" "$HOME/etk/os-install/.gt-tmp/KERNEL.stock" "$GT/"
rmdir "$HOME/etk/os-install/.gt-tmp"
REMOTE

    # THE GATE: ground truth is only "true" if it reproduces what the certified
    # kernel actually embeds. Read the answer out of the artifact, not a note.
    local want got xi="$(dirname "$FORGE_KERNEL_ARTDIR")/scripts/extract_initramfs.py" tmp
    tmp=$(mktemp)
    python3 "$xi" "$FORGE_KERNEL_ARTDIR/$CERT_KNAME" "$tmp" >/dev/null || die "groundtruth: could not carve the initramfs out of $CERT_KNAME"
    want=$(sha256sum "$tmp" | cut -d' ' -f1); rm -f "$tmp"
    got=$(NSSH "sha256sum $GT/initramfs-stock-$BASEDATE.cpio" | cut -d' ' -f1)
    if [ "$got" = "$want" ]; then
        say "groundtruth: initramfs ${got:0:12}.. == the one $CERT_KNAME embeds — remint-faithful"
    elif [ "${PROVISION_GT_ALLOW_DRIFT:-0}" = 1 ]; then
        say "groundtruth: WARN initramfs ${got:0:12}.. != certified ${want:0:12}.. (PROVISION_GT_ALLOW_DRIFT=1)"
    else
        die "groundtruth: staged initramfs ${got:0:12}.. is NOT what $CERT_KNAME embeds (${want:0:12}..).
  A remint would diverge from the certified kernel. Pull the rig's stock kernel to
  $rigk (from /flash/KERNEL.etk-stock), or set PROVISION_GT_ALLOW_DRIFT=1 deliberately."
    fi

    # ROCKNIX_REF pinned to the release tag — stage_72.sh still defaults to the
    # pre-release nightly; the certified remint used the tag (VALIDATION.md).
    say "groundtruth: stage_72.sh (ROCKNIX_REF=$BASEDATE) into rocknix-gtk-kernel-sid"
    NSSH "ROCKNIX_REF=$BASEDATE BASEDATE=$BASEDATE GT=$GT bash ~/rocknix-gtk/scripts/stage_72.sh"
}

# ---------------------------------------------------------------------------
phase_toolchain() {
    # Hours, so detached like every forge lane: a dropped ssh cannot kill it and
    # a re-run reports instead of restarting. The fork script pins rpcs3-docker
    # and tags the name forge.sh expects ($FORGE_RPCS3_IMAGE).
    say "toolchain: $FORGE_RPCS3_IMAGE (detached; log ~/forge-runs/toolchain-rpcs3.log)"
    NRUN "$FORGE_RPCS3_IMAGE" <<'REMOTE'
set -euo pipefail
IMG="$1" R="$HOME/forge-runs"; mkdir -p "$R"
if [ -n "$(docker images -q "$IMG")" ]; then echo "   $IMG present — nothing to do"; exit 0; fi
if [ -f "$R/toolchain-rpcs3.pid" ] && kill -0 "$(cat "$R/toolchain-rpcs3.pid")" 2>/dev/null; then
    echo "   already building (pid $(cat "$R/toolchain-rpcs3.pid")): tail -f ~/forge-runs/toolchain-rpcs3.log"; exit 0
fi
rm -f "$R/toolchain-rpcs3.rc"
cd "$HOME/etk-rpcs3-gtk"
setsid nohup bash -c "TAG='$IMG' scripts/build-image-etk.sh > '$R/toolchain-rpcs3.log' 2>&1; echo \$? > '$R/toolchain-rpcs3.rc'" \
    >/dev/null 2>&1 < /dev/null &
echo $! > "$R/toolchain-rpcs3.pid"
echo "   launched pid $! — watch: ssh etk-cloud tail -f forge-runs/toolchain-rpcs3.log"
REMOTE
}

# ---------------------------------------------------------------------------
phase_check() {
    local head; head=$(git rev-parse --short=9 HEAD)
    NSSH true 2>/dev/null || die "cannot reach '$HOST' (BatchMode). A REBUILT node has new host keys:
  ssh-keygen -R <its address>, then one interactive 'ssh $HOST true'."
    local drivers_csv; drivers_csv=$(echo $CERT_DRIVERS | tr ' ' ',')
    # what the certified kernel embeds — the staging row is judged against it
    local wantinit="" tmp xi="$(dirname "$FORGE_KERNEL_ARTDIR")/scripts/extract_initramfs.py"
    if [ -f "$xi" ] && [ -f "$FORGE_KERNEL_ARTDIR/$CERT_KNAME" ]; then
        tmp=$(mktemp)
        python3 "$xi" "$FORGE_KERNEL_ARTDIR/$CERT_KNAME" "$tmp" >/dev/null 2>&1 && wantinit=$(sha256sum "$tmp" | cut -d' ' -f1)
        rm -f "$tmp"
    fi
    NRUN "$head" "$FORGE_RPCS3_BASE" "$FORGE_RPCS3_IMAGE" "$FORGE_TURNIP_VERS" \
        "$BASE_GZ" "$PROVISION_BASE_SHA" "$CERT_KNAME" "$CERT_ANAME" "$drivers_csv" "$BASEDATE" "$wantinit" <<'REMOTE'
HEAD="$1" BASE="$2" IMG="$3" VERS="$4" GZ="$5" GZSHA="$6" KN="$7" AN="$8" DRV="$9" D="${10}" WANTINIT="${11}"
row() { printf '  %-7s %-46s %s\n' "$1" "$2" "$3"; }
ok()  { [ "$1" = 0 ] && echo READY || echo "MISSING${2:+ — $2}"; }
up()  { docker ps --format '{{.Names}}' | grep -qx "$1"; }
echo "== node: $(hostname) · $(nproc) cores · $(free -g | awk '/Mem/{print $2}') GB · $(df -Ph ~ | awk 'NR==2{print $4}') free · load $(cut -d' ' -f1-3 /proc/loadavg)"
# A container on a bare image id is a legacy-builder `docker build` step — our
# own toolchain build while it runs (2026-10-05: `competent_solomon` read as
# contention). Forge containers are matched by name first.
other=$(docker ps --format '{{.Names}} {{.Image}}' | grep -vE '^(turnip-rocknix|rocknix-gtk-kernel-sid|etk-imgtool) ' \
        | grep -vE ' [0-9a-f]{12}$' | cut -d' ' -f1 | tr '\n' ' ')
# Judge contention by LOAD, not by presence: a parked `sleep infinity` box from
# another workstream is not competing for cores (2026-10-05: asahi-kbuild sat
# idle at load 0.00 and still read CONTENDED).
if [ -n "$other" ]; then
    if awk '{exit !($1 >= 1.0)}' /proc/loadavg; then
        echo "   CONTENDED: load $(cut -d' ' -f1 /proc/loadavg) with non-forge containers up: $other"
    else
        echo "   idle non-forge containers present (not contending): $other"
    fi
fi
echo "== lane readiness"
# host
for c in git rsync curl python3 setsid docker; do command -v $c >/dev/null || miss="$miss $c"; done
docker info >/dev/null 2>&1 || miss="$miss docker-daemon"
row host "tools + docker (no sudo)" "$( [ -z "$miss" ] && echo READY || echo "MISSING:$miss")"
# checkouts
nh=$(git -C ~/etk rev-parse --short=9 HEAD 2>/dev/null)
row all "~/etk @ Air HEAD $HEAD" "$( [ "$nh" = "$HEAD" ] && echo READY || echo "MISSING — node at ${nh:-none}")"
for r in rocknix-gtk etk-turnip-gtk etk-rpcs3-gtk; do
    h=$(git -C ~/$r rev-parse --short HEAD 2>/dev/null); row all "~/$r" "$( [ -n "$h" ] && echo "READY @ $h" || echo MISSING)"
done
# rpcs3
git -C ~/rpcs3 cat-file -e "$BASE^{commit}" 2>/dev/null; row rpcs3 "~/rpcs3 has BASE $BASE" "$(ok $?)"
git -C ~/rpcs3 remote get-url armsx3 >/dev/null 2>&1; row rpcs3 "~/rpcs3 remote 'armsx3'" "$(ok $?)"
if [ -n "$(docker images -q "$IMG" 2>/dev/null)" ]; then row rpcs3 "image $IMG" READY
elif [ -f ~/forge-runs/toolchain-rpcs3.pid ] && kill -0 "$(cat ~/forge-runs/toolchain-rpcs3.pid)" 2>/dev/null; then
    row rpcs3 "image $IMG" "BUILDING $(( ($(date +%s) - $(stat -c %Y ~/forge-runs/toolchain-rpcs3.pid)) / 60 )) min — $(tail -n 1 ~/forge-runs/toolchain-rpcs3.log | tr -d '\r' | cut -c1-48)"
else row rpcs3 "image $IMG" "MISSING$( [ -f ~/forge-runs/toolchain-rpcs3.rc ] && echo " — last build rc=$(cat ~/forge-runs/toolchain-rpcs3.rc)")"; fi
# turnip
up turnip-rocknix; row turnip "container turnip-rocknix Up" "$(ok $?)"
for v in $VERS; do
    docker exec turnip-rocknix test -f /work/mesa-$v/src/freedreno/vulkan/tu_etk_gears.h 2>/dev/null
    row turnip "tree /work/mesa-$v (gears)" "$(ok $?)"
done
# kernel
up rocknix-gtk-kernel-sid; row kernel "container rocknix-gtk-kernel-sid Up" "$(ok $?)"
docker exec rocknix-gtk-kernel-sid sh -c "gcc-15 -dumpfullversion" >/dev/null 2>&1
row kernel "gcc-15 in container" "$( [ $? = 0 ] && echo "READY ($(docker exec rocknix-gtk-kernel-sid gcc-15 -dumpfullversion))" || echo MISSING)"
docker exec rocknix-gtk-kernel-sid sh -c "test -d /kernel/staging/patches-72/01-mainline && test -f /kernel/staging/config-7.2-rig.txt && test -f /kernel/staging/initramfs-stock-$D.cpio && test -d /kernel/staging/external-firmware-$D && test -f /kernel/linux-7.2.tar.xz" 2>/dev/null
row kernel "staging (stage_72.sh) assembled" "$(ok $? 'groundtruth phase')"
gi=$(docker exec rocknix-gtk-kernel-sid sha256sum /kernel/staging/initramfs-stock-$D.cpio 2>/dev/null | cut -d' ' -f1)
if [ -z "$WANTINIT" ]; then row kernel "staged initramfs == certified embed" "UNJUDGED — no certified kernel on the Air"
elif [ "$gi" = "$WANTINIT" ]; then row kernel "staged initramfs == certified embed" "READY (${gi:0:12})"
else row kernel "staged initramfs == certified embed" "MISMATCH ${gi:0:12} != ${WANTINIT:0:12} — re-run groundtruth"; fi
# image
up etk-imgtool; row image "container etk-imgtool Up" "$(ok $?)"
f=~/etk/os-install/$GZ
[ -f "$f" ] && [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$GZSHA" ]; row image "base $GZ (pinned sha)" "$(ok $?)"
[ -d ~/etk/os-install/build/seed_config ]; row image "build/seed_config" "$(ok $? 'inputs phase')"
[ -f ~/rocknix-gtk/artifacts/$KN ]; row image "kernel $KN" "$(ok $?)"
[ -f ~/etk/emulators/$AN ]; row image "rpcs3 ${AN:0:40}.." "$(ok $?)"
n=0 t=0; for d in $(echo "$DRV" | tr ',' ' '); do t=$((t+1)); [ -f ~/etk/drivers/$d ] && n=$((n+1)); done
row image "catalog drivers ($n/$t)" "$( [ "$n" = "$t" ] && echo READY || echo MISSING)"
echo "   (input SHAs are verified by the image lane itself against gtk_stack.json/install.sh)"
REMOTE
    # forge's own fingerprints live on the Air and survive a node loss — say so
    # before anyone reads `./forge.sh --status` as node truth.
    if [ -d state/forge/fingerprints ]; then
        say "note: state/forge/fingerprints describe the Air's staged artifacts, not this node."
        say "      kernel/image can fingerprint FRESH against an empty node — prove the image lane with --force;"
        say "      NEVER --force the kernel lane at the certified name (TRACK_MANUAL §A.1)."
    fi
}

# ---------------------------------------------------------------------------
PHASES="${*:-check}"
[ "$PHASES" = all ] && PHASES="checkouts containers inputs groundtruth trees toolchain check"
for p in $PHASES; do
    case "$p" in
        check|checkouts|containers|trees|inputs|groundtruth|toolchain) "phase_$p" ;;
        *) die "unknown phase '$p' (check checkouts containers trees inputs groundtruth toolchain all)" ;;
    esac
done
