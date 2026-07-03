#!/bin/sh
# ==========================================================
# ETK AUDIO WATCHDOG (v0.2.0 - HARNESS + PERSISTENT BOOT STATUS)
# ==========================================================
# The SM8250 sound card intermittently NEVER probes at boot (~1 in 4
# boots observed 2026-07-02): an early q6afe clock-vote race against
# ADSP audio_pd bring-up fails the probe of 3370000.codec (va_macro,
# the clock supplier for ALL LPASS macros), the whole audio chain
# parks in deferred-probe forever, and PipeWire serves only "Dummy
# Output" — the entire boot is silent in every app. See
# AI_MANIFEST.md "ROCKNIX AUDIO STACK" + dossiers/AudioCampaign_20260702.md §1.
#
# This one-shot detects the failed state and re-triggers the probe
# via the bus-level drivers_probe node (validated live 2026-07-02:
# the deferred chain cascades up in ~2s, PipeWire hot-swaps the real
# sinks in). Run at boot by etk-audio-watchdog.service (HARNESS: the
# unit is hand-provisioned; fold into install.sh when promoted).
#
# GEMINI IMMUTABLE RULE:
# - BusyBox/POSIX only. No GNU-isms.
# - FAIL-SILENT + BOUNDED: every action best-effort; the script must
#   never wedge boot, never loop forever, never exit nonzero except
#   on a genuinely dead audio path.
# - TRIPWIRE IS ANOMALY-ONLY: log nothing on a healthy boot.
# - Status snapshot: $SHM_DIR/audio_path.txt (ok|revived|dead) via
#   atomic tmp+mv — consumed by the ledger audio gate (campaign W2).
# ==========================================================

. /storage/games-internal/roms/etk/scripts/env.sh 2>/dev/null

SHM_DIR="${SHM_DIR:-/dev/shm/etk_shm}"
TRIPWIRE_LOG="${TRIPWIRE_LOG:-/storage/etk_tripwire.log}"
STATUS_FILE="$SHM_DIR/audio_path.txt"

# The deferred-probe chain, root supplier first (va_macro). Poking a
# bound device is a harmless no-op, so partial states self-heal too.
AUDIO_DEVS="3370000.codec 3200000.rxmacro 3220000.txmacro 3240000.codec 3210000.soundwire 3230000.soundwire 3250000.soundwire sound"

log_tw() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') AUDIO_WATCHDOG: $1" >> "$TRIPWIRE_LOG" 2>/dev/null
}

set_status() {
    mkdir -p "$SHM_DIR" 2>/dev/null
    echo "$1" > "$STATUS_FILE.tmp" 2>/dev/null && mv "$STATUS_FILE.tmp" "$STATUS_FILE" 2>/dev/null
    # Persistent boot-scoped copy. The SHM copy does NOT survive the Sentry's
    # session lifecycle (observed 2026-07-03: $SHM_DIR recreated between
    # sessions — boot status written at 10:47 was gone by 10:57). The ledger's
    # snd= attribution reads THIS file instead, validating the leading epoch
    # against the current boot so a stale line from a prior boot never lies.
    if [ -n "$TELEMETRY_DIR" ]; then
        mkdir -p "$TELEMETRY_DIR" 2>/dev/null
        echo "$(date +%s) $1" > "$TELEMETRY_DIR/audio_boot.txt.tmp" 2>/dev/null && \
            mv "$TELEMETRY_DIR/audio_boot.txt.tmp" "$TELEMETRY_DIR/audio_boot.txt" 2>/dev/null
    fi
}

card_present() {
    ls /proc/asound 2>/dev/null | grep -q "^card[0-9]"
}

notify() {
    # Best-effort mako toast (service env has no session vars — derive them).
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
    DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
    export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
    dbus-send --session --print-reply \
        --dest=org.freedesktop.Notifications \
        /org/freedesktop/Notifications \
        org.freedesktop.Notifications.Notify \
        "string:ETK Pitstop" uint32:0 string: \
        "string:$1" "string:$2" \
        array:string: dict:string:variant: int32:8000 >/dev/null 2>&1
}

# --- SETTLE: give the normal probe its window (card binds ~4s on good
# boots; the deferred-pending report fires ~20s on bad ones). ---------
UP=$(cut -d. -f1 /proc/uptime 2>/dev/null)
case "$UP" in ''|*[!0-9]*) UP=0 ;; esac
if [ "$UP" -lt 25 ]; then
    sleep $((25 - UP))
fi

# --- HEALTHY PATH: silent exit. ---------------------------------------
if card_present; then
    set_status "ok"
    exit 0
fi

# --- FAILED BOOT: detect, log, revive. --------------------------------
log_tw "no sound card at boot (deferred-probe race) — attempting revive"

ATTEMPT=1
while [ "$ATTEMPT" -le 3 ]; do
    for dev in $AUDIO_DEVS; do
        [ -e "/sys/bus/platform/devices/$dev" ] || continue
        [ -e "/sys/bus/platform/devices/$dev/driver" ] && continue
        echo "$dev" > /sys/bus/platform/drivers_probe 2>/dev/null
    done
    sleep 3
    if card_present; then
        log_tw "revive OK on attempt $ATTEMPT (card registered)"
        set_status "revived"
        notify "AUDIO REVIVED" "Sound card was dead at boot (probe race); ETK re-probed the chain. Audio is live."
        exit 0
    fi
    ATTEMPT=$((ATTEMPT + 1))
    sleep 7
done

log_tw "revive FAILED after 3 attempts — audio dead this boot (reboot to retry)"
set_status "dead"
notify "AUDIO DEAD THIS BOOT" "Sound card probe failed and revive did not stick. Reboot to roll again."
exit 1
