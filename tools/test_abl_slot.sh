#!/bin/bash
# test_abl_slot.sh — pins tools/abl_slot.sh (the ABL-era KERNEL slot tool).
#
# WHY: under ROCKNIX-ABL (20261001+) the one file the bootloader loads is
# /flash/KERNEL, a boot.img; there is no GRUB entry to fall back to. The tool is
# the only writer of that slot for a GTK kernel, so every refusal (not a
# boot.img, no keepalive, wrong DTB count, GRUB-era unit, non-stock slot with no
# fallback, wrong car, mount failure) and every write (park stock once, sha-verified
# read-back, expected sha banked) must hold BEFORE it touches car12.
#
# HOW: sandbox with a fake `ssh`/`scp` on PATH that run the tool's remote bodies
# LOCALLY against a fake unit tree ($TD/unit: flash/ storage/ proc/ sys/ modules/),
# with stubs for mount/dmesg/lsmod/uname. Boot images are real mkbootimg v0 images
# around a fake Image + 9 tiny DTBs. Needs mkbootimg + dtc (Fedora: android-tools,
# dtc). No rig, no network. Host GNU tools stand in for BusyBox: the remote bodies
# are written POSIX-only (manual §Q); the rig run is the discriminating check for that.
#
# DISCRIMINATION: `--against <rev>` takes the tool from that revision. Against
# ee353f3 (before the tool existed) every case FAILS.
#
#   tools/test_abl_slot.sh                   # working tree — must PASS
#   tools/test_abl_slot.sh --against ee353f3 # pre-tool — must FAIL

set -u
cd "$(dirname "$0")/.." || exit 1
REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"
command -v mkbootimg >/dev/null || { echo "SKIP: mkbootimg not on PATH"; exit 0; }
command -v dtc >/dev/null || { echo "SKIP: dtc not on PATH"; exit 0; }

TD=$(mktemp -d); [ -n "${KEEP:-}" ] || trap 'rm -rf "$TD"' EXIT; [ -n "${KEEP:-}" ] && echo "sandbox: $TD"
mkdir -p "$TD/bin" "$TD/stubs" "$TD/units" "$TD/tools" "$TD/scripts" "$TD/img"
if [ -n "$REV" ]; then
    git show "$REV:tools/abl_slot.sh" > "$TD/tools/abl_slot.sh" 2>/dev/null || printf '#!/bin/bash\necho "no tools/abl_slot.sh at %s"; exit 99\n' "$REV" > "$TD/tools/abl_slot.sh"
else cp tools/abl_slot.sh "$TD/tools/abl_slot.sh"; fi
cp scripts/etk_car.sh "$TD/scripts/etk_car.sh"
chmod +x "$TD/tools/abl_slot.sh"
TOOL="$TD/tools/abl_slot.sh"

# --- boot images: fake Image (ARM64 magic + Linux version string) + 9 DTBs -----
STOCK_CMD='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 video=efifb:off gpt'
GTK_CMD="$STOCK_CMD msm.context_keepalive=1 panic=30"
mkimage() {  # $1 out  $2 cmdline  $3 ndtbs  $4 version-string
    python3 -I - "$TD/img" "$4" <<'PY'
import sys, gzip, os
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
mkimage "$TD/nokeep.img" "$STOCK_CMD" 9 "(root@rocknix-gtk) (gcc-15 (Debian 15.3.0-4) 15.3.0)"
mkimage "$TD/eight.img" "$GTK_CMD"   8 "(root@rocknix-gtk) (gcc-15 (Debian 15.3.0-4) 15.3.0)"
printf 'not a boot image at all\n' > "$TD/raw.img"
GTK_SHA=$(sha256sum "$TD/gtk.img" | cut -d' ' -f1)
STOCK_SHA=$(sha256sum "$TD/stock.img" | cut -d' ' -f1)
STOCK_MD5=$(md5sum "$TD/stock.img" | cut -d' ' -f1)

# --- the fake unit ---------------------------------------------------------------
U="$TD/unit"
export ABL_FLASH="$U/flash" ABL_STG="$U/storage/rocknix-gtk/abl" ABL_PROC="$U/proc" ABL_SYS="$U/sys" ABL_MODROOT="$U/modules"
reset_unit() {   # fresh stock ABL unit, booted on stock
    rm -rf "$U"; mkdir -p "$U/flash" "$U/storage" "$U/proc/device-tree" "$U/proc/asound" \
        "$U/sys/class/drm/card0-DSI-1" "$U/sys/kernel/debug/dri/0" "$U/modules/7.2.0"
    cp "$TD/stock.img" "$U/flash/KERNEL"
    printf '%s  target/KERNEL\n' "$STOCK_MD5" > "$U/flash/KERNEL.md5"
    boot_as stock
    printf 'Retroid Pocket Flip2 Visionox\0' > "$U/proc/device-tree/model"
    printf ' 0 [SM8250 ]: fake\n' > "$U/proc/asound/cards"
    echo connected > "$U/sys/class/drm/card0-DSI-1/status"
    printf '\tmode: "1080x1920": 120 263424 1080 1096 1098 1120 1920 1940 1944 1960 0x48 0x0\n' > "$U/sys/kernel/debug/dri/0/state"
    printf '[    1.0] adreno 3d00000.gpu: supply vdd not found, using dummy regulator\n[    1.1] msm_dpu ae01000.display-controller: bound 3d00000.gpu (ops a6xx_gpu_funcs)\n' > "$U/dmesg"
    printf 'Module                  Size  Used by\nfake_a 1 0\nfake_b 1 0\n' > "$U/lsmod"
    echo 7.2.0 > "$U/release"
    : > "$U/mount.log"
}
boot_as() {   # stock | gtk — what the fake unit is RUNNING
    if [ "$1" = gtk ]; then
        printf '%s\n' "$GTK_CMD" > "$U/proc/cmdline"
        echo 'Linux version 7.2.0 (root@rocknix-gtk) (gcc-15 (Debian 15.3.0-4) 15.3.0) #1 SMP PREEMPT' > "$U/proc/version"
        mkdir -p "$U/sys/module/msm/parameters"; echo 1 > "$U/sys/module/msm/parameters/context_keepalive"
    else
        printf '%s\n' "$STOCK_CMD" > "$U/proc/cmdline"
        echo 'Linux version 7.2.0 (@0b091aade48d) (aarch64-rocknix-linux-gnu-gcc-15.2.0 (GCC) 15.2.0) #1 SMP PREEMPT' > "$U/proc/version"
        rm -rf "$U/sys/module"
    fi
}

# --- fakes: ssh runs the remote body locally; scp copies into the unit -------------
cat > "$TD/bin/ssh" <<'FAKE'
#!/bin/bash
target=""
while [ $# -gt 0 ]; do case "$1" in -o|-i|-p) shift 2 ;; -*) shift ;; *) target="$1"; shift; break ;; esac; done
cmd="$*"; host="${target#*@}"
[ -f "$UNITS/$host" ] || exit 255
if printf '%s' "$cmd" | grep -q ETK_CAR_PROBE_OK; then cat "$UNITS/$host"; echo ETK_CAR_PROBE_OK; exit 0; fi
export PATH="$STUBS:$PATH"
exec bash -c "$cmd"
FAKE
cat > "$TD/bin/scp" <<'FAKE'
#!/bin/bash
args=(); while [ $# -gt 0 ]; do case "$1" in -o|-i|-P) shift 2 ;; -*) shift ;; *) args+=("$1"); shift ;; esac; done
src="${args[0]}"; dst="${args[1]}"; dst="${dst#*:}"
[ -f "$UNITS/${args[1]%%:*}" ] || [ -f "$UNITS/${args[1]#*@}" ] || true
cp "$src" "$dst"
FAKE
cat > "$TD/stubs/mount" <<'FAKE'
#!/bin/bash
echo "$*" >> "$UNIT/mount.log"
[ -n "${MOUNT_FAIL:-}" ] && exit 1
exit 0
FAKE
printf '#!/bin/bash\ncat "$UNIT/dmesg"\n' > "$TD/stubs/dmesg"
printf '#!/bin/bash\ncat "$UNIT/lsmod"\n' > "$TD/stubs/lsmod"
printf '#!/bin/bash\n[ "$1" = -r ] && { cat "$UNIT/release"; exit 0; }\nexec /usr/bin/uname "$@"\n' > "$TD/stubs/uname"
chmod +x "$TD/bin/"* "$TD/stubs/"*
export PATH="$TD/bin:$PATH" UNITS="$TD/units" STUBS="$TD/stubs" UNIT="$U"
printf 'car=car12\nmodel=Retroid Pocket Flip2 Visionox\nmemkb=11809768\npanel=vtdr6130,rpflip2\nhost=sm8250-12gb\n' > "$TD/units/car12host"

PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
expect() {   # <label> <want-rc> <want-substring> <command...>
    local label="$1" wrc="$2" wsub="$3"; shift 3
    local out rc; out=$("$@" 2>&1); rc=$?
    if [ "$rc" = "$wrc" ] && printf '%s' "$out" | grep -qF -- "$wsub"; then ok "$label"
    else bad "$label (rc=$rc want $wrc; out: $(printf '%s' "$out" | tail -c 220 | tr '\n' ' '))"; fi
}
slot_sha() { sha256sum "$U/flash/KERNEL" | cut -d' ' -f1; }

# --- status -----------------------------------------------------------------------
reset_unit
expect "status: stock unit, nothing parked"         0 "fallback  : NONE parked" "$TOOL" status root@car12host --car car12
expect "status: reports the slot is stock"           0 "is stock: yes"          "$TOOL" status root@car12host
expect "status: unreachable unit -> rc 1"            1 "could not read"         "$TOOL" status root@nowhere
[ -s "$U/mount.log" ] && bad "status wrote to the unit (mount called)" || ok "status never remounts"

# --- stage refusals (slot must stay stock after each) -------------------------------
expect "stage: raw file refused"                     1 "not an Android boot.img" "$TOOL" stage root@car12host "$TD/raw.img"
expect "stage: boot.img without keepalive refused"   1 "lacks msm.context_keepalive=1" "$TOOL" stage root@car12host "$TD/nokeep.img"
expect "stage: 8-DTB image refused"                  1 "carries 8 DTBs"          "$TOOL" stage root@car12host "$TD/eight.img"
expect "stage: wrong car refused"                    1 "is not car8"             "$TOOL" stage root@car12host "$TD/gtk.img" --car car8
[ "$(slot_sha)" = "$STOCK_SHA" ] && ok "slot untouched by every host-side refusal" || bad "a refusal changed the slot"
[ -e "$U/flash/KERNEL.etk-stock" ] && bad "a refusal parked a fallback" || ok "no fallback parked by refusals"

mkdir -p "$U/flash/EFI"
expect "stage: GRUB-era unit refused"                1 "GRUB is present"        "$TOOL" stage root@car12host "$TD/gtk.img"
rm -rf "$U/flash/EFI"
cp "$TD/nokeep.img" "$U/flash/KERNEL"
expect "stage: non-stock slot with no fallback refused" 1 "restore stock by hand" "$TOOL" stage root@car12host "$TD/gtk.img"
[ "$(sha256sum "$U/flash/KERNEL" | cut -d' ' -f1)" = "$(sha256sum "$TD/nokeep.img" | cut -d' ' -f1)" ] && ok "refused slot left as found" || bad "refusal rewrote the slot"
reset_unit
MOUNT_FAIL=1 expect "stage: remount rw failure -> SLOT_FAIL, slot untouched" 1 "cannot remount" "$TOOL" stage root@car12host "$TD/gtk.img"
unset MOUNT_FAIL
[ "$(slot_sha)" = "$STOCK_SHA" ] && ok "slot still stock after mount failure" || bad "mount failure changed the slot"

# --- the real stage ----------------------------------------------------------------
reset_unit
expect "stage: GTK boot.img lands, stock parked, pristine" 0 "SLOT_OK slot_sha=$GTK_SHA stock=new stock_md5=$STOCK_MD5 os_md5=$STOCK_MD5 pristine=yes" "$TOOL" stage root@car12host "$TD/gtk.img" --car car12
[ "$(slot_sha)" = "$GTK_SHA" ] && ok "slot holds the GTK image byte-exact" || bad "slot sha wrong after stage"
[ "$(md5sum "$U/flash/KERNEL.etk-stock" | cut -d' ' -f1)" = "$STOCK_MD5" ] && ok "KERNEL.etk-stock is the pristine stock" || bad "fallback copy wrong"
[ "$(cat "$U/storage/rocknix-gtk/abl/expected.sha256")" = "$GTK_SHA" ] && ok "expected sha banked for verify" || bad "expected sha missing"
[ "$(cat "$U/storage/rocknix-gtk/abl/expected.name")" = "gtk.img" ] && ok "expected name banked" || bad "expected name missing"
grep -q 'remount,rw' "$U/mount.log" && tail -n1 "$U/mount.log" | grep -q 'remount,ro' && ok "flash remounted rw then left ro" || bad "mount sequence: $(tr '\n' '|' < "$U/mount.log")"
[ -e "$U/flash/KERNEL.new" ] && bad "KERNEL.new left behind" || ok "no temp file left on flash"
expect "stage: idempotent re-stage keeps the parked stock"   0 "stock=kept"           "$TOOL" stage root@car12host "$TD/gtk.img"
[ "$(md5sum "$U/flash/KERNEL.etk-stock" | cut -d' ' -f1)" = "$STOCK_MD5" ] && ok "re-stage did not overwrite the fallback" || bad "re-stage clobbered KERNEL.etk-stock"
expect "status: after stage shows staged = slot"             0 "= slot"               "$TOOL" status root@car12host

# --- verify: the surface after the cold boot ----------------------------------------
boot_as stock
expect "verify: unit still running STOCK after stage -> FAIL" 1 "running cmdline != slot cmdline" "$TOOL" verify root@car12host "$TD/gtk.img"
expect "verify: ...and names the missing keepalive"            1 "keepalive NOT on the live cmdline" "$TOOL" verify root@car12host
boot_as gtk
expect "verify: booted the GTK slot -> PASS"                  0 "ABL_SLOT_VERIFY PASS" "$TOOL" verify root@car12host "$TD/gtk.img" --car car12
expect "verify: matches the image's Linux version"            0 "running kernel is the image's build" "$TOOL" verify root@car12host "$TD/gtk.img"
expect "verify: without an image, judges against the banked sha" 0 "slot holds the staged gtk.img" "$TOOL" verify root@car12host
expect "verify: ABL appended nothing"                         0 "nothing appended"     "$TOOL" verify root@car12host
rm -rf "$U/modules/7.2.0"
expect "verify: missing module tree -> FAIL (frankenboot class)" 1 "NO module tree"    "$TOOL" verify root@car12host
mkdir -p "$U/modules/7.2.0"
echo disconnected > "$U/sys/class/drm/card0-DSI-1/status"
expect "verify: panel down -> FAIL"                           1 "panel DSI-1 not connected" "$TOOL" verify root@car12host
echo connected > "$U/sys/class/drm/card0-DSI-1/status"
cp "$TD/stock.img" "$U/flash/KERNEL"
expect "verify: slot changed since stage -> FAIL"             1 "slot sha" "$TOOL" verify root@car12host "$TD/gtk.img"
cp "$TD/gtk.img" "$U/flash/KERNEL"
[ -s "$U/mount.log" ] && : > "$U/mount.log"
"$TOOL" verify root@car12host >/dev/null 2>&1
[ -s "$U/mount.log" ] && bad "verify wrote to the unit (mount called)" || ok "verify never remounts"

# --- restore ------------------------------------------------------------------------
expect "restore: stock back in the slot, pristine"           0 "SLOT_OK restored slot_sha=$STOCK_SHA md5=$STOCK_MD5 os_md5=$STOCK_MD5 pristine=yes" "$TOOL" restore root@car12host --car car12
[ "$(slot_sha)" = "$STOCK_SHA" ] && ok "slot is stock byte-exact after restore" || bad "restore left the wrong bytes"
[ -e "$U/storage/rocknix-gtk/abl/expected.sha256" ] && bad "expected sha survived restore" || ok "expected sha cleared by restore"
[ -f "$U/flash/KERNEL.etk-stock" ] && ok "fallback copy kept after restore" || bad "restore deleted the fallback"
rm -f "$U/flash/KERNEL.etk-stock"
expect "restore: nothing parked -> SLOT_FAIL"                1 "no $U/flash/KERNEL.etk-stock" "$TOOL" restore root@car12host

# --- restore --card (the unit did not boot; card in the Air) --------------------------
mkdir -p "$TD/card"; cp "$TD/gtk.img" "$TD/card/KERNEL"; cp "$TD/stock.img" "$TD/card/KERNEL.etk-stock"
printf '%s  target/KERNEL\n' "$STOCK_MD5" > "$TD/card/KERNEL.md5"
expect "restore --card: stock back on the mounted card"      0 "SLOT_OK restored on card" "$TOOL" restore --card "$TD/card"
[ "$(sha256sum "$TD/card/KERNEL" | cut -d' ' -f1)" = "$STOCK_SHA" ] && ok "card KERNEL is stock byte-exact" || bad "card restore wrote wrong bytes"
expect "restore --card: card without a parked stock refused" 1 "no $TD/img/KERNEL.etk-stock" "$TOOL" restore --card "$TD/img"
expect "restore --card: not a directory refused"             1 "not a directory"       "$TOOL" restore --card "$TD/nope"

echo
echo "test_abl_slot: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" = 0 ]
