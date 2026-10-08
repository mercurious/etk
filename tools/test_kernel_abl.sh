#!/bin/bash
# test_kernel_abl.sh — pins the ABL-era kernel path through the KIT:
#   install.sh   STEP 6.4  KERNELABLREMOTE  (the only writer of /flash/KERNEL)
#   bin/osguard.sh         ABL stand-down   (judges the slot, never edits grub)
#   uninstall.sh           ABLRESTORE       (parked stock back into the slot)
#
# WHY: under ROCKNIX-ABL (20261001+) the bootloader loads exactly one file,
# /flash/KERNEL, a boot.img with the cmdline baked in; there is no grub entry to
# fall back to. Every refusal (non-stock slot with no parked copy, GRUB-era unit,
# staging sha, mount failure) and every write (park stock once and prove it
# against KERNEL.md5, sha-verified read-back, heal bundle with chain=abl, test
# mode leaves the slot alone) must hold BEFORE this runs at car12 — and osguard
# must not drop a KERNEL.gtktest on an ABL unit (the §1b false-heal path).
#
# HOW: the three rig-side bodies are EXTRACTED from the real files (heredoc
# markers) and run locally under a fake unit tree with FLASH/HEAL/STAGING
# overridden; `mount` is a stub that logs. Boot images are real mkbootimg v0
# images (fake Image + 9 tiny DTBs). Needs mkbootimg + dtc. No rig, no network.
#
# DISCRIMINATION: `--against <rev>` takes all three files from that revision.
# Against 6615699 (before the ABL path existed) every run case FAILS.
#
#   tools/test_kernel_abl.sh                     # working tree — must PASS
#   tools/test_kernel_abl.sh --against 6615699   # pre-change — must FAIL

set -u
cd "$(dirname "$0")/.." || exit 1
REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"
command -v mkbootimg >/dev/null || { echo "SKIP: mkbootimg not on PATH"; exit 0; }
command -v dtc >/dev/null || { echo "SKIP: dtc not on PATH"; exit 0; }

TD=$(mktemp -d); [ -n "${KEEP:-}" ] || trap 'rm -rf "$TD"' EXIT; [ -n "${KEEP:-}" ] && echo "sandbox: $TD"
mkdir -p "$TD/src" "$TD/stubs" "$TD/img"
for f in install.sh uninstall.sh bin/osguard.sh; do
    mkdir -p "$TD/src/$(dirname "$f")"
    if [ -n "$REV" ]; then git show "$REV:$f" > "$TD/src/$f" 2>/dev/null || : > "$TD/src/$f"
    else cp "$f" "$TD/src/$f"; fi
done
# extract <file> <marker> -> the body between "<< 'MARKER'" and the terminator line
extract() { awk -v m="$2" 'index($0, "<< \x27" m "\x27") {inb=1; next} inb && $0 == m {exit} inb {print}' "$TD/src/$1"; }
extract install.sh   KERNELABLREMOTE > "$TD/stage.sh"
extract uninstall.sh ABLRESTORE      > "$TD/restore.sh"
GUARD="$TD/src/bin/osguard.sh"

# --- boot images --------------------------------------------------------------------
STOCK_CMD='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 video=efifb:off gpt'
GTK_CMD="$STOCK_CMD msm.context_keepalive=1 panic=30"
mkimage() {  # $1 out  $2 cmdline  $3 ndtbs  $4 version-string
    python3 -I - "$TD/img" "$4" <<'PY'
import sys, os
d, ver = sys.argv[1], sys.argv[2]
img = bytearray(0x40) + b'\0' * 4096
img[0x38:0x3c] = b'ARM\x64'
img += ('Linux version 7.2.0 ' + ver + ' #1 SMP PREEMPT\n').encode()
open(os.path.join(d, 'Image'), 'wb').write(bytes(img))
PY
    local k="$TD/img/kernel.gz"; gzip -n -c "$TD/img/Image" > "$k"
    local i; for i in $(seq 1 "$3"); do
        printf '/dts-v1/;\n/ { model = "Fake Board %s"; compatible = "fake,board%s"; };\n' "$i" "$i" > "$TD/img/d.dts"
        dtc -q -I dts -O dtb -o "$TD/img/d.dtb" "$TD/img/d.dts"; cat "$TD/img/d.dtb" >> "$k"
    done
    printf 'dummy' > "$TD/img/ramdisk"
    mkbootimg --kernel "$k" --ramdisk "$TD/img/ramdisk" --header_version 0 --pagesize 2048 \
        --cmdline "$2" -o "$1" || { echo "mkbootimg failed"; exit 1; }
}
mkimage "$TD/stock.img" "$STOCK_CMD" 9 "(@0b091aade48d) (aarch64-rocknix-linux-gnu-gcc-15.2.0 (GCC) 15.2.0)"
mkimage "$TD/gtk.img"   "$GTK_CMD"   9 "(root@rocknix-gtk) (gcc-15 (Debian 15.3.0-4) 15.3.0)"
mkimage "$TD/other.img" "$STOCK_CMD" 9 "(@deadbeef) (aarch64-rocknix-linux-gnu-gcc-15.2.0 (GCC) 15.2.0)"
GTK_SHA=$(sha256sum "$TD/gtk.img" | cut -d' ' -f1)
STOCK_SHA=$(sha256sum "$TD/stock.img" | cut -d' ' -f1)
STOCK_MD5=$(md5sum "$TD/stock.img" | cut -d' ' -f1)

# --- the fake unit + stubs ------------------------------------------------------------
U="$TD/unit"
cat > "$TD/stubs/mount" <<'FAKE'
#!/bin/bash
echo "$*" >> "$UNIT/mount.log"
[ -n "${MOUNT_FAIL:-}" ] && exit 1
exit 0
FAKE
chmod +x "$TD/stubs/mount"
export PATH="$TD/stubs:$PATH" UNIT="$U"
reset_unit() {   # fresh stock ABL unit (no grub), slot = stock, nothing parked
    rm -rf "$U"; mkdir -p "$U/flash" "$U/storage" "$U/modules/7.2.0"
    cp "$TD/stock.img" "$U/flash/KERNEL"
    printf '%s  target/KERNEL\n' "$STOCK_MD5" > "$U/flash/KERNEL.md5"
    : > "$U/mount.log"
}
stage() {   # run install.sh's rig body: stage <image> [mode]
    cp "$1" "$U/storage/KERNEL.staging"
    HOST_SHA=$(sha256sum "$1" | cut -d' ' -f1) K_RELEASE=7.2.0 K_MODE="${2:-default}" \
    FLASH="$U/flash" HEAL="$U/storage/heal" STAGING="$U/storage/KERNEL.staging" sh "$TD/stage.sh"
}
restore() { FLASH="$U/flash" HEAL="$U/storage/heal" sh "$TD/restore.sh"; }
guard() {   # run osguard with the unit's seams; $1 = mode flag or empty
    OSG_FLASH="$U/flash" OSG_HEAL="$U/storage/heal" OSG_MOD_BASE="$U/modules" OSG_RUN_REL=7.2.0 \
    OSG_NO_REMOUNT=1 TRIPWIRE_LOG="$U/trip.log" OSG_MARKER="$U/marker" ETK_ROOT="$U/noetk" \
    sh "$GUARD" ${1:-}
}
slot_sha() { sha256sum "$U/flash/KERNEL" | cut -d' ' -f1; }

PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
expect() {   # <label> <want-rc> <want-substring> <command...>
    local label="$1" wrc="$2" wsub="$3"; shift 3
    local out rc; out=$("$@" 2>&1); rc=$?
    if [ "$rc" = "$wrc" ] && printf '%s' "$out" | grep -qF -- "$wsub"; then ok "$label"
    else bad "$label (rc=$rc want $wrc; out: $(printf '%s' "$out" | tail -c 240 | tr '\n' ' '))"; fi
}
[ -s "$TD/stage.sh" ] || echo "note: no KERNELABLREMOTE body extracted (expected when --against a pre-ABL revision)"

# ===== install.sh STEP 6.4 — KERNELABLREMOTE ============================================
reset_unit
expect "stage: GTK boot.img lands, stock parked and proven pristine" 0 "KERNEL_OK chain=abl slot=written slot_sha=$GTK_SHA stock=new stock_md5=$STOCK_MD5 os_md5=$STOCK_MD5 pristine=yes keepalive=on" stage "$TD/gtk.img"
[ "$(slot_sha)" = "$GTK_SHA" ] && ok "slot holds the GTK image byte-exact" || bad "slot sha wrong after stage"
[ "$(md5sum "$U/flash/KERNEL.etk-stock" 2>/dev/null | cut -d' ' -f1)" = "$STOCK_MD5" ] && ok "KERNEL.etk-stock is the pristine stock" || bad "fallback copy wrong/missing"
[ "$(cat "$U/storage/heal/KERNEL.staged.sha256" 2>/dev/null)" = "$GTK_SHA" ] && ok "heal bundle: staged sha banked" || bad "heal bundle sha missing"
[ "$(cat "$U/storage/heal/chain" 2>/dev/null)" = "abl" ] && ok "heal bundle: chain=abl written" || bad "heal bundle chain missing"
[ "$(cat "$U/storage/heal/KERNEL.staged.release" 2>/dev/null)" = "7.2.0" ] && ok "heal bundle: release banked" || bad "heal bundle release missing"
[ -f "$U/storage/KERNEL.staging" ] && bad "staging file left behind" || ok "staging file consumed into the bundle"
grep -q 'remount,rw' "$U/mount.log" && tail -n1 "$U/mount.log" | grep -q 'remount,ro' && ok "flash remounted rw then left ro" || bad "mount sequence: $(tr '\n' '|' < "$U/mount.log")"
[ -e "$U/flash/KERNEL.new" ] && bad "KERNEL.new left behind" || ok "no temp file left on flash"
expect "stage: re-deploy of the same image -> already, stock kept" 0 "slot=already" stage "$TD/gtk.img"
[ "$(md5sum "$U/flash/KERNEL.etk-stock" | cut -d' ' -f1)" = "$STOCK_MD5" ] && ok "re-deploy did not overwrite the fallback" || bad "re-deploy clobbered KERNEL.etk-stock"
expect "stage: a different GTK image replaces the slot, stock kept" 0 "slot=written" stage "$TD/other.img"
[ "$(md5sum "$U/flash/KERNEL.etk-stock" | cut -d' ' -f1)" = "$STOCK_MD5" ] && ok "second deploy kept the ORIGINAL stock parked" || bad "second deploy re-parked a non-stock kernel"

reset_unit
expect "stage: mode=test stages the bundle, slot untouched" 0 "KERNEL_OK chain=abl slot=untouched" stage "$TD/gtk.img" test
[ "$(slot_sha)" = "$STOCK_SHA" ] && ok "test mode left the slot stock" || bad "test mode wrote the slot"
[ -e "$U/flash/KERNEL.etk-stock" ] && bad "test mode parked a fallback" || ok "test mode parked nothing"
[ "$(cat "$U/storage/heal/mode" 2>/dev/null)" = "test" ] && ok "test mode banked mode=test" || bad "mode not banked"
[ -s "$U/mount.log" ] && bad "test mode remounted flash" || ok "test mode never remounted"

reset_unit; cp "$TD/other.img" "$U/flash/KERNEL"
expect "stage: non-stock slot with no parked copy REFUSED" 1 "put stock back by hand" stage "$TD/gtk.img"
[ "$(slot_sha)" = "$(sha256sum "$TD/other.img" | cut -d' ' -f1)" ] && ok "refused slot left as found" || bad "refusal rewrote the slot"
[ -e "$U/flash/KERNEL.etk-stock" ] && bad "refusal parked a non-stock fallback" || ok "refusal parked nothing"
tail -n1 "$U/mount.log" | grep -q 'remount,ro' && ok "refusal left flash ro" || bad "refusal left flash rw"

reset_unit; mkdir -p "$U/flash/EFI"
expect "stage: GRUB-era unit REFUSED" 1 "GRUB is present" stage "$TD/gtk.img"
reset_unit
MOUNT_FAIL=1 expect "stage: remount failure -> KERNEL_FAIL, slot untouched" 1 "cannot remount" stage "$TD/gtk.img"
unset MOUNT_FAIL
[ "$(slot_sha)" = "$STOCK_SHA" ] && ok "slot still stock after mount failure" || bad "mount failure changed the slot"
reset_unit; cp "$TD/gtk.img" "$U/storage/KERNEL.staging"
expect "stage: staging sha mismatch REFUSED" 1 "staging sha mismatch" env HOST_SHA=deadbeef K_RELEASE=7.2.0 K_MODE=default FLASH="$U/flash" HEAL="$U/storage/heal" STAGING="$U/storage/KERNEL.staging" sh "$TD/stage.sh"
[ -e "$U/storage/KERNEL.staging" ] && bad "bad staging file kept" || ok "bad staging file discarded"

# ===== bin/osguard.sh — ABL stand-down ================================================
reset_unit
expect "osguard: ABL unit, nothing deployed -> nothing to guard, rc 0" 0 "nothing to guard" guard --check
stage "$TD/gtk.img" >/dev/null
expect "osguard: slot == staged -> ok (not yet booted)" 0 "sha ok" guard --check
[ -e "$U/flash/KERNEL.gtktest" ] && bad "osguard dropped KERNEL.gtktest on an ABL unit" || ok "osguard wrote no KERNEL.gtktest"
cp "$TD/stock.img" "$U/flash/KERNEL"     # the OS updater put stock back
expect "osguard --check: OS update reverted the slot -> named, rc 2" 2 "Re-run install.sh" guard --check
expect "osguard heal: names it, writes nothing, rc 0" 0 "NOT live" guard
[ "$(slot_sha)" = "$STOCK_SHA" ] && ok "osguard did not rewrite the slot" || bad "osguard rewrote the slot"
grep -q 'Re-run the ETK installer' "$U/marker" 2>/dev/null && ok "osguard left the operator-visible marker" || bad "no marker written"
[ -e "$U/flash/KERNEL.gtktest" ] && bad "osguard dropped KERNEL.gtktest" || ok "still no KERNEL.gtktest"
reset_unit; stage "$TD/gtk.img" test >/dev/null
expect "osguard: mode=test bundle -> nothing to guard" 0 "nothing to guard" guard --check

# ===== uninstall.sh — ABLRESTORE =======================================================
reset_unit; stage "$TD/gtk.img" >/dev/null; : > "$U/mount.log"
expect "uninstall: parked stock restored, sha-verified, pristine -> parked copy removed" 0 "Removed: KERNEL.etk-stock" restore
[ "$(slot_sha)" = "$STOCK_SHA" ] && ok "slot is stock byte-exact after uninstall" || bad "uninstall left the wrong bytes"
[ -e "$U/flash/KERNEL.etk-stock" ] && bad "pristine parked copy not removed" || ok "parked copy gone"
[ -e "$U/storage/heal/chain" ] && bad "heal chain marker survived" || ok "heal chain marker cleared"
tail -n1 "$U/mount.log" | grep -q 'remount,ro' && ok "uninstall left flash ro" || bad "uninstall left flash rw"
reset_unit; stage "$TD/gtk.img" >/dev/null; printf 'ffffffffffffffffffffffffffffffff  target/KERNEL\n' > "$U/flash/KERNEL.md5"
expect "uninstall: parked copy not provably pristine -> restored but KEPT" 0 "Kept: KERNEL.etk-stock" restore
[ -f "$U/flash/KERNEL.etk-stock" ] && ok "unproven parked copy kept" || bad "unproven parked copy deleted"
reset_unit; : > "$U/mount.log"
restore >/dev/null 2>&1 \&\& ok "uninstall: nothing parked -> no-op, rc 0" || bad "uninstall: nothing parked returned nonzero"
[ -s "$U/mount.log" ] && bad "no-op remounted flash" || ok "no-op touched nothing"
reset_unit; stage "$TD/gtk.img" >/dev/null
MOUNT_FAIL=1 expect "uninstall: remount failure -> FAILED, slot left as is" 1 "FAILED" restore
unset MOUNT_FAIL
[ "$(slot_sha)" = "$GTK_SHA" ] && ok "failed restore left the GTK slot intact" || bad "failed restore corrupted the slot"

echo
echo "test_kernel_abl: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" = 0 ]
