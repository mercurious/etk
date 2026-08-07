#!/usr/bin/env bash
# ==========================================================
# forge lane: Turnip — one build per MESA_VER in the turnip-rocknix container
# ==========================================================
# Runs ON THE BUILD NODE, detached by forge.sh. Env:
#   VERS    space-separated Mesa versions (e.g. "26.1.6 26.2.0-rc3")
#   GTKVER  fork generation for the staged filename (e.g. 0.7)
#   RUNDIR  this run's directory
#
# Encoded traps (handoff §3.2):
#   * a tree without tu_etk_gears.h is a pre-0.7 tree — the shipped
#     BIT-COLLISION build came from exactly that; refuse to build it
#   * the SHIPPED artifact is the UNSTRIPPED .so (~17.1 MB); the .stripped.so
#     is 15.9 MB and shipping it is the wrong-artifact trap
#   * the embedded "Mesa <ver> (git-<sha>) ETK-GTK" string must name the
#     PREPARED TREE's own HEAD (prepare-fork-branch makes a real checkout:
#     base tag + fork patches as commits) — proof the artifact came from the
#     tree you think it did. NOT the etk-turnip-gtk repo tip: that repo's sha
#     never appears in a build (proven live 2026-08-07, first forge run).
# Reminting REMINDER: a new driver build-id invalidates every shader vault —
# first launch recompiles pre-menu. Cold-boot + warm-relaunch before any A/B.
# ==========================================================
set -eu

log() { printf '[lane_turnip] %s\n' "$*"; }

for V in $VERS; do
    log "== $V =="
    docker exec turnip-rocknix test -f "/work/mesa-$V/src/freedreno/vulkan/tu_etk_gears.h" || {
        log "FATAL: /work/mesa-$V is NOT a gtk_${GTKVER} tree (tu_etk_gears.h missing)"
        log "       re-prepare: BASE_TAG=mesa-$V ./scripts/prepare-fork-branch.sh apply"
        exit 1
    }
    docker exec turnip-rocknix bash -lc "MESA_VER=$V JOBS=4 /work/build_rocknix.sh"

    RAW="/work/out/libvulkan_freedreno-rocknix-$V.so"
    VSTR=$(docker exec turnip-rocknix sh -c "strings $RAW | grep -F 'ETK-GTK' | head -1" || true)
    log "version string: $VSTR"
    case "$VSTR" in
        *"Mesa $V"*ETK-GTK*) : ;;
        *) log "FATAL: missing/foreign ETK-GTK version string"; exit 1 ;;
    esac
    EMBED=$(printf '%s' "$VSTR" | sed -n 's/.*git-\([0-9a-f]*\)).*/\1/p')
    TREEHEAD=$(docker exec turnip-rocknix git -C "/work/mesa-$V" rev-parse HEAD 2>/dev/null || echo none)
    if [ "$TREEHEAD" = none ]; then
        log "WARN: /work/mesa-$V has no git — embedded git-$EMBED unverifiable"
    else
        case "$TREEHEAD" in
            "$EMBED"*) log "embedded git-$EMBED == prepared tree HEAD" ;;
            *) log "FATAL: embedded git-$EMBED != tree HEAD ($TREEHEAD) — built from a different tree than /work/mesa-$V"; exit 1 ;;
        esac
    fi

    SZ=$(docker exec turnip-rocknix stat -c %s "$RAW")
    [ "$SZ" -gt 16500000 ] || { log "FATAL: $SZ B smells STRIPPED — ship the unstripped .so"; exit 1; }

    TNAME="etk_turnip_rocknix_${V}_gtk_${GTKVER}.so"
    mkdir -p "$HOME/etk/drivers"
    docker exec turnip-rocknix cat "$RAW" > "$HOME/etk/drivers/$TNAME"
    ( cd "$HOME/etk/drivers" && sha256sum "$TNAME" > "$TNAME.sha256" )
    log "artifact: $TNAME ${SZ} B sha256=$(cut -d' ' -f1 "$HOME/etk/drivers/$TNAME.sha256")"
done
log "LANE OK"
