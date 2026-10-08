#!/bin/bash
# ==========================================================
# REFRESH PROBE — what rate is the panel REALLY scanning at? (host-side, read-only)
# ==========================================================
# Three witnesses, weakest to strongest:
#   1. sway      — the mode the compositor asked for (swaymsg get_outputs)
#   2. DRM       — the mode the kernel committed (debugfs dri/*/state, "mode:" vrefresh)
#   3. hardware  — DPU interrupt rates from debugfs dri/*/debug/core_irq, sampled over a
#                  window. On a command-mode panel (Flip2 12GB Visionox VTDR6130) the TE
#                  read-pointer IRQ (cb ...cmd_te_rd_ptr_irq) fires once per REAL panel
#                  refresh; pp_tx_done counts frames actually pushed down the link.
# The verdict trusts witness 3. TE only runs while vblank is enabled, so keep motion on
# the screen during the window (scroll ES, run a game) or the probe reports IDLE.
#
# Nothing is written on the unit: ssh runs cat/grep/swaymsg queries only. No push-list
# entry — nothing lives on the unit. Log: state/refresh_probe/<utc>_<target>.txt
# Dossier: dossiers/Flip2_12GB_PanelProbe_20261007.md
#
# Usage:
#   tools/refresh_probe.sh <ssh-target> [seconds=10]     e.g. root@flip2-12g.local
#   tools/refresh_probe.sh --parse <saved-log>           re-judge a saved capture
# ==========================================================
set -u
ETK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

judge() {   # $1 = capture file; prints the evidence table + verdict
    awk '
    /^@@ T0$/ {phase=0; next}  /^@@ T1$/ {phase=1; next}  /^@@ /{phase=-1}
    /^@@ WINDOW / {win=$3}
    /^@@ MODEL /  {sub(/^@@ MODEL /,""); model=$0}
    /^@@ PANEL /  {sub(/^@@ PANEL /,""); panel=$0}
    /^@@ KERNEL / {kernel=$3}
    /^@@ DEBUGFS / {dfs=$3}
    /^SWAY / && /Current mode/ {sub(/.*Current mode: /,""); sway=$0}
    /^SWAY / && /"current_mode": \{/ {incm=1; next}               # JSON form (older captures)
    incm && /"width":/   {w=$0; gsub(/[^0-9]/,"",w)}
    incm && /"height":/  {h=$0; gsub(/[^0-9]/,"",h)}
    incm && /"refresh":/ {r=$0; gsub(/[^0-9]/,"",r); sway=sprintf("%sx%s @ %.3f Hz", w, h, r/1000); incm=0}
    /^DRMSTATE / && /mode: "/ { s=$0; sub(/.*mode: "[^"]*": /,"",s); split(s,f," "); if (f[1]+0>0) { drm=f[1]" Hz (clock "f[2]" kHz)"; drmhz=f[1]+0 } }
    phase>=0 && /IRQ=\[/ {
        key=$0; sub(/ count:[0-9]+/,"",key)
        c=$0; sub(/.*count:/,"",c); sub(/ .*/,"",c)
        cnt[phase,key]=c; keys[key]=1
    }
    END {
        printf "unit     : %s\n panel   : %s\n kernel  : %s\n", model, panel, kernel
        printf "witness 1  sway  : %s\n", (sway==""?"(no sway socket / not running)":sway)
        printf "witness 2  DRM   : %s\n", (drm==""?"(no active mode in debugfs state)":drm)
        if (dfs!="ok") { printf "witness 3  HW    : debugfs not readable (%s) — mount -t debugfs none /sys/kernel/debug\nVERDICT: UNMEASURED\n", dfs; exit }
        if (win+0<=0) { print "VERDICT: UNMEASURED (no window)"; exit }
        te=-1; pp=-1
        for (k in keys) {
            d=cnt[1,k]-cnt[0,k]; hz=d/win
            name=k; sub(/.*cb:/,"",name)
            if (d>0) printf "witness 3  HW    : %-48s %8.2f /s\n", name, hz
            if (k ~ /rd_ptr/) te=(hz>te?hz:te)
            if (k ~ /pp_tx_done|pp_done/) pp=(hz>pp?hz:pp)
        }
        if (te<0) { print "VERDICT: NO TE IRQ registered — not a command-mode encoder, or names changed; read core_irq raw in the log"; exit }
        if (te<1) { print "VERDICT: IDLE — vblank off during the window; re-run with motion on screen"; exit }
        rate = (te>=114 && te<=126) ? "120 Hz" : (te>=57 && te<=63) ? "60 Hz" : sprintf("%.1f Hz (neither 60 nor 120)", te)
        if (drmhz>0 && (te/drmhz<0.95 || te/drmhz>1.05)) printf "WITNESSES DISAGREE: DRM committed %d Hz but the panel TE runs %.2f/s — the panel is pacing itself, trust TE\n", drmhz, te
        printf "VERDICT: PANEL SCANNING AT %s — hardware TE %.2f/s over %ss; frames pushed %.2f/s\n", rate, te, win, (pp<0?0:pp)
    }' "$1"
}

if [ "${1:-}" = "--parse" ]; then
    [ -r "${2:-}" ] || { echo "usage: $0 --parse <capture>"; exit 2; }
    judge "$2"; exit 0
fi

TARGET="${1:-}"; WIN="${2:-10}"
[ -n "$TARGET" ] || { sed -n '/^# Usage:/,/^# ====/p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
case "$WIN" in ''|*[!0-9]*) echo "seconds must be an integer"; exit 2;; esac

OUT_DIR="$ETK_ROOT/state/refresh_probe"; mkdir -p "$OUT_DIR"
CAP="$OUT_DIR/$(date -u +%Y%m%dT%H%M%SZ)_${TARGET##*@}.txt"

echo "refresh_probe: sampling $TARGET for ${WIN}s — keep motion on the screen now"
# BusyBox-safe remote side: reads only.
ssh -o ConnectTimeout=8 "$TARGET" "WIN=$WIN sh -s" > "$CAP" 2>&1 <<'REMOTE'
echo "@@ WINDOW $WIN"
echo "@@ MODEL $(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
echo "@@ PANEL $(tr '\0' ' ' < /proc/device-tree/soc@0/display-subsystem@ae00000/dsi@ae94000/panel@0/compatible 2>/dev/null)"
echo "@@ KERNEL $(uname -r)"
for c in /sys/class/drm/card*-DSI-*; do [ -e "$c/modes" ] && echo "MODES $c: $(tr '\n' ' ' < "$c/modes")"; done
SOCK=$(ls /run/*/sway-ipc.*.sock /var/run/*/sway-ipc.*.sock /run/user/*/sway-ipc.*.sock 2>/dev/null | head -1)
[ -n "$SOCK" ] && swaymsg -p -s "$SOCK" -t get_outputs 2>&1 | sed 's/^/SWAY /'
IRQ=""; STATE=""
for d in /sys/kernel/debug/dri/*; do
    [ -r "$d/debug/core_irq" ] && IRQ="$d/debug/core_irq"
    [ -r "$d/state" ] && grep -q 'DSI' "$d/state" 2>/dev/null && STATE="$d/state"
done
if [ -z "$IRQ" ]; then
    if grep -q ' /sys/kernel/debug debugfs' /proc/mounts; then echo "@@ DEBUGFS no-core_irq"; else echo "@@ DEBUGFS not-mounted"; fi
else
    echo "@@ DEBUGFS ok"
fi
[ -n "$STATE" ] && sed 's/^/DRMSTATE /' "$STATE"
grep -i mdss /proc/interrupts | sed 's/^/PROCIRQ0 /'
echo "@@ T0"; [ -n "$IRQ" ] && cat "$IRQ"
sleep "$WIN"
echo "@@ T1"; [ -n "$IRQ" ] && cat "$IRQ"
echo "@@ END"
grep -i mdss /proc/interrupts | sed 's/^/PROCIRQ1 /'
REMOTE
rc=$?
[ $rc -eq 0 ] || { echo "refresh_probe: ssh failed (rc=$rc) — capture: $CAP"; tail -3 "$CAP"; exit 1; }
judge "$CAP" | tee -a "$CAP.verdict"
echo "capture: $CAP"
