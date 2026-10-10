#!/bin/sh
# ==========================================================
# tools/test_sd_rebind.sh — crash-card SD rebind: install-time bind + idempotence
# ==========================================================
# Paid for 2026-10-09 on car8, right after its 20261001 migration. ETK_ROOT
# (/storage/games-internal/roms/etk) lives on the SDGAMES card once STEP 6.85's
# rebind stacks the card's games-internal/ over /storage/games-internal. The
# uninstall removed the rebind unit, the OS-update and install boots ran
# without it, so install.sh pushed the whole kit to the INTERNAL directory —
# and the next cold boot hid it under the card's copy (no env.sh): no Sentry,
# no SHM, no HUD, no input_d (L1+R3 dead), no ledger.
# The fix: install.sh runs STEP 6.85's own script (extracted from itself)
# BEFORE its first push — the SD REBIND PREFLIGHT — and the script judges
# "already bound" by device:inode, so it never stacks binds and never mistakes
# an OS mount on /storage/roms (20261001 has one) for its own.
#
# Mounts are simulated: a stub `mount` turns the target into a symlink to the
# source and logs it (STACKED when the target was already mounted); a stub
# `mountpoint` answers "is a symlink". `stat -L` then sees the same
# device:inode through the link exactly as it does through a bind on the rig.
#
# This harness proves:
#   1. no SDGAMES card -> exit 0, nothing mounted (single-card rigs);
#   2. card without games-internal/ -> card mounted, no binds;
#   3. card, unbound (car8 after uninstall + OTA) -> games-internal and roms
#      both resolve to the card's tree;
#   4. a second run (the boot after an install-time bind) mounts NOTHING more;
#   5. an OS mount already on /storage/roms (not ours) is still re-pointed at
#      the card's games-internal/roms;
#   6. install.sh order: the preflight sits after the live-session guard and the
#      beacon's announcement, BEFORE STEP 0's first ETK_ROOT write and STEP 1, extracts the same
#      script STEP 6.85 writes, and refuses the install when the bind fails;
#      ...and the PowerShell port runs the same two bodies (RBND, REBINDPRE) by
#      marker, after its guard and before its first step;
#   7. (--rig) the rig's shell + BusyBox stat give byte-identical results.
#
# DISCRIMINATION:
#   tools/test_sd_rebind.sh                     # working tree — must PASS
#   tools/test_sd_rebind.sh --against ac40b4a   # pre-fix — must FAIL
#   tools/test_sd_rebind.sh --rig               # + BusyBox leg (writes only /tmp on the rig)
set -u
cd "$(dirname "$0")/.." || exit 1
REV=""; RIGLEG=0
while [ $# -gt 0 ]; do
    case "$1" in
        --against) REV="${2:?--against needs a revision}"; shift 2 ;;
        --rig) RIGLEG=1; shift ;;
        *) echo "usage: $0 [--against <rev>] [--rig]"; exit 2 ;;
    esac
done
FAIL=0; PASS=0
ok()   { PASS=$((PASS+1)); printf 'ok   %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$*"; }
TD=$(mktemp -d /tmp/etk_rebind_XXXXXX)
[ -n "${KEEP:-}" ] && echo "sandbox: $TD" || trap 'rm -rf "$TD"' EXIT
if [ -n "$REV" ]; then git show "$REV:install.sh" > "$TD/install.sh" || exit 1
else cp install.sh "$TD/install.sh"; fi
RBND_RE='/^cat << .RBND. > \/storage\/\.config\/custom_scripts\/etk-sd-rebind\.sh$/'
awk "$RBND_RE"' {inb=1; next} inb && /^RBND$/ {exit} inb {print}' "$TD/install.sh" > "$TD/rebind.sh"
[ -s "$TD/rebind.sh" ] || { echo "FAIL no rebind script extracted from install.sh"; exit 1; }
awk 'index($0, "<< \x27REBINDPRE\x27") {inb=1; next} inb && $0 == "REBINDPRE" {exit} inb {print}' "$TD/install.sh" > "$TD/prebody.sh"

# --- the runner (shared with the rig leg): sandbox per case, deterministic report ---
cat > "$TD/runner.sh" << 'RUNNER'
#!/bin/sh
D=$1; cd "$D" || exit 1
mkdir -p "$D/stubs"
cat > "$D/stubs/mount" << 'M'
#!/bin/sh
if [ "$1" = "--bind" ]; then src=$2; dst=$3; [ -n "${MOUNT_FAIL:-}" ] && exit 1; else src=$SB/card; dst=$2; fi
[ -L "$dst" ] && echo "STACKED $dst" >> "$SB/mount.log"
echo "mount $*" >> "$SB/mount.log"
rm -rf "$dst" && ln -s "$src" "$dst"
M
printf '#!/bin/sh\n[ "$1" = -q ] && shift\n[ -L "$1" ]\n' > "$D/stubs/mountpoint"
chmod +x "$D/stubs/mount" "$D/stubs/mountpoint"
mk() {  # mk <case> card|nocard|notree
    SB=$D/cases/$1; rm -rf "$SB"; mkdir -p "$SB/storage/games-internal/roms/etk/scripts" "$SB/storage/roms" "$SB/bylabel" "$SB/dev"
    echo internal > "$SB/storage/games-internal/roms/etk/scripts/env.sh"
    if [ "$2" != nocard ]; then
        : > "$SB/dev/mmcblk0p2"; ln -s "$SB/dev/mmcblk0p2" "$SB/bylabel/SDGAMES"
        mkdir -p "$SB/card/roms"
        [ "$2" = card ] && { mkdir -p "$SB/card/games-internal/roms/etk/vault"; echo card > "$SB/card/games-internal/roms/etk/vault/marker"; }
    fi
    sed -e "s#/dev/disk/by-label#$SB/bylabel#g" -e "s#/storage#$SB/storage#g" "$D/rebind.sh" > "$SB/rebind.sh"
}
run() { SB=$D/cases/$1; export SB; PATH="$D/stubs:$PATH" bash "$SB/rebind.sh" > /dev/null 2>&1; echo "rc=$?" >> "$SB/rc"; }
res() { r=$(readlink -f "$1" 2>/dev/null); echo "${r#$SB/}"; }
cnt() { n=$(grep -c "$1" "$2" 2>/dev/null); echo "${n:-0}"; }
report() {
    SB=$D/cases/$1
    echo "== $1 $(tr '\n' ' ' < "$SB/rc")binds=$(cnt '^mount --bind' "$SB/mount.log") stacked=$(cnt '^STACKED' "$SB/mount.log") cardmount=$(cnt '^mount [^-]' "$SB/mount.log")"
    echo "   games-internal -> $(res "$SB/storage/games-internal")"
    echo "   roms -> $(res "$SB/storage/roms")"
}
mk nocard nocard;  run nocard
mk notree notree;  run notree
mk unbound card;   run unbound
mk rerun card;     run rerun; : > "$D/cases/rerun/mount.log"; run rerun
mk osroms card;    ln -s "$D/cases/osroms/card/roms" "$D/cases/osroms/storage/roms.os"; rm -rf "$D/cases/osroms/storage/roms"
                   mv "$D/cases/osroms/storage/roms.os" "$D/cases/osroms/storage/roms"; run osroms
for c in nocard notree unbound rerun osroms; do report $c; done
# the REBINDPRE verdict (install's refuse gate), run as install.sh runs it
pre() {  # pre <case> <card|nocard|notree> [prebound]
    mk "$1" "$2"; SB=$D/cases/$1; export SB
    [ "${3:-}" = prebound ] && run "$1"
    cp "$SB/rebind.sh" "$SB/pre.sh"
    sed -e "s#/tmp/etk-sd-rebind.preflight.sh#$SB/pre.sh#g" -e "s#/dev/disk/by-label#$SB/bylabel#g" -e "s#/storage#$SB/storage#g" "$D/prebody.sh" > "$SB/prebody.sh"
    echo "== pre $1 $(PATH="$D/stubs:$PATH" sh "$SB/prebody.sh" 2>/dev/null | grep '^REBIND_PRE' | tr '\n' ' ')"
}
if [ -s "$D/prebody.sh" ]; then
    pre pnone nocard; pre ptree notree; pre punbound card; pre pbound card prebound
    MOUNT_FAIL=1; export MOUNT_FAIL; pre pfail card; unset MOUNT_FAIL
fi
RUNNER
sh "$TD/runner.sh" "$TD" > "$TD/host.out" 2>&1
line() { grep -A2 "^== $1 " "$TD/host.out"; }

# 1-5. the script
line nocard | head -1 | grep -q 'rc=0 binds=0 stacked=0 cardmount=0' && ok "no SDGAMES card: exit 0, nothing mounted" || fail "no card: $(line nocard | head -1)"
line notree | head -1 | grep -q 'rc=0 binds=0 stacked=0 cardmount=1' && ok "card without games-internal/: mounted, no binds" || fail "notree: $(line notree | head -1)"
line unbound | grep -q 'games-internal -> card/games-internal$' && line unbound | grep -q 'roms -> card/games-internal/roms$' \
  && ok "unbound card (car8 after uninstall + OTA): games-internal + roms -> the card's tree" || fail "unbound: $(line unbound | tr '\n' ' ')"
line rerun | head -1 | grep -q 'rc=0 rc=0 binds=0 stacked=0 cardmount=0' && ok "second run on a bound rig mounts nothing (no stacked binds)" || fail "rerun mounted again: $(line rerun | head -1)"
line rerun | grep -q 'games-internal -> card/games-internal$' && ok "second run keeps the card's tree" || fail "rerun lost the bind"
line osroms | grep -q 'roms -> card/games-internal/roms$' && ok "an OS mount on /storage/roms (not ours) is re-pointed at games-internal/roms" || fail "osroms: $(line osroms | tr '\n' ' ')"

# the verdict install.sh acts on
pv() { grep "^== pre $1 " "$TD/host.out" | sed "s/^== pre $1 //"; }
pv pnone | grep -q '^REBIND_PRE none' && ok "verdict: no card -> none (install proceeds, nothing bound)" || fail "verdict pnone: '$(pv pnone)'"
pv ptree | grep -q '^REBIND_PRE nocardtree' && ok "verdict: card without games-internal/ -> nocardtree" || fail "verdict ptree: '$(pv ptree)'"
pv punbound | grep -q '^REBIND_PRE bound was=n' && ok "verdict: unbound card -> bound now (was=n), before the push" || fail "verdict punbound: '$(pv punbound)'"
pv pbound | grep -q '^REBIND_PRE bound was=y' && ok "verdict: already bound -> was=y" || fail "verdict pbound: '$(pv pbound)'"
pv pfail | grep -q '^REBIND_PRE FAIL' && ok "verdict: bind fails -> FAIL (install refuses)" || fail "verdict pfail: '$(pv pfail)'"

# 6. install.sh order + refusal
ln_of() { grep -n -m1 -F -- "$1" "$TD/install.sh" | cut -d: -f1; }
PRE=$(ln_of '# >>> SD REBIND PREFLIGHT'); GUARD=$(ln_of 'LIVE-SESSION GUARD'); MESA=$(ln_of 'MESA_HASH_FILE='); S1=$(ln_of '# STEP 1: PROVISION')
BEACON=$(ln_of 'rig_toast 2 "ETK install starting"')
if [ -z "$PRE" ]; then fail "install.sh has no SD REBIND PREFLIGHT"
else
    [ "$GUARD" -lt "$PRE" ] && ok "preflight runs after the live-session guard (no game holds the paths)" || fail "preflight precedes the live-session guard"
    [ "$BEACON" -lt "$PRE" ] && ok "preflight runs after the beacon announces the install (first rig mutation)" || fail "preflight mutates the rig before the beacon announces"
    [ "$PRE" -lt "$MESA" ] && [ "$PRE" -lt "$S1" ] && ok "preflight binds BEFORE STEP 0's first ETK_ROOT write and STEP 1" || fail "preflight is after the first ETK_ROOT write (line $PRE vs $MESA/$S1)"
    blk=$(awk '/# >>> SD REBIND PREFLIGHT/ {i=1} i {print} /# <<< SD REBIND PREFLIGHT/ {exit}' "$TD/install.sh")
    ext=$(printf '%s\n' "$blk" | sed -n 's/^REBIND_BODY=\$(awk \(.*\) "\${BASH_SOURCE\[0\]}")$/\1/p' | sed "s/^'//; s/'\$//")
    [ -n "$ext" ] && [ "$(awk "$ext" "$TD/install.sh")" = "$(cat "$TD/rebind.sh")" ] && ok "preflight extracts the very script STEP 6.85 writes (one source)" || fail "preflight's extraction differs from STEP 6.85's script"
    printf '%s\n' "$blk" | grep -q 'tui_fail "Install refused: $REBIND_WHY"' && printf '%s\n' "$blk" | grep -A1 'Install refused: $REBIND_WHY${N}' | grep -q 'exit 1' \
      && printf '%s\n' "$blk" | grep -q 'etk_toast_verdict_stopped' && ok "a present-but-unbindable card refuses the install (TUI + plain, STOPPED verdict on the handheld)" || fail "no refusal on a failed bind"
fi

# the verdict is ONE body (REBINDPRE), run by marker from both installers
grep -q "<< 'REBINDPRE'" "$TD/install.sh" && ok "install.sh judges the preflight in a quoted REBINDPRE body" || fail "install.sh has no REBINDPRE body"
if [ -n "$REV" ]; then git show "$REV:windows_installer/etk-install.ps1" > "$TD/etk-install.ps1" 2>/dev/null; else cp windows_installer/etk-install.ps1 "$TD/etk-install.ps1"; fi
PSPRE=$(grep -n -m1 'Marker "REBINDPRE"' "$TD/etk-install.ps1" | cut -d: -f1); PSPUSH=$(grep -n -m1 -E '^Write-Step 1 ' "$TD/etk-install.ps1" | cut -d: -f1)
PSGUARD=$(grep -n -m1 'LIVE-SESSION GUARD' "$TD/etk-install.ps1" | cut -d: -f1)
PSBEACON=$(grep -n -m1 'Invoke-RigToast 2 "ETK install starting"' "$TD/etk-install.ps1" | cut -d: -f1); PSS0=$(grep -n -m1 -E '^Write-Step 0 ' "$TD/etk-install.ps1" | cut -d: -f1)
[ -n "$PSPRE" ] && [ "$PSGUARD" -lt "$PSPRE" ] && [ "$PSBEACON" -lt "$PSPRE" ] && [ "$PSPRE" -lt "${PSS0:-0}" ] && [ "$PSPRE" -lt "${PSPUSH:-0}" ] && grep -q 'Marker "RBND") -RemotePath "/tmp/etk-sd-rebind.preflight.sh"' "$TD/etk-install.ps1" \
  && ok "PS port runs RBND + REBINDPRE after its guard and beacon, before its first step" || fail "PS port lacks the preflight (or runs it out of order)"

# 7. rig BusyBox leg
if [ "$RIGLEG" = 1 ]; then
    RIG="${RIG_SSH:-root@SM8250.local}"; RT="/tmp/etk_rebind_$$"
    if tar -C "$TD" -cf - rebind.sh prebody.sh runner.sh \
       | ssh -o BatchMode=yes "$RIG" "mkdir -p $RT && tar -C $RT -xf - && sh $RT/runner.sh $RT; R=\$?; rm -rf $RT; exit \$R" \
       > "$TD/rig.out" 2> "$TD/rig.err"; then
        cmp -s "$TD/host.out" "$TD/rig.out" && ok "BusyBox leg: rig results byte-identical to host" \
          || { fail "BusyBox leg: rig results DIFFER"; diff "$TD/host.out" "$TD/rig.out" | head -12; }
    else fail "BusyBox leg: rig run errored: $(head -2 "$TD/rig.err")"; fi
else
    echo "note: BusyBox leg skipped (--rig for the rig pass)"
fi

echo "test_sd_rebind: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" = 0 ]
