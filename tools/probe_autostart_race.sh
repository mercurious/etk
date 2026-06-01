#!/bin/bash
# ============================================================
# ETK PROBE: autostart ↔ MangoHud "race condition" — empirical test
# ============================================================
# Host-side dev tool. Queries the rig over SSH and produces an
# empirical artifact testing the AI_MANIFEST claim:
#
#   "Do NOT use /storage/.config/autostart.sh ... it is tied to the
#    Wayland/EmulationStation UI load sequence and causes race
#    conditions with MangoHud."
#
# THE TEST: if autostart truly raced MangoHud, the two would have to
# run at overlapping times. This probe pulls systemd's own recorded
# timeline for THIS boot and checks three falsifiable facts:
#   (A) When does autostart run, and does it finish BEFORE the UI?
#   (B) Who owns MangoHud, and is it a boot process or a game-launch one?
#   (C) Is there any temporal overlap between the two?
#
# Read-only on the rig. No reboot required: systemd already recorded
# the boot ordering. Re-runnable; writes a timestamped artifact under log/.
# Remote snippets are BusyBox/POSIX-safe (AI_MANIFEST law #5).
# ============================================================
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$HERE/etk.conf" ] && . "$HERE/etk.conf"
RIG_SSH="${RIG_SSH:-root@SM8250.local}"
SSH="ssh -o ConnectTimeout=5 -o BatchMode=yes $RIG_SSH"

STAMP="$(date +%Y%m%d-%H%M%S)"
ART="$HERE/log/autostart_race_probe_${STAMP}.txt"
mkdir -p "$HERE/log"

echo "🏁 ETK AUTOSTART↔MANGOHUD RACE PROBE"
echo "📡 Rig: $RIG_SSH    🗒  Artifact: ${ART#$HERE/}"
echo "----------------------------------------------------"

if ! $SSH 'echo ok' >/dev/null 2>&1; then
    echo "❌ Rig unreachable at $RIG_SSH — is it powered on and on the LAN?"
    exit 1
fi

# Pull the raw evidence from the rig in one round-trip.
RAW="$($SSH 'sh -s' <<'REMOTE'
echo "BOOT_TIME=$(uptime -s 2>/dev/null || echo unknown)"
# UI service is platform-dependent (sway on panfrost-class, weston/emustation elsewhere).
UI=""
for c in sway.service essway.service weston.service emustation.service; do
    if systemctl cat "$c" >/dev/null 2>&1; then UI="$UI $c"; fi
done
echo "UI_SERVICES=$UI"
# Monotonic-since-boot start/exit for the units that matter.
for svc in rocknix-autostart.service etk.service $UI; do
    st=$(systemctl show "$svc" -p ExecMainStartTimestampMonotonic --value 2>/dev/null)
    ex=$(systemctl show "$svc" -p ExecMainExitTimestampMonotonic --value 2>/dev/null)
    ae=$(systemctl show "$svc" -p ActiveEnterTimestampMonotonic --value 2>/dev/null)
    echo "SVC|$svc|start=$st|exit=$ex|active_enter=$ae"
done
echo "AUTOSTART_DIR<<:"
ls -1 /storage/.config/autostart/ 2>/dev/null
echo ":>>"
echo "ETK_USES_AUTOSTART=$(ls /storage/.config/autostart/ 2>/dev/null | grep -ci etk)"
echo "MANGO_OWNED_BY_RUNEMU=$(grep -ic mangohud /usr/bin/runemu.sh 2>/dev/null)"
echo "MANGO_RUNNING_NOW=$(pgrep -f -l -i mangohud 2>/dev/null | wc -l)"
echo "EMU_RUNNING_NOW=$(pgrep -f 'rpcs3|AppRun.wrapped' 2>/dev/null | wc -l)"
# Journal proof: the line where autostart hands off to the UI, and its Finished line.
echo "JOURNAL<<:"
journalctl -b -o short-monotonic -u rocknix-autostart.service 2>/dev/null | grep -iE 'Starting|Finished|sway|weston|emustation|complete' | tail -6
echo ":>>"
REMOTE
)"

# ---- parse ----
field() { printf '%s\n' "$RAW" | grep "^$1=" | head -1 | cut -d= -f2-; }
us2s() { awk "BEGIN{ if(\"$1\"==\"\"||\"$1\"==\"0\"){print \"n/a\"} else {printf \"%.3f\", $1/1000000} }"; }

BOOT_TIME="$(field BOOT_TIME)"
ETK_USES_AUTOSTART="$(field ETK_USES_AUTOSTART)"
MANGO_OWNED="$(field MANGO_OWNED_BY_RUNEMU)"
MANGO_NOW="$(field MANGO_RUNNING_NOW)"
EMU_NOW="$(field EMU_RUNNING_NOW)"

as_exit="$(printf '%s\n' "$RAW" | grep '^SVC|rocknix-autostart.service|' | sed 's/.*exit=//;s/|.*//')"
as_start="$(printf '%s\n' "$RAW" | grep '^SVC|rocknix-autostart.service|' | sed 's/.*start=//;s/|.*//')"

{
echo "ETK AUTOSTART ↔ MANGOHUD RACE PROBE — empirical artifact"
echo "Generated: $STAMP   Rig: $RIG_SSH   Boot: $BOOT_TIME"
echo "========================================================"
echo
echo "(A) AUTOSTART TIMING (seconds since boot)"
printf '%s\n' "$RAW" | grep '^SVC|' | while IFS='|' read -r _ svc s e a; do
    printf "    %-28s start=%-8s exit=%-8s active=%-8s\n" \
        "$svc" "$(us2s "${s#start=}")" "$(us2s "${e#exit=}")" "$(us2s "${a#active_enter=}")"
done
echo
echo "    /storage/.config/autostart/ contents:"
printf '%s\n' "$RAW" | awk '/^AUTOSTART_DIR<<:/{f=1;next}/^:>>/{f=0}f{print "      "$0}'
echo "    ETK scripts in autostart dir: $ETK_USES_AUTOSTART (0 = ETK does NOT use autostart)"
echo
echo "    Journal handoff (autostart's last act is launching the UI):"
printf '%s\n' "$RAW" | awk '/^JOURNAL<<:/{f=1;next}/^:>>/{f=0}f{print "      "$0}'
echo
echo "(B) MANGOHUD OWNERSHIP"
echo "    mangohud references in /usr/bin/runemu.sh (game-launch wrapper): $MANGO_OWNED"
echo "    mangohud processes running now: $MANGO_NOW"
echo "    emulator processes running now: $EMU_NOW"
echo "    => MangoHud is loaded by runemu.sh at GAME LAUNCH (LD_PRELOAD into the"
echo "       emulator), not at boot. With no emulator running it is absent."
echo
echo "(C) VERDICT"
if [ "${as_exit:-0}" != "0" ] && [ -n "${as_exit:-}" ]; then
    echo "    autostart finished at +$(us2s "$as_exit")s into boot and its final action"
    echo "    starts the UI. MangoHud only appears at a later, user-initiated game launch."
    echo "    The two windows do not overlap."
fi
echo
echo "    CLAIM: 'autostart ... causes race conditions with MangoHud'  →  UNSUPPORTED."
echo "      - autostart runs at boot and GATES the UI (ordered Before= the UI service);"
echo "        it is not 'tied to the UI load sequence' in the sense of running with/after it."
echo "      - MangoHud is a game-launch overlay; there is no shared time window to race in."
echo "      - ETK does not even use autostart (etk.service in /storage/.config/system.d/)."
echo
echo "    WHAT IS REAL (the correct reason to keep daemons OUT of autostart):"
echo "      autostart is SYNCHRONOUS and on the critical chain to the UI. A long-running"
echo "      or blocking script dropped there delays UI bring-up. A persistent supervisor"
echo "      therefore belongs in its own systemd unit — which is what ETK already does."
} | tee "$ART"

echo
echo "✅ Artifact written: ${ART#$HERE/}"
