#!/usr/bin/env bash
# ==========================================================
# forge lane: RPCS3 — reset -> patch -> build -> hardened package -> gates
# ==========================================================
# Runs ON THE BUILD NODE (Ubuntu), detached by forge.sh. Env (set by forge):
#   TREE    rpcs3 checkout (base + patch working model, e.g. /home/ubuntu/rpcs3)
#   BASE    upstream base commit (tracked files reset here; untracked survive)
#   PATCH   patch FILENAME inside $RUNDIR (pushed from the Air's committed
#           fork clone — the node's untracked copies are never consulted)
#   IMG     toolchain image (etk-rpcs3-jammy-aarch64:llvm22)
#   MARKER  symbol that must be in the built binary (stale-build tripwire)
#   ANAME   staged artifact name under ~/etk/emulators/
#   RUNDIR  this run's directory (logs, banked diffs, pushed scripts)
#
# Encoded traps (ForgeScript_Handoff_20260807 §3.1):
#   * .ci/build-linux-aarch64.sh does `mkdir build` and FAILS if build/ exists
#   * it ends by calling the RAW .ci/deploy-linux.sh — that artifact is the
#     BUILDING.md trap; the hardened packager discards and repackages
#   * tree state is banked BEFORE the reset — no state is silently destroyed
# ==========================================================
set -eu

log() { printf '[lane_rpcs3] %s\n' "$*"; }
cd "$TREE"

# bank whatever the tree holds (its normal resting state is base+patch from
# the previous run; anything else is investigated from this diff, never lost)
git diff > "$RUNDIR/rpcs3-pre-reset.diff" || true
log "banked pre-reset diff: $(wc -l < "$RUNDIR/rpcs3-pre-reset.diff") lines"

log "reset --hard $BASE + apply $PATCH"
git reset --hard "$BASE" >/dev/null
git apply --check "$RUNDIR/$PATCH"
git apply "$RUNDIR/$PATCH"

# canonical packager + gate from the fork repo (one source of truth)
install -m 0755 "$RUNDIR/package-appimage.sh" scripts/package-appimage.sh
install -m 0755 "$RUNDIR/verify-markers.sh"  scripts/verify-markers.sh

# build/ is root-owned (the container writes the bind mount as root), so a
# plain rm hits EPERM — clear it from inside the container instead
rm -rf build 2>/dev/null || true
[ -e build ] && docker run --rm -v "$TREE":/rpcs3 "$IMG" sh -c 'rm -rf /rpcs3/build'
[ -e build ] && { log "FATAL: could not clear build/"; exit 1; }
log "clean build (25 min class) with $IMG"
docker run --rm -v "$TREE":/rpcs3 "$IMG" \
    sh -c 'cd / && COMPILER=clang RUN_UNIT_TESTS=OFF /rpcs3/.ci/build-linux-aarch64.sh'

log "hardened repackage (discarding the raw deploy artifact)"
docker run --rm -v "$TREE":/rpcs3 "$IMG" \
    sh -c "cd /rpcs3 && MARKER='$MARKER' REPO=/rpcs3 scripts/package-appimage.sh aarch64"

APPIMG=$(ls -t build/*.AppImage | head -1)
[ -n "$APPIMG" ] || { log "FATAL: no AppImage after packaging"; exit 1; }

# marker gates run IN the container (host binutils not guaranteed)
log "gates: verify-markers (patch + built ELF) + GTK Edition literal"
docker run --rm -v "$TREE":/rpcs3 -v "$RUNDIR":/rundir "$IMG" sh -c "
    cd /rpcs3
    bash scripts/verify-markers.sh /rundir/$PATCH build/AppDir/usr/bin/rpcs3
    n=\$(strings -a build/AppDir/usr/bin/rpcs3 | grep -c 'GTK_' || true)
    echo \"evidence: GTK_ string count: \$n\"
    ed=\$(strings -a build/AppDir/usr/bin/rpcs3 | grep -cF 'GTK Edition' || true)
    [ \"\$ed\" -gt 0 ] || { echo 'FATAL: GTK Edition literal missing'; exit 1; }
    strings -a build/AppDir/usr/bin/rpcs3 | grep -F 'GTK Edition v' | head -1
"

# stage node-side where the image lane reads (trap #2: gitignored payloads
# never travel by git pull — they are STAGED, explicitly)
mkdir -p "$HOME/etk/emulators"
cp "$APPIMG" "$HOME/etk/emulators/$ANAME"
( cd "$HOME/etk/emulators" && sha256sum "$ANAME" > "$ANAME.sha256" )
log "artifact: $ANAME $(stat -c %s "$HOME/etk/emulators/$ANAME") B"
log "sha256  : $(cut -d' ' -f1 "$HOME/etk/emulators/$ANAME.sha256")"
log "LANE OK"
