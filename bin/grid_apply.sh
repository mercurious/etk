#!/bin/sh
# ============================================================================
# grid_apply.sh — GRID mode: big.LITTLE thread-affinity applier (SM8250)
# The POWER-tab `grid` knob's engine. Spec: dossiers/GridModeAffinity_Spec_20260710.md
# Verdict it acts on: the pack bog is thread CONTENTION, not saturation
# (perfstat 2026-07-10: fps 14 with GPU 76% / CPU 68% — nothing capped).
#
# GEMINI IMMUTABLE RULE: fail-silent EVERYWHERE — this script must never be able
# to break ignition, the R3 panic path (input_d.py is a separate process and is
# never touched), or the Sentry. It only ever masks the EMULATOR's own threads;
# `off` (or any unknown rung) resets them to 0-7. POSIX/BusyBox-safe; taskset is
# util-linux (verified on-rig 2026-07-10).
#
# Usage:  grid_apply.sh [off|A|B|C]
#         no arg = read `# knob grid <v>` from the POWER profile (absent => off)
# Callers: Pitstop POWER tab (_power_apply, live) + thermal_d.sh (re-assert at
#         TICK 1 and every 30 ticks, so late-spawned SPU workers get collected).
# Marker:  appends "ep=<epoch> grid=<rung> threads=<n>" to $SHM_DIR/grid_mark —
#         the perfstat timeline (perf_logs/) splits at these epochs for the
#         within-session A/B.
#
# Rungs (SM8250: silvers cpu0-3 @1.8GHz, golds cpu4-6 @2.42, prime cpu7 @2.84):
#   A "quarantine"    noise->silvers; hot set left 0-7 (EAS places it on clean bigs)
#   B "assigned pits" A + rsx->prime, PPU->golds 4-5, SPU->golds 4-6, cellAudio->silvers
#   C "silver assist" B, but SPU may also use silvers (more cores at lower clock)
#   off               everything -> 0-7 (scheduler decides; the stock state)
# ============================================================================
# Guarded source: POSIX `.` ABORTS a non-interactive shell if the file is absent
# (fail-silent law: this script must degrade to defaults, never die at the top).
[ -f /storage/games-internal/roms/etk/scripts/env.sh ] && . /storage/games-internal/roms/etk/scripts/env.sh 2>/dev/null

PROFILE=/storage/etk-power/profile
RUNG="$1"
[ -z "$RUNG" ] && RUNG=$(sed -n 's/^# knob grid //p' "$PROFILE" 2>/dev/null | head -1)
[ -z "$RUNG" ] && RUNG=off

# Profile-driven call (thermal_d, no arg) with rung=off is a TRUE no-op — the
# stock scheduler state needs no enforcement, and this avoids 60s mask-churn +
# grid_mark spam. An EXPLICIT `grid_apply.sh off` (Pitstop press) still does the
# full reset + mark, which the within-session A/B needs to record the flip-back.
[ -z "$1" ] && [ "$RUNG" = "off" ] && exit 0

ALL=0-7; SILVER=0-3
case "$RUNG" in
    A) M_RSX=$ALL;  M_PPU=$ALL; M_SPU=$ALL; M_AUD=$ALL;    M_NOISE=$SILVER ;;
    B) M_RSX=7;     M_PPU=4-5;  M_SPU=4-6;  M_AUD=$SILVER; M_NOISE=$SILVER ;;
    C) M_RSX=7;     M_PPU=4-5;  M_SPU=$ALL; M_AUD=$SILVER; M_NOISE=$SILVER ;;
    *) M_RSX=$ALL;  M_PPU=$ALL; M_SPU=$ALL; M_AUD=$ALL;    M_NOISE=$ALL; RUNG=off ;;
esac

# Emulator pid via cmdline walk (PROC-discovery law: never pgrep -f — it
# self-matches; never pgrep -x AppRun.wrapped — proven unreliable).
EPID=""
for c in /proc/[0-9]*/cmdline; do
    [ -r "$c" ] || continue
    exe=$(tr '\0' '\n' < "$c" 2>/dev/null | head -n 1)
    case "$exe" in *AppRun.wrapped) EPID="${c%/cmdline}"; EPID="${EPID#/proc/}" ;; esac
done
[ -z "$EPID" ] && exit 0

# Classify by comm PREFIX (comm truncates at 15 chars — never match full names).
N=0
for t in /proc/"$EPID"/task/*; do
    comm=$(cat "$t/comm" 2>/dev/null) || continue
    tid="${t##*/}"
    case "$comm" in
        rsx::*)     m=$M_RSX ;;
        PPU\[*)     m=$M_PPU ;;
        SPU*)       m=$M_SPU ;;
        cellAudio*) m=$M_AUD ;;
        *)          m=$M_NOISE ;;
    esac
    taskset -pc "$m" "$tid" >/dev/null 2>&1 && N=$((N + 1))
done

echo "ep=$(date +%s) grid=$RUNG threads=$N" >> "${SHM_DIR:-/dev/shm/etk_shm}/grid_mark" 2>/dev/null
exit 0
