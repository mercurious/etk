#!/bin/sh
# ETK Flip 2 internal-mic UCM overlay (install.sh STEP 6.76) — run by etk-ucm.service
# at boot, BEFORE PipeWire/WirePlumber read the card's UCM. Bind-mounts the kit's
# HiFi-RP.conf (stock + an "Internal Microphone" device) over the read-only stock file,
# but only when every gate holds; any failed gate leaves stock UCM in place (fail-soft:
# the mic stays off, nothing else changes). The verdict goes to stdout, i.e.
# `journalctl -u etk-ucm` (the tripwire copy is best-effort: the Sentry rewrites that
# log later in boot). The gates:
#   1. this is a Flip 2;
#   2. the BOOTED device tree carries the ETK "Internal Mic" widget (install.sh STEP 6.4
#      mic DTB) — on the stock DTB (fallback-stock entry, kill-switch, stand-down) the
#      mic path cannot power up, so offering the device would only produce silence;
#   3. the stock file is the one the overlay was derived from (an OS update that changes
#      it makes the overlay stale: reinstall ETK after refreshing config/ucm/);
#   4. not already bound.
# Test seams (tools/test_ucm_bind.sh): UCMB_* paths and UCMB_MOUNT (mount command).
ETK_UCM_BASE_SHA="862e5312cbfc2bce25c01733a3cd3e4c9f40a7d78d2b6743af1a75bf71410ae0"
TARGET="${UCMB_TARGET:-/usr/share/alsa/ucm2/Qualcomm/sm8250/HiFi-RP.conf}"
OVERLAY="${UCMB_OVERLAY:-/storage/.config/etk-ucm/HiFi-RP.conf}"
COMPAT="${UCMB_COMPAT:-/sys/firmware/devicetree/base/compatible}"
WIDGETS="${UCMB_WIDGETS:-/sys/firmware/devicetree/base/sound/widgets}"
MOUNTS="${UCMB_MOUNTS:-/proc/mounts}"
TRIP="${TRIPWIRE_LOG:-/storage/etk_tripwire.log}"
MOUNT="${UCMB_MOUNT:-mount}"

log() { echo "[etk-ucm] $*"; echo "[$(date '+%H:%M:%S')] etk-ucm: $*" >> "$TRIP" 2>/dev/null; }

tr '\0' '\n' < "$COMPAT" 2>/dev/null | grep -q '^retroidpocket,rpflip2$' \
    || { log "not a Flip 2 - stock UCM"; exit 0; }
tr '\0' '\n' < "$WIDGETS" 2>/dev/null | grep -q '^Internal Mic$' \
    || { log "booted DT has no 'Internal Mic' widget (stock DTB) - stock UCM, internal mic off"; exit 0; }
[ -f "$OVERLAY" ] || { log "overlay missing ($OVERLAY) - stock UCM"; exit 0; }
[ "$(sha256sum "$TARGET" 2>/dev/null | cut -d' ' -f1)" = "$ETK_UCM_BASE_SHA" ] \
    || { log "stock $(basename "$TARGET") changed (OS update?) - overlay stale, stock UCM kept; reinstall ETK with a refreshed config/ucm/"; exit 0; }
grep -q " $TARGET " "$MOUNTS" 2>/dev/null && { log "already bound"; exit 0; }
if $MOUNT --bind "$OVERLAY" "$TARGET" 2>/dev/null; then
    log "internal mic UCM overlay bound over $TARGET"
else
    log "bind-mount FAILED - stock UCM"
fi
exit 0
