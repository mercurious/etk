#!/bin/bash
# ==========================================================
# tools/test_dpmirror_deploy.sh — DP-mirror: the MODE knob vs the DAEMON kill-switch
# ==========================================================
# ETK_DP_MIRROR was designed (d269e09, 2026-06-27) as the daemon's LIVE MODE:
# 1 = mirror, 0 = record-only (game native on DP-1) — record-only needs the
# daemon. edd93d8 (2026-08-08) made an install-time kill-switch real but put it
# on ETK_DP_MIRROR=0, so choosing record-only per etk.conf.example removed the
# daemon at the next install. 2026-10-09: the kill-switch is its own knob,
# ETK_DP_MIRROR_DAEMON=0; ETK_DP_MIRROR is the mode again.
#
# Runs install.sh's real STEP 6.7 block (extracted) against a stub `ssh` that
# records DEPLOY (the DPMIRRORREMOTE body arrived) or REMOVE, and sources
# bin/dpmirror_d.sh's _mirror_enabled through its DPMIRROR_LIB seam.
#
#   tools/test_dpmirror_deploy.sh                     # working tree — must PASS
#   tools/test_dpmirror_deploy.sh --against 621d53d   # pre-fix — must FAIL
set -u
cd "$(dirname "$0")/.." || exit 1
REV=""; [ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"
FAIL=0; PASS=0
ok()   { PASS=$((PASS+1)); printf 'ok   %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$*"; }
TD=$(mktemp -d /tmp/etk_dpdeploy_XXXXXX); trap 'rm -rf "$TD"' EXIT
for f in install.sh etk.conf.example bin/dpmirror_d.sh; do
    o="$TD/$(basename "$f")"
    if [ -n "$REV" ]; then git show "$REV:$f" > "$o" 2>/dev/null || : > "$o"; else cp "$f" "$o"; fi
done
# STEP 6.7's deploy/remove block: from the Support-services toast to the fi after "removed"
awk '/^rig_toast 88 "Support services"/ {i=1; next} i {print} i && /DP-mirror daemon removed/ {getline; print; exit}' "$TD/install.sh" > "$TD/block.sh"
[ -s "$TD/block.sh" ] || { echo "FAIL no STEP 6.7 block extracted"; exit 1; }

step() {  # step <ETK_DP_MIRROR|unset> <ETK_DP_MIRROR_DAEMON|unset> -> DEPLOY|REMOVE + say line
    env -i PATH="$PATH" bash -c '
        [ "$1" = unset ] || export ETK_DP_MIRROR="$1"
        [ "$2" = unset ] || export ETK_DP_MIRROR_DAEMON="$2"
        RIG_SSH=root@rig G= N= Y= R= C= LOG="$4"
        say() { printf "SAY %s\n" "$1"; }
        ssh() { local in; in=$(cat); case "$in$*" in   # to a file: the deploy call sends stdout to /dev/null
            *"systemctl enable /storage/.config/system.d/etk-dpmirror.service"*) echo DEPLOY >> "$LOG" ;;
            *"disable --now etk-dpmirror.service"*) echo REMOVE >> "$LOG" ;;
            *) echo "SSH?" >> "$LOG" ;; esac; }
        . "$3" < /dev/null' _ "$1" "$2" "$TD/block.sh" "$TD/ssh.log" 2>&1
    cat "$TD/ssh.log" 2>/dev/null; rm -f "$TD/ssh.log"
}
o=$(step 0 unset)
printf '%s' "$o" | grep -q '^DEPLOY' && ok "ETK_DP_MIRROR=0 (record-only) keeps the daemon deployed" || fail "ETK_DP_MIRROR=0 removed the daemon record-only needs: $(printf '%s' "$o" | tr '\n' ' ')"
printf '%s' "$o" | grep -q 'mode record-only' && ok "install line names the mode (record-only)" || fail "install line does not name record-only mode"
o=$(step 1 unset);     printf '%s' "$o" | grep -q '^DEPLOY' && printf '%s' "$o" | grep -q 'mode mirror' && ok "ETK_DP_MIRROR=1 -> deployed, mode mirror" || fail "mirror mode: $(printf '%s' "$o" | tr '\n' ' ')"
o=$(step unset unset); printf '%s' "$o" | grep -q '^DEPLOY' && ok "defaults -> deployed" || fail "defaults: $(printf '%s' "$o" | tr '\n' ' ')"
o=$(step 1 0)
printf '%s' "$o" | grep -q '^REMOVE' && printf '%s' "$o" | grep -q 'kill-switch ETK_DP_MIRROR_DAEMON=0' && ok "ETK_DP_MIRROR_DAEMON=0 removes the daemon (and says which knob)" || fail "DAEMON=0 did not remove: $(printf '%s' "$o" | tr '\n' ' ')"
o=$(step 0 0); printf '%s' "$o" | grep -q '^REMOVE' && ok "kill-switch wins in record-only mode too" || fail "DAEMON=0 + mode 0: $(printf '%s' "$o" | tr '\n' ' ')"

# the daemon's live reader: ETK_DP_MIRROR_DAEMON must never read as the mode
printf 'ETK_DP_MIRROR=0\nETK_DP_MIRROR_DAEMON=1\n' > "$TD/etk.conf"
if DPMIRROR_LIB=1 bash -c '. "$1"; CONF="$2"; _mirror_enabled' _ "$TD/dpmirror_d.sh" "$TD/etk.conf" 2>/dev/null; then
    fail "daemon reads ETK_DP_MIRROR_DAEMON=1 as mirror mode (prefix match)"
else ok "daemon's live reader: ETK_DP_MIRROR=0 stays record-only beside ETK_DP_MIRROR_DAEMON=1"; fi
printf 'ETK_DP_MIRROR=1\nETK_DP_MIRROR_DAEMON=0\n' > "$TD/etk.conf"
DPMIRROR_LIB=1 bash -c '. "$1"; CONF="$2"; _mirror_enabled' _ "$TD/dpmirror_d.sh" "$TD/etk.conf" 2>/dev/null \
  && ok "daemon's live reader: ETK_DP_MIRROR=1 stays mirror beside ETK_DP_MIRROR_DAEMON=0" || fail "daemon reads DAEMON=0 as the mode"

grep -q '^ETK_DP_MIRROR_DAEMON=1' "$TD/etk.conf.example" && ok "etk.conf.example documents ETK_DP_MIRROR_DAEMON" || fail "etk.conf.example lacks ETK_DP_MIRROR_DAEMON"

echo "test_dpmirror_deploy: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" = 0 ]
