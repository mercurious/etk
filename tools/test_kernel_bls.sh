#!/bin/bash
# test_kernel_bls.sh — pins the OpenABL kernel path through the KIT (ETK 0.11.0):
#   bin/bls_deploy.sh      the one writer of the ETK set on /flash
#   install.sh STEP 6.4    the chain probe + KERNELBLSREMOTE (fills the heal bundle)
#   bin/osguard.sh         the OpenABL block (release gate, coherence, the silent judge)
#   bin/kernel_stage.sh    a PE Image stages chain=bls (the couch update)
#   uninstall.sh           BLSRESTORE
#
# WHY: on OpenABL the OS owns KERNEL, rocknix.conf and dtbs/*.dtb and the kit lives beside
# them. Everything car12 proved by hand (P3 steps 0-2) has to hold when the kit does it: the
# OS's files are never written, the default never names a payload that did not verify, an OS
# update re-renders only what moved, a live GTK boot is silent (the toast it replaces lied on
# every boot of car12), and the GRUB phases never run on this layout.
#
# HOW: the rig bodies are extracted from the real files and run locally against a fake unit,
# with a `mount` stub that logs and a stub splicer. Host GNU tools stand in for BusyBox; if
# the banked 20261030 DTBs are here, the real splicer runs on the real Flip 2 trees too.
#
# DISCRIMINATION: `--against <rev>` takes every file from that revision. Against main before
# this change the suite FAILS.
#
#   tools/test_kernel_bls.sh                  # working tree — must PASS
#   tools/test_kernel_bls.sh --against main   # pre-change — must FAIL

set -u
cd "$(dirname "$0")/.." || exit 1
REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"

TD=$(mktemp -d); [ -n "${KEEP:-}" ] || trap 'rm -rf "$TD"' EXIT; [ -n "${KEEP:-}" ] && echo "sandbox: $TD"
mkdir -p "$TD/src/bin" "$TD/src/windows_installer" "$TD/stubs"
for f in install.sh uninstall.sh bin/osguard.sh bin/kernel_stage.sh bin/bls_deploy.sh bin/etk_dtb_mic.py windows_installer/etk-uninstall.ps1; do
    if [ -n "$REV" ]; then git show "$REV:$f" > "$TD/src/$f" 2>/dev/null || : > "$TD/src/$f"
    else cp "$f" "$TD/src/$f"; fi
done
extract() { awk -v m="$2" 'index($0, "<< \x27" m "\x27") {inb=1; next} inb && $0 == m {exit} inb {print}' "$TD/src/$1"; }
extract install.sh KERNELBLSREMOTE > "$TD/stage.sh"
extract uninstall.sh BLSRESTORE    > "$TD/restore.sh"
DEPLOY="$TD/src/bin/bls_deploy.sh"; GUARD="$TD/src/bin/osguard.sh"; STAGE="$TD/src/bin/kernel_stage.sh"

cat > "$TD/stubs/mount" <<'FAKE'
#!/bin/bash
echo "$*" >> "$MOUNTLOG"
exit 0
FAKE
chmod +x "$TD/stubs/mount"
# The stub splicer: SPLICE=ok|skip|fail. "derive" appends a marker so kit != stock.
cat > "$TD/splicer.py" <<'FAKE'
import os, sys
args = [a for a in sys.argv[1:] if not a.startswith('--')]
open(os.environ['SPLICELOG'], 'a').write(' '.join(sys.argv[1:]) + '\n')
mode = os.environ.get('SPLICE', 'ok')
if mode == 'skip':
    print('DTB_MIC_SKIP no-sound-node base=aa mic=absent vbus=present'); sys.exit(3)
if mode == 'fail':
    print('DTB_MIC_FAIL boom'); sys.exit(1)
d = open(args[1], 'rb').read() + b'+kit\n'
open(args[2], 'wb').write(d)
print('DTB_MIC_OK sha=bb base=aa mic=applied vbus=present')
FAKE

U="$TD/unit"; F="$U/flash"; H="$U/heal"; E="$F/loader/entries"
OPT='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 video=efifb:off gpt'
V=sm8250-retroidpocket-flip2-visionox.dtb; P=sm8250-retroidpocket-flip2.dtb
export MOUNTLOG="$U/mount.log" SPLICELOG="$U/splice.log" PATH="$TD/stubs:$PATH"
export BLS_FLASH="$F" BLS_PROC="$U/proc" BLS_HEAL="$H" BLS_SPLICER="$TD/splicer.py" ETK_ROOT="$U/etk"

mk_pe() { { printf 'MZ'; head -c 4096 /dev/zero; printf 'Linux version %s (root@%s) #1\n' "$2" "$3"; } > "$1"; }
mk_bundle() {   # the heal bundle install.sh writes
    mkdir -p "$H"
    mk_pe "$H/KERNEL.staged" 7.2.0 rocknix-gtk
    sha256sum "$H/KERNEL.staged" | cut -d' ' -f1 > "$H/KERNEL.staged.sha256"
    echo 7.2.0 > "$H/KERNEL.staged.release"
    echo KERNEL.rocknix-gtk-20261101-0.7.0 > "$H/KERNEL.staged.name"
    echo 'msm.context_keepalive=1 panic=30' > "$H/cmdline_extra"
    echo default > "$H/mode"; echo bls > "$H/chain"
}
mk_unit() {   # a car12-shaped 20261101 rig; $1 = the model, default Visionox
    rm -rf "$U"; mkdir -p "$E" "$F/dtbs" "$U/proc/device-tree" "$U/etk/bin" "$U/modules/7.2.0"
    : > "$U/mount.log"; : > "$U/splice.log"
    printf '%s\0' "${1:-Retroid Pocket Flip2 Visionox}" > "$U/proc/device-tree/model"
    printf '%s\n' "$OPT" > "$U/proc/cmdline"
    mk_pe "$F/KERNEL" 7.2.0 stock
    printf 'title      ROCKNIX\nversion    20261101\nlinux      /KERNEL\noptions    %s\n' "$OPT" > "$E/rocknix.conf"
    printf 'stock visionox\n' > "$F/dtbs/$V"; printf 'stock plain\n' > "$F/dtbs/$P"
    mk_bundle
}
snap() { (cd "$F" && find . -type f | LC_ALL=C sort | xargs sha256sum); }
osfiles() { (cd "$F" && sha256sum KERNEL loader/entries/rocknix.conf dtbs/$V dtbs/$P); }
run_guard() {   # <run_rel> [args] ; sets RC
    ETK_OS_GUARD=1 OSG_FLASH="$F" OSG_MOD_BASE="$U/modules" OSG_RUN_REL="$1" OSG_HEAL="$H" \
    OSG_DT="$U/dt" OSG_NO_REMOUNT=1 OSG_MARKER="$U/marker" OSG_BACKUP_DIR="$U" \
    OSG_GRUBENV_LIST="" OSG_CMDLINE="$U/proc/cmdline" OSG_BLS_DEPLOY="$DEPLOY" \
    TRIPWIRE_LOG="$U/trip.log" sh "$GUARD" "${@:2}" > "$U/guard.out" 2>&1
    RC=$?
}

PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
expect() {   # <label> <want-rc> <want-substring> <command...>
    local label="$1" wrc="$2" wsub="$3"; shift 3
    local out rc; out=$("$@" 2>&1); rc=$?
    if [ "$rc" = "$wrc" ] && printf '%s' "$out" | grep -qF -- "$wsub"; then ok "$label"
    else bad "$label (rc=$rc want $wrc; out: $(printf '%s' "$out" | tail -c 240 | tr '\n' ' '))"; fi
}
same() { [ "$1" = "$2" ] && ok "$3" || bad "$3 (got: $(printf '%s' "$1" | head -c 200 | tr '\n' '~'))"; }
has()  { [ -e "$F/$1" ] && ok "$2" || bad "$2"; }
hasnt() { [ ! -e "$F/$1" ] && ok "$2" || bad "$2"; }
inlog() { grep -qF -- "$1" "$2" 2>/dev/null && ok "$3" || bad "$3"; }
noremount() { [ -s "$U/mount.log" ] && bad "$1" || ok "$1"; }

echo "== deploy: a fresh 20261101 rig =="
mk_unit; O0=$(osfiles)
expect "deploy: rc 0, names what it wrote" 0 "wrote=KERNEL.gtk,dtb,etk-stock.conf,etk-gtk.conf,loader.conf" sh "$DEPLOY"
same "$(sha256sum "$F/KERNEL.gtk" | cut -d' ' -f1)" "$(cat "$H/KERNEL.staged.sha256")" "deploy: KERNEL.gtk is the bundle, byte for byte"
same "$(cat "$F/dtbs/etk/$V")" "stock visionox
+kit" "deploy: the kit DTB comes from the OS's own tree"
hasnt "dtbs/etk/$P" "deploy: only the running model's tree"
same "$(cat "$E/etk-gtk.conf")" "title      ROCKNIX-GTK
version    KERNEL.rocknix-gtk-20261101-0.7.0
linux      /KERNEL.gtk
devicetree /dtbs/etk/$V
options    $OPT msm.context_keepalive=1 panic=30" "deploy: etk-gtk.conf, stock's options verbatim + the extra (same render as bls_prestage)"
same "$(cat "$E/etk-stock.conf")" "title      ROCKNIX (stock kernel, ETK DTB line)
version    20261101
linux      /KERNEL
devicetree /dtbs/$V
options    $OPT
sort-key   etk-stock" "deploy: etk-stock.conf names the OS's own tree"
same "$(cat "$F/loader/loader.conf")" "default etk-gtk.conf" "deploy: exactly one default line"
same "$(osfiles)" "$O0" "deploy: KERNEL, rocknix.conf and the stock DTBs untouched"
same "$(cut -d' ' -f2 "$U/mount.log" | tr '\n' ' ')" "remount,rw remount,ro " "deploy: rw, then ro"
hasnt KERNEL.gtk.new "deploy: no temp file left"
[ -e "$H/.bls-render" ] && bad "deploy: render dir left" || ok "deploy: render dir gone"

echo "== deploy: idempotent =="
S0=$(snap); : > "$U/mount.log"
expect "again: writes nothing" 0 "wrote=none" sh "$DEPLOY"
same "$(snap)" "$S0" "again: /flash unchanged"
noremount "again: never remounted"
expect "--check: in sync, rc 0" 0 "BLS_DEPLOY_OK wrote=none" sh "$DEPLOY" --check

echo "== deploy: an OS update moves stock's options and DTB =="
sed -i "s/^version .*/version    20261201/; s/ gpt$/ gpt newtoken=1/" "$E/rocknix.conf"
printf 'stock visionox v2\n' > "$F/dtbs/$V"
G0=$(sha256sum "$F/KERNEL.gtk"); S1=$(snap); : > "$U/mount.log"
expect "--check names the drift, rc 2" 2 "BLS_DEPLOY_DRIFT dtb,etk-stock.conf,etk-gtk.conf" sh "$DEPLOY" --check
same "$(snap)" "$S1" "--check wrote nothing"
noremount "--check never remounted"
expect "update: only what moved" 0 "wrote=dtb,etk-stock.conf,etk-gtk.conf" sh "$DEPLOY"
inlog "options    $OPT newtoken=1 msm.context_keepalive=1 panic=30" "$E/etk-gtk.conf" "update: the GTK entry follows the new options"
inlog "version    20261201" "$E/etk-stock.conf" "update: the stock entry follows the new version"
inlog "v2" "$F/dtbs/etk/$V" "update: the kit DTB follows the new tree"
same "$(sha256sum "$F/KERNEL.gtk")" "$G0" "update: KERNEL.gtk not rewritten"

echo "== deploy: mode=test, and the plain Flip 2 =="
mk_unit; echo test > "$H/mode"
expect "test: everything staged, stock boots" 0 "default=etk-stock.conf" sh "$DEPLOY"
same "$(cat "$F/loader/loader.conf")" "default etk-stock.conf" "test: the default line"
has KERNEL.gtk "test: the kernel is staged"
has loader/entries/etk-gtk.conf "test: the entry is there for the Vol-Down menu"
mk_unit "Retroid Pocket Flip2"
sh "$DEPLOY" >/dev/null
inlog "devicetree /dtbs/etk/$P" "$E/etk-gtk.conf" "plain Flip 2: its own tree, never Visionox"
inlog "devicetree /dtbs/$P" "$E/etk-stock.conf" "plain Flip 2: stock entry too"

echo "== deploy: the splicer stands down =="
mk_unit
SPLICE=skip sh "$DEPLOY" > "$U/o" 2>&1
inlog "dtb=stock(no-sound-node)" "$U/o" "skip: the reason is named"
same "$(cat "$F/dtbs/etk/$V")" "stock visionox" "skip: the entry gets the stock tree, never nothing"
mk_unit
SPLICE=fail sh "$DEPLOY" > "$U/o" 2>&1
inlog "dtb=stock(derive-failed)" "$U/o" "fail: named, and the deploy still completes"
mk_unit; BLS_SPLICER="$U/nothing.py" sh "$DEPLOY" > "$U/o" 2>&1
inlog "dtb=stock(derive-failed)" "$U/o" "no splicer: same floor"
mk_unit; printf 'ETK_INTERNAL_MIC="0"   # off\n' > "$U/etk/etk.conf"
sh "$DEPLOY" >/dev/null
inlog "--no-mic" "$U/splice.log" "etk.conf ETK_INTERNAL_MIC=0 reaches the splicer"
mk_unit; printf 'ETK_SD_POLL=1\nif [[ bash ]]; then $unset_thing; fi\n' > "$U/etk/etk.conf"
expect "a bashism in etk.conf does not end the run" 0 "BLS_DEPLOY_OK" sh "$DEPLOY"
inlog "--sd-poll" "$U/splice.log" "etk.conf ETK_SD_POLL=1 reaches the splicer"

echo "== deploy: a GTK card keeps its own labels =="
CARDOPT='boot=LABEL=ROCKNIX-GTK disk=LABEL=GTKSTOR quiet rootwait console=tty0 video=efifb:off gpt'
mk_unit; sed -i "s/^options .*/options    $CARDOPT/" "$E/rocknix.conf"; printf '%s\n' "$CARDOPT" > "$U/proc/cmdline"
sh "$DEPLOY" >/dev/null
inlog "options    $CARDOPT msm.context_keepalive=1 panic=30" "$E/etk-gtk.conf" "card: the entry carries the card's labels"
# the OS update writes a stock rocknix.conf naming ROCKNIX/STORAGE over the card's
sed -i "s/^options .*/options    $OPT/" "$E/rocknix.conf"; O0=$(sha256sum "$E/rocknix.conf")
expect "card, after an OS update: nothing of ours moved" 0 "wrote=none" sh "$DEPLOY"
inlog "boot=LABEL=ROCKNIX-GTK disk=LABEL=GTKSTOR" "$E/etk-gtk.conf" "card: the labels it booted with win"
inlog "boot=LABEL=ROCKNIX-GTK disk=LABEL=GTKSTOR" "$E/etk-stock.conf" "card: the stock entry stays on the card too"
same "$(sha256sum "$E/rocknix.conf")" "$O0" "card: rocknix.conf is still the OS's"
echo "== deploy: a card's first boot (entry with no devicetree, bundle seeded) =="
mk_unit; sed -i "s/^options .*/options    $CARDOPT/" "$E/rocknix.conf"; printf '%s\n' "$CARDOPT" > "$U/proc/cmdline"
mk_pe "$F/KERNEL.gtk" 7.2.0 rocknix-gtk; cmp -s "$F/KERNEL.gtk" "$H/KERNEL.staged" && ok "card fixture: KERNEL.gtk is the bundle" || bad "card fixture: KERNEL.gtk is the bundle"
printf 'title      ROCKNIX-GTK\nversion    x\nlinux      /KERNEL.gtk\noptions    %s msm.context_keepalive=1 panic=30\n' "$CARDOPT" > "$E/etk-gtk.conf"
printf 'default etk-gtk.conf\n' > "$F/loader/loader.conf"
printf '%s msm.context_keepalive=1 panic=30\n' "$CARDOPT" > "$U/proc/cmdline"
expect "first boot: the render takes over, kernel not rewritten" 0 "wrote=dtb,etk-stock.conf,etk-gtk.conf" sh "$DEPLOY"
inlog "devicetree /dtbs/etk/$V" "$E/etk-gtk.conf" "first boot: the running model's tree is named"
inlog "devicetree /dtbs/$V" "$E/etk-stock.conf" "first boot: the floor exists now"

echo "== deploy: refusals write nothing =="
refused() {   # <label> <substring> ; the fixture is already bent
    local s; s=$(snap); : > "$U/mount.log"
    expect "$1" 1 "$2" sh "$DEPLOY"
    same "$(snap)" "$s" "$1: wrote nothing"
    noremount "$1: never remounted"
}
mk_unit; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$F/KERNEL"
refused "refuse: a boot.img rig" "not a PE image"
mk_unit; rm "$E/rocknix.conf"
refused "refuse: no rocknix.conf" "not an OpenABL rig"
mk_unit; cp "$E/rocknix.conf" "$E/etk-gtk+1-2.conf"
refused "refuse: a count trial in place" "boot-count trial"
mk_unit; touch "$F/KERNEL.gtk.aside"
refused "refuse: a fallthrough trial in place" "fallthrough trial"
mk_unit; printf 'default rocknix.conf\ntimeout 3\n' > "$F/loader/loader.conf"
refused "refuse: a foreign loader.conf" "not ours"
mk_unit; rm "$H/KERNEL.staged.sha256"
refused "refuse: an empty bundle" "no staged kernel"
mk_unit; echo tampered >> "$H/KERNEL.staged"
refused "refuse: a bad sha" "fails its sha256"
mk_unit; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$H/KERNEL.staged"; sha256sum "$H/KERNEL.staged" | cut -d' ' -f1 > "$H/KERNEL.staged.sha256"
refused "refuse: a boot.img in the bundle" "not a PE image"
mk_unit; printf 'Odd Board\0' > "$U/proc/device-tree/model"
refused "refuse: an unknown model" "unknown model"
mk_unit; rm "$F/dtbs/$V"
refused "refuse: the OS ships no such DTB" "ships no dtbs"
mk_unit; sed -i '/^options/d' "$E/rocknix.conf"
refused "refuse: no options line" "no options line"

echo "== deploy: loader.conf goes last =="
mk_unit; mkdir -p "$F/KERNEL.gtk.new/blocker"
expect "a failed kernel write stops the run" 1 "BLS_DEPLOY_FAIL" sh "$DEPLOY"
hasnt loader/loader.conf "the default was never written"
hasnt loader/entries/etk-gtk.conf "the entry was never written"
same "$(tail -n1 "$U/mount.log" | cut -d' ' -f2)" "remount,ro" "/flash went back to ro"
mk_unit; printf 'default etk-stock.conf\n' > "$F/loader/loader.conf"; mkdir -p "$F/KERNEL.gtk.new/blocker"
sh "$DEPLOY" >/dev/null 2>&1
same "$(cat "$F/loader/loader.conf")" "default etk-stock.conf" "a failed write leaves the old default in charge"

echo "== deploy: the ABL-era parked copy =="
mk_unit; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$F/KERNEL.etk-stock"
sh "$DEPLOY" >/dev/null
hasnt KERNEL.etk-stock "a parked boot.img is removed: it cannot boot here"
expect "...and that alone counts as work" 0 "wrote=none" sh "$DEPLOY"
mk_unit; sh "$DEPLOY" >/dev/null; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$F/KERNEL.etk-stock"; : > "$U/mount.log"
expect "--check reports a lone parked boot.img" 2 "legacy-KERNEL.etk-stock" sh "$DEPLOY" --check
expect "a lone parked boot.img is still removed" 0 "BLS_DEPLOY_OK" sh "$DEPLOY"
hasnt KERNEL.etk-stock "...gone"
mk_unit; mk_pe "$F/KERNEL.etk-stock" 7.2.0 x
sh "$DEPLOY" >/dev/null
has KERNEL.etk-stock "a parked file that is not a boot.img is not ours to remove"

echo "== real splicer, real Flip 2 trees (banked DTBs) =="
BANK="$HOME/rocknix-gtk/groundtruth/nightly-20261030/bootloader-20261030/dtbs"
if [ -f "$BANK/$V" ]; then
    mk_unit; cp "$BANK/$V" "$F/dtbs/$V"
    BLS_SPLICER="$TD/src/bin/etk_dtb_mic.py" sh "$DEPLOY" > "$U/o" 2>&1
    inlog "dtb=mic" "$U/o" "real tree: the mic delta applies on the 20261101 Visionox DTB"
    python3 "$TD/src/bin/etk_dtb_mic.py" check "$F/dtbs/etk/$V" 2>&1 | grep -q DTB_MIC_PATCHED && ok "real tree: the output reads patched" || bad "real tree: the output reads patched"
else
    echo "(no banked DTBs: real-splicer cases skipped)"
fi

echo "== install.sh: the chain probe =="
PROBE=$(sed -n "s/^K_CHAIN=\$(ssh \$RIG_SSH '\(.*\)' 2>\/dev\/null)$/\1/p" "$TD/src/install.sh" | sed "s|/flash|$F|g")
mk_unit; same "$(sh -c "$PROBE")" "bls" "probe: a 20261101 rig reads bls"
mkdir -p "$F/EFI"; same "$(sh -c "$PROBE")" "bls" "probe: an old /flash/EFI does not make it grub"
mk_unit; rm -rf "$F/loader"; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$F/KERNEL"
same "$(sh -c "$PROBE")" "abl" "probe: a 20261001 rig still reads abl"
mk_unit; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$F/KERNEL"
same "$(sh -c "$PROBE")" "abl" "probe: a pre-staged 20261001 rig is not bls"

echo "== install.sh: KERNELBLSREMOTE =="
stage() { HOST_SHA="$1" K_RELEASE=7.2.0 K_MODE="${2:-default}" K_NAME=KERNEL.rocknix-gtk-20261101-0.7.0 K_EXTRA='msm.context_keepalive=1 panic=30' \
          HEAL="$H" STAGING="$U/staging" ETK_ROOT="$TD/src" sh "$TD/stage.sh" 2>&1; }
mk_unit; rm -rf "$H"; mk_pe "$U/staging" 7.2.0 rocknix-gtk; W=$(sha256sum "$U/staging" | cut -d' ' -f1)
OUT=$(stage "$W"); printf '%s' "$OUT" | grep -q 'BLS_DEPLOY_OK' && ok "stage: rc path reaches the deploy" || bad "stage: rc path reaches the deploy ($OUT)"
same "$(cat "$H/chain")" "bls" "stage: the bundle says chain=bls"
same "$(cat "$H/KERNEL.staged.sha256")" "$W" "stage: the bundle carries the sha"
same "$(cat "$H/KERNEL.staged.name")" "KERNEL.rocknix-gtk-20261101-0.7.0" "stage: the bundle carries the name"
same "$(cat "$H/cmdline_extra")" "msm.context_keepalive=1 panic=30" "stage: the bundle carries the extra"
same "$(cat "$F/loader/loader.conf")" "default etk-gtk.conf" "stage: the deploy ran"
hasnt ../staging "stage: the staging file was consumed"
mk_unit; rm -rf "$H"; mk_pe "$U/staging" 7.2.0 rocknix-gtk
OUT=$(stage deadbeef); printf '%s' "$OUT" | grep -q 'staging sha mismatch' && ok "stage: a bad transfer is refused" || bad "stage: a bad transfer is refused ($OUT)"
[ -e "$H/KERNEL.staged" ] && bad "stage: nothing reached the bundle" || ok "stage: nothing reached the bundle"
hasnt loader/loader.conf "stage: nothing reached /flash"
mk_unit; rm -rf "$H"; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$U/staging"; W=$(sha256sum "$U/staging" | cut -d' ' -f1)
OUT=$(stage "$W"); printf '%s' "$OUT" | grep -q 'not a PE image' && ok "stage: a boot.img is refused" || bad "stage: a boot.img is refused ($OUT)"
mk_unit; rm -rf "$H"; mk_pe "$U/staging" 7.2.0 rocknix-gtk; W=$(sha256sum "$U/staging" | cut -d' ' -f1)
OUT=$(stage "$W" test); same "$(cat "$F/loader/loader.conf")" "default etk-stock.conf" "stage: mode=test keeps stock as default"

echo "== osguard: the silent judge =="
deployed() { mk_unit "$@"; sh "$DEPLOY" >/dev/null; : > "$U/mount.log"; }
deployed; printf '%s msm.context_keepalive=1 panic=30\n' "$OPT" > "$U/proc/cmdline"; S0=$(snap)
run_guard 7.2.0
same "$RC" 0 "live: rc 0"
inlog "this boot runs it" "$U/guard.out" "live: the log says so"
[ -f "$U/marker" ] && bad "live: NO toast (the false one this replaces)" || ok "live: NO toast (the false one this replaces)"
same "$(snap)" "$S0" "live: /flash unchanged"
noremount "live: never remounted"
deployed; run_guard 7.2.0
same "$RC" 0 "stock boot, set in place: rc 0"
inlog "ran the stock kernel" "$U/guard.out" "stock boot: logged"
[ -f "$U/marker" ] && bad "stock boot: no toast (a menu pick is not a fault)" || ok "stock boot: no toast (a menu pick is not a fault)"

echo "== osguard: after an OS update =="
deployed; sed -i "s/ gpt$/ gpt newtoken=1/" "$E/rocknix.conf"; printf 'stock visionox v2\n' > "$F/dtbs/$V"; O0=$(osfiles)
run_guard 7.2.0 --check
same "$RC" 2 "--check: rc 2"
inlog "PLAN" "$U/guard.out" "--check: planned only"
grep -q newtoken "$E/etk-gtk.conf" && bad "--check: no write" || ok "--check: no write"
run_guard 7.2.0 --dry-run
grep -q newtoken "$E/etk-gtk.conf" && bad "--dry-run: no write" || ok "--dry-run: no write"
run_guard 7.2.0
same "$RC" 0 "heal: rc 0"
inlog "newtoken=1 msm.context_keepalive=1" "$E/etk-gtk.conf" "heal: the entry re-rendered"
inlog "v2" "$F/dtbs/etk/$V" "heal: the kit DTB re-derived"
same "$(osfiles)" "$O0" "heal: the OS's files untouched"
inlog "REBOOT once" "$U/marker" "heal: the toast tells the truth"
rm -f "$U/marker"; run_guard 7.2.0
[ -f "$U/marker" ] && bad "heal: next boot is silent" || ok "heal: next boot is silent"

echo "== osguard: a couch update staged a new kernel =="
deployed; mk_pe "$H/KERNEL.staged" 7.2.0 rocknix-gtk-newer; sha256sum "$H/KERNEL.staged" | cut -d' ' -f1 > "$H/KERNEL.staged.sha256"
run_guard 7.2.0
same "$(sha256sum "$F/KERNEL.gtk" | cut -d' ' -f1)" "$(cat "$H/KERNEL.staged.sha256")" "couch: the new kernel reached /flash"
inlog "REBOOT once" "$U/marker" "couch: told to reboot"

echo "== osguard: the release gate =="
deployed; rm -rf "$U/modules/7.2.0"; mkdir -p "$U/modules/7.3.0"; O0=$(osfiles)
run_guard 7.3.0
same "$(cat "$F/loader/loader.conf")" "default etk-stock.conf" "gate: the default goes back to stock"
inlog "new kernel (7.3.0)" "$U/marker" "gate: the toast names the release"
same "$(osfiles)" "$O0" "gate: the OS's files untouched"
has KERNEL.gtk "gate: the GTK kernel stays for when it fits again"
rm -f "$U/marker"; run_guard 7.3.0
[ -f "$U/marker" ] && bad "gate: named once, not every boot" || ok "gate: named once, not every boot"
deployed; rm -rf "$U/modules/7.2.0"; mkdir -p "$U/modules/7.3.0"
run_guard 7.3.0 --check
same "$RC" 2 "gate --check: rc 2"
same "$(cat "$F/loader/loader.conf")" "default etk-gtk.conf" "gate --check: no write"

echo "== osguard: other states on this layout =="
deployed; cp "$E/etk-gtk.conf" "$E/etk-gtk+1.conf"; rm "$E/etk-gtk.conf"; S0=$(snap)
run_guard 7.2.0
inlog "needs attention" "$U/marker" "a trial in place is named"
same "$(snap)" "$S0" "a trial in place: no write"
mk_unit; rm -rf "$H"; S0=$(snap)
run_guard 7.2.0
same "$RC" 0 "no bundle: rc 0"
inlog "nothing to guard" "$U/guard.out" "no bundle: nothing to guard"
same "$(snap)" "$S0" "no bundle: wrote nothing"
hasnt KERNEL.gtktest "no bundle: the GRUB phases never ran (no KERNEL.gtktest)"
mk_unit; mk_bundle; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$H/KERNEL.staged"
sha256sum "$H/KERNEL.staged" | cut -d' ' -f1 > "$H/KERNEL.staged.sha256"; echo abl > "$H/chain"; S0=$(snap)
run_guard 7.2.0
inlog "new bootloader" "$U/marker" "chain=abl on this layout: the ABL block names the move"
inlog "Re-run the ETK installer" "$U/marker" "chain=abl: the toast says what to do"
same "$(snap)" "$S0" "chain=abl: /flash untouched"

echo "== osguard: a couch update reached a rig still on the ABL chain =="
mk_unit; rm -rf "$F/loader"; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$F/KERNEL"; S0=$(snap)
run_guard 7.2.0
same "$RC" 0 "ABL rig + chain=bls: rc 0"
inlog "deploys after the ROCKNIX update" "$U/guard.out" "ABL rig + chain=bls: logged"
[ -f "$U/marker" ] && bad "ABL rig + chain=bls: no toast on every boot" || ok "ABL rig + chain=bls: no toast on every boot"
same "$(snap)" "$S0" "ABL rig + chain=bls: the running slot untouched"

echo "== kernel_stage: the couch path =="
ks() { ETK_ROOT="$TD/src" KS_HEAL="$H" KS_CFG_LIST="" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$U/ks.log" sh "$STAGE" "$@"; }
mkdir -p "$TD/src/config"
printf '{ "kernel": { "cmdline_extra": "msm.context_keepalive=1 panic=30 extra=9" } }\n' > "$TD/src/config/gtk_stack.json"
mk_unit; rm -rf "$H"; mk_pe "$U/KERNEL.rocknix-gtk-20261101-0.7.1" 7.2.0 rocknix-gtk; W=$(sha256sum "$U/KERNEL.rocknix-gtk-20261101-0.7.1" | cut -d' ' -f1)
expect "stage: a PE Image stages" 0 "chain=bls" ks "$U/KERNEL.rocknix-gtk-20261101-0.7.1" "$W"
same "$(cat "$H/chain")" "bls" "stage: chain=bls"
same "$(cat "$H/KERNEL.staged.release")" "7.2.0" "stage: the release comes from the Image"
same "$(cat "$H/KERNEL.staged.name")" "KERNEL.rocknix-gtk-20261101-0.7.1" "stage: the name comes from the asset"
same "$(cat "$H/cmdline_extra")" "msm.context_keepalive=1 panic=30 extra=9" "stage: the extra comes from the manifest"
mk_unit; rm -rf "$H"; mk_pe "$U/k" 7.2.0 rocknix-gtk; W=$(sha256sum "$U/k" | cut -d' ' -f1)
expect "stage: a manifest that disagrees refuses" 1 "nothing staged" env KS_RELEASE=7.3.0 ETK_ROOT="$TD/src" KS_HEAL="$H" KS_CFG_LIST="" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$U/ks.log" sh "$STAGE" "$U/k" "$W"
[ -e "$H/KERNEL.staged" ] && bad "stage: the refusal staged nothing" || ok "stage: the refusal staged nothing"

echo "== uninstall: BLSRESTORE =="
restore() { FLASH="$F" STOCK_ENTRY_TOO="${1:-0}" sh "$TD/restore.sh" 2>&1; }
deployed; O0=$(osfiles); OUT=$(restore)
hasnt KERNEL.gtk "restore: the GTK kernel is gone"
hasnt loader/entries/etk-gtk.conf "restore: its entry is gone"
hasnt dtbs/etk "restore: the kit DTBs are gone"
has loader/entries/etk-stock.conf "restore: etk-stock.conf STAYS (a never-set unit boots dark without it)"
same "$(cat "$F/loader/loader.conf")" "default etk-stock.conf" "restore: the default is stock"
same "$(osfiles)" "$O0" "restore: the OS's files untouched"
same "$(cut -d' ' -f2 "$U/mount.log" | tr '\n' ' ')" "remount,rw remount,ro " "restore: rw, then ro"
printf '%s' "$OUT" | grep -q 'stock-entry-too' && ok "restore: names how to remove the rest" || bad "restore: names how to remove the rest ($OUT)"
deployed; restore 1 >/dev/null
hasnt loader/entries/etk-stock.conf "--stock-entry-too: the stock entry goes"
hasnt loader/loader.conf "--stock-entry-too: loader.conf goes"
has loader/entries/rocknix.conf "--stock-entry-too: rocknix.conf stays"
deployed; mv "$E/etk-gtk.conf" "$E/etk-gtk+0-3.conf"; mv "$F/KERNEL.gtk" "$F/KERNEL.gtk.aside"; restore >/dev/null
hasnt loader/entries/etk-gtk+0-3.conf "restore: a count trial is cleared"
hasnt KERNEL.gtk.aside "restore: a fallthrough trial is cleared"
deployed; printf 'default rocknix.conf\ntimeout 3\n' > "$F/loader/loader.conf"; restore >/dev/null
same "$(sed -n 2p "$F/loader/loader.conf")" "timeout 3" "restore: a foreign loader.conf is left alone"
deployed; rm "$E/etk-stock.conf"; restore >/dev/null
hasnt loader/loader.conf "restore: no stock entry means no default naming one"
mk_unit; S0=$(snap); OUT=$(restore)
same "$(snap)" "$S0" "restore: nothing of ours, nothing written"
noremount "restore: nothing of ours, no remount"
mk_unit; rm -rf "$F/loader"; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$F/KERNEL"; S0=$(snap); OUT=$(restore)
same "$(snap)" "$S0" "restore: a 20261001 rig is not this body's"

echo "== Windows uninstaller =="
grep -q 'Marker "BLSRESTORE"' "$TD/src/windows_installer/etk-uninstall.ps1" && ok "the PS port runs BLSRESTORE by marker" || bad "the PS port runs BLSRESTORE by marker"

echo
echo "test_kernel_bls: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
