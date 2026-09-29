#!/bin/sh
# ============================================================
# test_dtb_slot.sh — STEP 6.4 Flip 2 internal-mic DTB slot harness
# ------------------------------------------------------------
# Extracts the "FLIP 2 INTERNAL MIC DTB" block VERBATIM from install.sh's
# KERNELREMOTE heredoc (anti-drift: the test runs the shipped code), rewrites
# only its /flash and heal paths into a private fixture, and asserts the
# contract the grub entries depend on:
#   * derive OK            -> GTK_DTB = kit slot, slot == heal DTB.staged, verdict mic
#   * ETK_INTERNAL_MIC=0   -> GTK_DTB = stock, a previous slot + heal DTB.* REMOVED
#   * stock DTB unusable   -> GTK_DTB = stock, slot REMOVED, verdict names the reason
#   * python3 missing      -> GTK_DTB = stock (never a slot the entries can't boot)
# The broken states are fixtures too: a suite that only sees the happy path
# proves nothing. Runs on the host AND the rig (BusyBox sh + python3):
#   host: sh tools/test_dtb_slot.sh <stock-flip2.dtb>
#   rig:  scp install.sh bin/etk_dtb_mic.py tools/test_dtb_slot.sh /tmp/;
#         ssh 'sh /tmp/test_dtb_slot.sh /flash/boot/grub/sm8250-retroidpocket-flip2.dtb /tmp'
# The real DTB is only READ. Exit 0 = all pass; nonzero = failure count.
# ============================================================
set -u
STOCK_SRC="${1:-}"
ROOT="${2:-$(cd "$(dirname "$0")/.." && pwd)}"      # dir holding install.sh (+ bin/ or etk_dtb_mic.py)
[ -f "$STOCK_SRC" ] || { echo "usage: $0 <stock-flip2.dtb> [etk-root]"; exit 99; }
[ -f "$ROOT/install.sh" ] || { echo "FATAL: no install.sh under $ROOT"; exit 99; }
T="${TMPDIR:-/tmp}/dtbslot_test.$$"
mkdir -p "$T/etk/bin"
if [ -f "$ROOT/bin/etk_dtb_mic.py" ]; then cp "$ROOT/bin/etk_dtb_mic.py" "$T/etk/bin/"; else cp "$ROOT/etk_dtb_mic.py" "$T/etk/bin/"; fi
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
check() { D="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$D"; else bad "$D"; fi; }
if ! command -v sha256sum >/dev/null 2>&1; then
    mkdir -p "$T/shim"; printf '#!/bin/sh\nshasum -a 256 "$@"\n' > "$T/shim/sha256sum"; chmod +x "$T/shim/sha256sum"; PATH="$T/shim:$PATH"
fi

# The block under test: from its banner comment to the line before K_STOCK_CMDLINE=.
awk '/^# FLIP 2 INTERNAL MIC DTB/{f=1} /^K_STOCK_CMDLINE=/{f=0} f' "$ROOT/install.sh" > "$T/block.raw"
check "block extracted from install.sh (non-empty, has the derive call)" grep -q 'etk_dtb_mic.py" derive' "$T/block.raw"

# run_block <case> <ETK_INTERNAL_MIC> [PATH override]
run_block() {
    C="$T/$1"; rm -rf "$C"; mkdir -p "$C/flash/boot/grub" "$C/heal"
    cp "$STOCK_SRC" "$C/flash/boot/grub/sm8250-retroidpocket-flip2.dtb"
    sed -e "s#/storage/rocknix-gtk/heal#$C/heal#g" -e "s#/flash/boot/grub/etk-flip2.dtb#$C/flash/boot/grub/etk-flip2.dtb#g" \
        -e "s#\"/flash\$FLIP2_DTB\"#\"$C/flash\$FLIP2_DTB\"#" "$T/block.raw" > "$C/block.sh"
    printf '%s\n' 'echo "GTK_DTB=$GTK_DTB"; echo "DTB_VERDICT=$DTB_VERDICT"' >> "$C/block.sh"
    [ -n "${PRE:-}" ] && eval "$PRE"
    ( set -e; FLIP2_DTB=/boot/grub/sm8250-retroidpocket-flip2.dtb; ETK_INTERNAL_MIC="$2"; ETK_ROOT="$T/etk"
      [ -n "${3:-}" ] && PATH="$3"; . "$C/block.sh" ) > "$C/out" 2>&1
    echo $? > "$C/rc"
}
val() { sed -n "s/^$2=//p" "$T/$1/out"; }

echo "== derive OK (ETK_INTERNAL_MIC=1, real stock DTB)"
PRE= run_block mic 1
check "block exits 0" [ "$(cat "$T/mic/rc")" = 0 ]
check "GTK_DTB = kit slot" [ "$(val mic GTK_DTB)" = "/boot/grub/etk-flip2.dtb" ]
check "verdict mic" [ "$(val mic DTB_VERDICT)" = "mic" ]
check "slot present and == heal DTB.staged" cmp "$T/mic/flash/boot/grub/etk-flip2.dtb" "$T/mic/heal/DTB.staged"
check "heal DTB.staged.sha256 matches the slot" [ "$(cat "$T/mic/heal/DTB.staged.sha256")" = "$(sha256sum "$T/mic/flash/boot/grub/etk-flip2.dtb" | cut -d' ' -f1)" ]
check "heal DTB.base.sha256 = the stock DTB" [ "$(cat "$T/mic/heal/DTB.base.sha256")" = "$(sha256sum "$STOCK_SRC" | cut -d' ' -f1)" ]
check "stock DTB untouched" cmp "$T/mic/flash/boot/grub/sm8250-retroidpocket-flip2.dtb" "$STOCK_SRC"
check "slot is the patched DTB (etk_dtb_mic check)" python3 "$T/etk/bin/etk_dtb_mic.py" check "$T/mic/flash/boot/grub/etk-flip2.dtb"

echo "== kill-switch ETK_INTERNAL_MIC=0 with a slot left by a previous install"
PRE='cp "$STOCK_SRC" "$C/flash/boot/grub/etk-flip2.dtb"; echo x > "$C/heal/DTB.staged"; echo y > "$C/heal/DTB.base.sha256"' run_block off 0
check "GTK_DTB = stock" [ "$(val off GTK_DTB)" = "/boot/grub/sm8250-retroidpocket-flip2.dtb" ]
check "verdict stock(off)" [ "$(val off DTB_VERDICT)" = "stock(off)" ]
check "old slot removed" [ ! -e "$T/off/flash/boot/grub/etk-flip2.dtb" ]
check "heal DTB.* removed" [ ! -e "$T/off/heal/DTB.staged" -a ! -e "$T/off/heal/DTB.base.sha256" ]

echo "== stock DTB is not a Flip 2 DT (patcher refuses)"
printf 'not a dtb at all, just bytes........................................' > "$T/garbage.dtb"
SAVE="$STOCK_SRC"; STOCK_SRC="$T/garbage.dtb"
PRE='cp "$SAVE" "$C/flash/boot/grub/etk-flip2.dtb"' run_block refuse 1
STOCK_SRC="$SAVE"
check "GTK_DTB = stock" [ "$(val refuse GTK_DTB)" = "/boot/grub/sm8250-retroidpocket-flip2.dtb" ]
check "verdict names the reason" [ "$(val refuse DTB_VERDICT)" = "stock(not-an-fdt)" ]
check "stale slot removed (entries never name it)" [ ! -e "$T/refuse/flash/boot/grub/etk-flip2.dtb" ]

echo "== python3 unavailable"
mkdir -p "$T/nopy"; for t in sha256sum sed awk cut rm cp sync printf cat; do p=$(command -v $t 2>/dev/null) && ln -sf "$p" "$T/nopy/$t"; done
PRE= run_block nopython 1 "$T/nopy"
check "GTK_DTB = stock" [ "$(val nopython GTK_DTB)" = "/boot/grub/sm8250-retroidpocket-flip2.dtb" ]
check "verdict stock(derive-failed)" [ "$(val nopython DTB_VERDICT)" = "stock(derive-failed)" ]
check "no slot" [ ! -e "$T/nopython/flash/boot/grub/etk-flip2.dtb" ]

rm -rf "$T"
echo; echo "$PASS passed, $FAIL failed"
exit $FAIL
