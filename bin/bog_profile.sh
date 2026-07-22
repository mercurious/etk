#!/bin/sh
# ============================================================================
# bog_profile.sh — the BOG PROFILER: perf-sample the live emulator through a
# pack-racing bog section and bank SYMBOLIZED stacks, so the contended lock/
# sync primitive gets a NAME (the post-GRID lane: placement falsified, the
# wall is inside RPCS3 — futex / atomic-wait / vm::reservation / FIFO spin).
#
# GEMINI IMMUTABLE RULE: fail-silent EVERYWHERE — chord-fired from input_d.py,
# so nothing here may ever block the input loop, break the R3 panic path, or
# kill a race. No game / no perf / already-sampling => clean silent exit 0.
#
# CRITICAL DESIGN FACT — symbolize AT CAPTURE TIME: the emulator binary lives
# in a dwarfs AppImage mount (/tmp/.mount_*) that VANISHES when the session
# ends. `perf script` run later resolves nothing. So the post-process step
# (nice -19, after the sampled window so it cannot pollute the sample) runs
# immediately, while the mount is alive.
#
# Trigger:  input_d.py chord R1 + DPAD-Down (in-game only by construction;
#           NOT SELECT — that is the camera toggle, operator 2026-07-10)
#           or manual: bog_profile.sh [SECONDS]   (default $BOG_PROFILE_SECS or 30)
# Output:   $TELEMETRY_DIR/perf_samples/bog_<epoch>.{perf.data,stacks.gz,summary.txt,meta}
#           pruned to the newest 6 sets. Host-side sweep: cockpit skill
#           scripts/grab_bog_sample.sh ("grab sample").
# Feedback: mako toast on COMPLETION only (a start-toast would render during
#           the sampled window and pollute the measurement). Fail-silent.
# ============================================================================
[ -f /storage/games-internal/roms/etk/scripts/env.sh ] && . /storage/games-internal/roms/etk/scripts/env.sh 2>/dev/null

# Duration precedence: arg > env > etk.conf BOG_PROFILE_SECS > 30. The etk.conf
# read is direct (not via env.sh) because the chord launches us under plain sh
# and env.sh's operator-config block is BASH_SOURCE-tied. For section-mapping
# stints (e.g. Eiger lock-vs-slush laps) set BOG_PROFILE_SECS=15 in etk.conf —
# a 15s window fits one track section; 30s spans several.
DUR="${1:-${BOG_PROFILE_SECS:-}}"
if [ -z "$DUR" ]; then
    DUR=$(sed -n 's/^ *\(export \)\{0,1\}BOG_PROFILE_SECS=["'\'']\{0,1\}\([0-9]\{1,\}\).*/\2/p' \
        /storage/games-internal/roms/etk/etk.conf 2>/dev/null | head -1)
fi
case "$DUR" in ''|*[!0-9]*) DUR=30 ;; esac
TDIR="${TELEMETRY_DIR:-/storage/games-internal/roms/etk/etk_telemetry}"
OUTDIR="$TDIR/perf_samples"
SHM="${SHM_DIR:-/dev/shm/etk_shm}"
LOCK="$SHM/bog_profile.pid"

command -v perf >/dev/null 2>&1 || exit 0

# Debounce: one sample at a time (a double chord press must not stack two
# perf sessions onto a rig that is already bogging).
if [ -f "$LOCK" ]; then
    OLD=$(cat "$LOCK" 2>/dev/null)
    [ -n "$OLD" ] && [ -d "/proc/$OLD" ] && exit 0
fi
echo $$ > "$LOCK" 2>/dev/null

# Emulator pid via cmdline walk (PROC-discovery law: never pgrep).
EPID=""
for c in /proc/[0-9]*/cmdline; do
    [ -r "$c" ] || continue
    exe=$(tr '\0' '\n' < "$c" 2>/dev/null | head -n 1)
    case "$exe" in *AppRun.wrapped) EPID="${c%/cmdline}"; EPID="${EPID#/proc/}" ;; esac
done
[ -z "$EPID" ] && { rm -f "$LOCK" 2>/dev/null; exit 0; }

mkdir -p "$OUTDIR" 2>/dev/null
EP=$(date +%s)
OUT="$OUTDIR/bog_$EP"

# --- SAMPLE the bog window (199 Hz, callgraphs, whole emulator process) ---
# DDU progress marker (operator-approved 2026-07-10): while this file exists and
# is inside its window, mango_bridge swaps the VAULT gauge for the sampling
# gauge `§~~~··` (5 progress slots over $DUR). Removed the instant the record
# window closes; the bridge also self-heals on a stale marker (elapsed >= dur).
echo "$(date +%s) $DUR" > "$SHM/bog_sample" 2>/dev/null
perf record -F 199 -g -p "$EPID" -o "$OUT.perf.data" -- sleep "$DUR" >/dev/null 2>&1
rm -f "$SHM/bog_sample" 2>/dev/null

# --- SYMBOLIZE NOW (mount-alive window), nice'd so it can't cause a bog ---
if [ -s "$OUT.perf.data" ]; then
    nice -n 19 perf script -i "$OUT.perf.data" 2>/dev/null | gzip > "$OUT.stacks.gz" 2>/dev/null
    nice -n 19 perf report --stdio --no-children -i "$OUT.perf.data" 2>/dev/null | head -400 > "$OUT.summary.txt" 2>/dev/null
fi

# --- META sidecar: joins the sample to its ledger row ---
# fps_end: the perfstat SHM line at window close (needs Perf Overlay High) —
# self-labels the sample bog-vs-perfect for lap mapping without the timeline
# join. The perf_logs timeline remains the full-context second witness.
# session_start: the Sentry's ignition epoch — the sample's ledger row is the
# one where (epoch − duration_s) == session_start. The ledger epoch is stamped
# by the postmortem at session END; bucketing samples into [row_epoch,
# next_row) windows joins them one session late (the 2026-07-22 false
# "identity misfire": four PKG-control samples misread as mid-ISO fallbacks).
{
    echo "epoch=$EP"
    echo "duration_s=$DUR"
    echo "session_start=$(cat "$SHM/session_start.txt" 2>/dev/null)"
    echo "game=$(cat "$SHM/active_id.txt" 2>/dev/null)"
    echo "tune=$(cat "$TDIR/active_tune.txt" 2>/dev/null)"
    echo "pwr=$(sed -n 's/^# pwr=//p' /storage/etk-power/profile 2>/dev/null | head -1)"
    echo "perf_data_bytes=$(stat -c %s "$OUT.perf.data" 2>/dev/null)"
    echo "fps_end=$(head -1 /dev/shm/rpcs3_perf_stat 2>/dev/null | tr ' ' ',')"
} > "$OUT.meta" 2>/dev/null

# --- PRUNE to the newest 12 sample sets (a lap-mapping stint banks many;
#     ~6MB/set => ~72MB ceiling, fine for the card) ---
ls -1t "$OUTDIR"/bog_*.meta 2>/dev/null | tail -n +13 | while read -r m; do
    b="${m%.meta}"
    rm -f "$b.meta" "$b.perf.data" "$b.stacks.gz" "$b.summary.txt" 2>/dev/null
done

# --- COMPLETION toast (mako, fail-silent; env derived per manifest) ---
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
dbus-send --session --dest=org.freedesktop.Notifications \
    /org/freedesktop/Notifications org.freedesktop.Notifications.Notify \
    "string:ETK Pitstop" uint32:0 string: \
    "string:BOG SAMPLE BANKED" "string:${DUR}s @ bog_$EP — say 'grab sample'" \
    array:string: dict:string:variant: int32:4000 >/dev/null 2>&1

rm -f "$LOCK" 2>/dev/null
exit 0
