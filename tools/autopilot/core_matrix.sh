#!/bin/bash
# ==========================================================
# tools/autopilot/core_matrix.sh -- cross-title core validation on car8 (lift)
# ==========================================================
# Runs a list of same-shaped trials, each one title on one core: pins the core
# for that title in car8's core_map.tsv (launch-cadence, no reboot), cools the
# car, runs ONE trial through tools/onboard/dyno_ab.sh off (gt5_trial.py, its
# full-log grab and its R3 recover), then records what the ledger and cores/
# say. car8 only: a pin is a car8 setting under lift access (manual §1.1).
# The original core_map.tsv is restored when the matrix ends (or dies).
#
#   tools/autopilot/core_matrix.sh OUTROOT SERIAL:CORE:MACRO:N [...]
#     CORE  = a file in car8's emulators/ (e.g. rpcs3-etk_gtk-edition-0.10.0_…AppImage)
#     MACRO = tools/autopilot/macros/<name>.macro
#     N     = runs 1..N, or A-B to resume a spec at run A (run names rA..rB never collide)
#   Rounds interleave: run 1 of every spec, then run 2 of every spec, ...
#   MATRIX_COOL_C (default 50) / MATRIX_COOL_MAX (default 900 s): cool below C before a launch.
#
# OUTROOT/matrix.tsv: one row per run -- name serial core verdict(exit) ledger-status
# ledger-dur gpu-fault new-cores. Verdict codes are gt5_trial's (0 SURVIVED, 1 PANIC, 3 CRASH,
# 5 FREEZE, 6 EXITED ...). A new file in cores/ beside a CLEAN row is a crash (triage T3).
# ==========================================================
set -u
ROOT="${1:?OUTROOT}"; shift
[ $# -ge 1 ] || { echo "need at least one SERIAL:CORE:MACRO:N" >&2; exit 2; }
cd "$(dirname "$0")/../.." || exit 1
HOST=etk-rig-usb
MAP=/storage/roms/etk/emulators/core_map.tsv
COOL_C="${MATRIX_COOL_C:-50}"; COOL_MAX="${MATRIX_COOL_MAX:-900}"
mkdir -p "$ROOT"
LOG="$ROOT/matrix.log"; TSV="$ROOT/matrix.tsv"
log() { echo "$(date +%T) $*" | tee -a "$LOG"; }
rig() { ssh -o ConnectTimeout=8 -o BatchMode=yes "$HOST" "$@"; }

[ "$(rig cat /storage/.etk/car 2>/dev/null)" = "car8" ] || { log "REFUSED: $HOST is not car8"; exit 2; }
STAMP=$(date +%Y%m%d-%H%M)
rig "cp $MAP $MAP.pre-matrix-$STAMP" || { log "cannot back up core_map"; exit 2; }
log "core_map backed up as core_map.tsv.pre-matrix-$STAMP"
restore() { rig "cp $MAP.pre-matrix-$STAMP $MAP" && log "core_map restored"; }
trap restore EXIT

wait_car() {   # up, kit garage answering, no game running
    local i
    for i in $(seq 1 60); do
        if python3 tools/pitlink/pitlink.py garage running 2>/dev/null | grep -q '"running": null'; then return 0; fi
        sleep 10
    done
    return 1
}
cool() {
    local t0 t; t0=$(date +%s)
    while :; do
        t=$(rig 'cat /sys/class/thermal/thermal_zone*/temp' 2>/dev/null | sort -n | tail -1)
        t=$(( ${t:-99000} / 1000 ))
        [ "$t" -lt "$COOL_C" ] && { log "cool: ${t} C"; return; }
        [ $(( $(date +%s) - t0 )) -ge "$COOL_MAX" ] && { log "cool: still ${t} C after ${COOL_MAX}s, going"; return; }
        [ $(( ($(date +%s) - t0) % 60 )) -lt 20 ] && log "cool: ${t} C, waiting for < ${COOL_C} C"
        sleep 20
    done
}
pin() {   # SERIAL CORE -> core_map row (replace or append)
    rig "f=$MAP; [ -f /storage/roms/etk/emulators/$2 ] || exit 3; awk -F'\t' -v s='$1' '\$1!=s' \$f > \$f.tmp && printf '%s\t%s\n' '$1' '$2' >> \$f.tmp && mv \$f.tmp \$f && awk -F'\t' -v s='$1' '\$1==s' \$f"
}

first() { case "$1" in *-*) echo "${1%-*}" ;; *) echo 1 ;; esac; }
last() { echo "${1#*-}"; }
MAXN=0; MINN=999
for spec in "$@"; do n=${spec##*:}
    [ "$(last "$n")" -gt "$MAXN" ] && MAXN=$(last "$n"); [ "$(first "$n")" -lt "$MINN" ] && MINN=$(first "$n"); done
[ -f "$TSV" ] || printf 'name\tserial\tcore\ttrial_rc\tledger_status\tledger_dur\tgpu_fault\tnew_cores\n' > "$TSV"

for round in $(seq "$MINN" "$MAXN"); do
    for spec in "$@"; do
        IFS=: read -r SERIAL CORE MACRO N <<< "$spec"
        [ "$round" -ge "$(first "$N")" ] && [ "$round" -le "$(last "$N")" ] || continue
        short=$(echo "$CORE" | sed -E 's/^rpcs3-(etk_gtk-edition-)?//; s/_armsx3.*//')
        NAME="r${round}-${SERIAL}-${short}"
        wait_car || { log "$NAME: car not ready after 10 min, matrix STOPS"; exit 4; }
        cool
        log "$NAME: pin $(pin "$SERIAL" "$CORE" | tr '\t' ' ')" || { log "$NAME: pin failed"; exit 3; }
        cores_before=$(rig 'ls /storage/roms/etk/cores/ 2>/dev/null' | sort)
        last_before=$(rig "tail -n1 /storage/roms/etk/etk_telemetry/sessions.tsv | cut -f1")
        log "$NAME: start"
        DYNO_GAME="$SERIAL" DYNO_MACRO="$MACRO" tools/onboard/dyno_ab.sh off "$NAME" "$ROOT"
        rc=$?
        wait_car || { log "$NAME: car not back after 10 min (rc $rc), matrix STOPS"; exit 4; }
        sleep 15   # the postmortem stamps the row on RUNNING->IDLE
        row=$(rig "awk -F'\t' '\$1>$last_before' /storage/roms/etk/etk_telemetry/sessions.tsv | tail -n1")
        newc=$(comm -13 <(echo "$cores_before") <(rig 'ls /storage/roms/etk/cores/ 2>/dev/null' | sort) | tr '\n' ' ')
        lstat=$(echo "$row" | cut -f5); ldur=$(echo "$row" | cut -f2); gf=$(echo "$row" | cut -f24)
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$NAME" "$SERIAL" "$short" "$rc" "${lstat:--}" "${ldur:--}" "${gf:--}" "${newc:--}" >> "$TSV"
        log "$NAME: trial rc $rc, ledger ${lstat:-none} ${ldur:-}s, gpu ${gf:--}, new cores ${newc:-none}"
    done
done
log "matrix done"
