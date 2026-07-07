#!/bin/sh
# ==========================================================
# ETK AUDIO WATCHDOG (v0.4.0 - DEPRECATED: KERNEL FIX VALIDATION HARNESS + BACKSTOP)
# ==========================================================
# ** DEPRECATED 2026-07-06 — the bug is now fixed in the kernel. **
# The SM8250 probe race (~1 in 4 boots: q6afe AFE clock-vote rejected
# while ADSP audio_pd settles → va_macro hard-fails → whole LPASS
# chain parks in deferred-probe forever → silent boot) is fixed at
# the root by ROCKNIX-GTK kernel patch 0002-q6afe-vote-probe-race
# (KERNEL.rocknix-gtk-20260706-audiofix0+): the dropped ADSP error
# reply now wakes the vote waiter, and the vote retries in place
# (250ms period, 15s bound) until the ADSP is ready — the probe
# succeeds on the first boot pass, no userspace revive needed.
# A kernel-side recovery is announced in dmesg as
#   "etk: AFE vote (N) recovered after N retries".
#
# This script's remaining jobs, until the kernel fix has absorbed
# N>=3 NATURAL races across cold boots (the race is a ~1-in-4 coin
# flip — only cold-boot mileage validates it):
#   1. VALIDATION TRIPWIRE: log when the kernel fix engaged (turns
#      every raced boot into a validation datapoint).
#   2. STATUS WRITER: audio_boot.txt feeds the ledger snd= column.
#   3. BACKSTOP: the old drivers_probe revive still runs if the card
#      is missing — on an audiofix kernel that firing means the
#      kernel fix REGRESSED (or a pre-fix kernel is booted) and is
#      logged as such.
# RETIREMENT PLAN (after validation): remove install.sh STEP 6.57
# (which writes etk-audio-watchdog.service) + the uninstall.sh
# removal block + this script, and move the audio_boot.txt status
# write into the Sentry or drop snd= attribution to
# card-presence-only in session_postmortem.sh.
# History: userspace revive validated live 2026-07-02/03; see
# AI_MANIFEST.md "ROCKNIX AUDIO STACK" + dossiers/AudioCampaign_20260702.md §1.
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

# --- SETTLE: give the normal probe its window (card binds ~3.9s on good
# boots). 8s, NOT 25: a real failed boot 2026-07-03 proved the launch race —
# the operator started a game before the old 25s settle + revive finished,
# and RPCS3/cubeb PINNED the dummy sink for the whole 468s session (ledger
# snd=dummy) even though the card was revived moments later. A revive that
# lands by ~11s beats any human launch. Poking a still-probing good boot is
# a harmless no-op (drivers_probe on bound devices does nothing). ---------
UP=$(cut -d. -f1 /proc/uptime 2>/dev/null)
case "$UP" in ''|*[!0-9]*) UP=0 ;; esac
if [ "$UP" -lt 8 ]; then
    sleep $((8 - UP))
fi

# --- HEALTHY PATH: silent exit — UNLESS the kernel fix saved this boot,
# which is a validation datapoint worth one tripwire line. ---------------
if card_present; then
    KFIX=$(dmesg 2>/dev/null | grep "etk: AFE vote" | tail -1)
    if [ -n "$KFIX" ]; then
        log_tw "kernel probe-race fix ENGAGED this boot:${KFIX#*]}"
    fi
    set_status "ok"
    exit 0
fi

# --- NO CARD AT 8s: the kernel's in-place vote retry (patch 0002) is
# bounded at 15s from the va_macro probe (~1s), so it may still be
# in flight. Grace-poll to ~20s uptime before declaring it failed —
# exit the moment the card lands (kernel fix win, just late). -----------
while :; do
    UP=$(cut -d. -f1 /proc/uptime 2>/dev/null)
    case "$UP" in ''|*[!0-9]*) UP=20 ;; esac
    [ "$UP" -ge 20 ] && break
    sleep 1
    if card_present; then
        KFIX=$(dmesg 2>/dev/null | grep "etk: AFE vote" | tail -1)
        log_tw "kernel probe-race fix ENGAGED (late, card at ${UP}s):${KFIX#*]}"
        set_status "ok"
        exit 0
    fi
done

# --- FAILED BOOT: detect, log, revive (BACKSTOP). On an audiofix kernel
# (patch 0002) reaching this line means the kernel fix regressed or a
# pre-fix kernel is booted — say so, then revive as before. -------------
log_tw "no sound card at boot (deferred-probe race) — kernel fix ABSENT or FAILED; attempting userspace revive"

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
