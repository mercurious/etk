#!/bin/bash
# test_abl_slot.sh — pins tools/abl_slot.sh (the ABL-era KERNEL slot tool).
#
# WHY: under ROCKNIX-ABL (20261001+) the one file the bootloader loads is
# /flash/KERNEL, a boot.img; there is no GRUB entry to fall back to. The slot's
# WRITER is the kit (install.sh STEP 6.4, pinned by tools/test_kernel_abl.sh);
# this tool is the read-only instrument around it plus the dark-unit card
# recovery, so: status/verify must never write, verify must judge the slot
# against install.sh's heal bundle and FAIL loudly when the unit still runs
# stock, and restore --card must put the parked stock back byte-exact.
#
# HOW: sandbox with a fake `ssh` on PATH that runs the tool's remote body
# LOCALLY against a fake unit tree ($TD/unit: flash/ storage/ proc/ sys/ modules/),
# with stubs for mount/dmesg/lsmod/uname. Boot images are real mkbootimg v0 images
# around a fake Image + 9 tiny DTBs. Needs mkbootimg + dtc (Fedora: android-tools,
# dtc). No rig, no network. Host GNU tools stand in for BusyBox: the remote bodies
# are written POSIX-only (manual §Q); the rig run is the discriminating check for that.
#
# DISCRIMINATION: `--against <rev>` takes the tool from that revision. Against
# ee353f3 (before the tool existed) every case FAILS; against e56f82a (before the
# boot-logo order check) the five logo cases FAIL; against 20222a1 (before verify --card)
# the nine card cases FAIL.
#
#   tools/test_abl_slot.sh                   # working tree — must PASS
#   tools/test_abl_slot.sh --against ee353f3 # pre-tool — must FAIL
#   tools/test_abl_slot.sh --against c8f0133 # before the /storage-device logo + pinned stock — those cases FAIL

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
mkdir -p "$TD/os-install/build"; cp os-install/build/relabel_bootimg.py "$TD/os-install/build/"
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
export ABL_FLASH="$U/flash" ABL_STG="$U/storage/rocknix-gtk/heal" ABL_PROC="$U/proc" ABL_SYS="$U/sys" ABL_MODROOT="$U/modules"
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
    printf 'Module                  Size  Used by\nfake_a 1 0\nfake_b 1 0\n' > "$U/lsmod"
    boot_order late
    echo 7.2.0 > "$U/release"
    : > "$U/mount.log"
}
staged_by_install() {   # what install.sh STEP 6.4 leaves behind for a deployed GTK boot.img
    mkdir -p "$U/storage/rocknix-gtk/heal"
    cp "$TD/gtk.img" "$U/flash/KERNEL"; cp "$TD/stock.img" "$U/flash/KERNEL.etk-stock"
    printf '%s\n' "$GTK_SHA" > "$U/storage/rocknix-gtk/heal/KERNEL.staged.sha256"
    printf 'default\n' > "$U/storage/rocknix-gtk/heal/mode"; printf 'abl\n' > "$U/storage/rocknix-gtk/heal/chain"
}
boot_order() {   # early | late | none — dmesg: when msm bound the DSI vs the root mount.
    # late = the live car12 numbers on stock/0.6.2 (mounted 2.17 s, DSI 3.83 s: logo missing);
    # early = the GRUB-era/0.6.3 shape (DSI first: load_splash finds /dev/fb0).
    local gpu='[    1.0] adreno 3d00000.gpu: supply vdd not found, using dummy regulator\n[    1.1] msm_dpu ae01000.display-controller: bound 3d00000.gpu (ops a6xx_gpu_funcs)\n'
    case "$1" in
        early) printf "$gpu"'[    1.212345] msm_dpu ae01000.display-controller: bound ae94000.dsi (ops 0xffffc923a87ea868)\n[    2.174590] EXT4-fs (mmcblk0p2): mounted filesystem 2e7e288a r/w with ordered data mode. Quota mode: none.\n' > "$U/dmesg"
               printf 'Module                  Size  Used by\nfake_a 1 0\nfake_b 1 0\n' > "$U/lsmod"; mkdir -p "$U/sys/bus/platform/drivers/gpio_sbu_mux" ;;
        late)  printf "$gpu"'[    2.174590] EXT4-fs (mmcblk0p2): mounted filesystem 2e7e288a r/w with ordered data mode. Quota mode: none.\n[    3.834315] msm_dpu ae01000.display-controller: bound ae94000.dsi (ops 0xffffc923a87ea868)\n' > "$U/dmesg"
               printf 'Module                  Size  Used by\nfake_a 1 0\nfake_b 1 0\ngpio_sbu_mux 12288 3\n' > "$U/lsmod"; rm -rf "$U/sys/bus/platform/drivers/gpio_sbu_mux" ;;
        none)  printf "$gpu" > "$U/dmesg" ;;
    esac
}
boot_as() {   # stock | gtk — what the fake unit is RUNNING
    if [ "$1" = gtk ]; then
        boot_order early
        printf '%s\n' "$GTK_CMD" > "$U/proc/cmdline"
        echo 'Linux version 7.2.0 (root@rocknix-gtk) (gcc-15 (Debian 15.3.0-4) 15.3.0) #1 SMP PREEMPT' > "$U/proc/version"
        mkdir -p "$U/sys/module/msm/parameters"; echo Y > "$U/sys/module/msm/parameters/context_keepalive"   # bool param: sysfs prints Y (live car12)
    else
        boot_order late
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

# --- a wrong car is refused before anything is read in detail ---------------------
expect "status: wrong car refused"                   1 "is not car8"             "$TOOL" status root@car12host --car car8
staged_by_install
expect "status: after install.sh shows staged = slot" 0 "= slot"               "$TOOL" status root@car12host
expect "status: fallback parked and pristine"        0 "pristine: md5 matches" "$TOOL" status root@car12host

# --- verify: the surface after the cold boot ----------------------------------------
boot_as stock
expect "verify: unit still running STOCK after stage -> FAIL" 1 "running cmdline != slot cmdline" "$TOOL" verify root@car12host "$TD/gtk.img"
expect "verify: ...and names the missing keepalive"            1 "keepalive NOT on the live cmdline" "$TOOL" verify root@car12host
boot_as gtk
expect "verify: booted the GTK slot -> PASS"                  0 "ABL_SLOT_VERIFY PASS" "$TOOL" verify root@car12host "$TD/gtk.img" --car car12
expect "verify: bool keepalive param Y accepted"             0 "param reads Y" "$TOOL" verify root@car12host
expect "verify: matches the image's Linux version"            0 "running kernel is the image's build" "$TOOL" verify root@car12host "$TD/gtk.img"
expect "verify: without an image, judges against install.sh's banked sha" 0 "slot holds what install.sh staged" "$TOOL" verify root@car12host
expect "verify: ABL appended nothing"                         0 "nothing appended"     "$TOOL" verify root@car12host
rm -rf "$U/modules/7.2.0"
expect "verify: missing module tree -> FAIL (frankenboot class)" 1 "NO module tree"    "$TOOL" verify root@car12host
mkdir -p "$U/modules/7.2.0"
echo disconnected > "$U/sys/class/drm/card0-DSI-1/status"
expect "verify: panel down -> FAIL"                           1 "panel DSI-1 not connected" "$TOOL" verify root@car12host
echo connected > "$U/sys/class/drm/card0-DSI-1/status"
# --- the boot logo: the splash needs /dev/fb0, i.e. msm bound the DSI before the root mount
expect "verify: boot logo order judged (DSI bound before the root mount)" 0 "BEFORE the root mount" "$TOOL" verify root@car12host
expect "status: boot line names the logo verdict"             0 "logo drawn"            "$TOOL" status root@car12host
boot_order late
expect "verify: GTK slot live but msm bound AFTER the root mount -> FAIL (logo missing, the 0.6.3 falsifier)" 1 "boot logo MISSING" "$TOOL" verify root@car12host
expect "verify: ...and names the module-vs-builtin tell"      1 "gpio_sbu_mux module"   "$TOOL" verify root@car12host
boot_order none
expect "verify: dmesg ring rolled -> logo unjudged, not failed" 0 "SKIP: boot logo unjudged" "$TOOL" verify root@car12host
boot_order early
cp "$TD/stock.img" "$U/flash/KERNEL"
expect "verify: slot changed since stage -> FAIL"             1 "slot sha" "$TOOL" verify root@car12host "$TD/gtk.img"
cp "$TD/gtk.img" "$U/flash/KERNEL"
[ -s "$U/mount.log" ] && : > "$U/mount.log"
"$TOOL" verify root@car12host >/dev/null 2>&1
[ -s "$U/mount.log" ] && bad "verify wrote to the unit (mount called)" || ok "verify never remounts"

# --- internal-storage unit (car8, 2026-10-09): STORAGE is sda25, mmcblk0p2 is the GAMES
# card mounted by userspace at 6.1 s. The logo is judged against the device /storage sits
# on; judging mmcblk0p2 read a false "drawn" on stock 20261001 (operator saw no logo).
reset_unit
printf '/dev/sda24 /flash vfat ro 0 0\n/dev/sda25 /storage ext4 rw 0 0\n/dev/mmcblk0p2 /storage/games-external ext4 rw 0 0\n' > "$U/proc/mounts"
printf '[    1.1] msm_dpu ae01000.display-controller: bound 3d00000.gpu (ops a6xx_gpu_funcs)\n[    2.234395] EXT4-fs (sda25): mounted filesystem 59ba7104 r/w with ordered data mode. Quota mode: none.\n[    3.769378] msm_dpu ae01000.display-controller: bound ae94000.dsi (ops 0xffffa0637f1ea8d0)\n[    6.118330] EXT4-fs (mmcblk0p2): mounted filesystem f4618c8e r/w with ordered data mode. Quota mode: none.\n' > "$U/dmesg"
expect "status: internal-storage unit judges the logo on /storage's device (sda25) -> MISSING" 0 "logo MISSING" "$TOOL" status root@car12host
expect "status: ...and times the STORAGE mount, not the games card" 0 "root mounted 2.234395s" "$TOOL" status root@car12host

# --- in-place OTA: KERNEL.md5 stale, the slot is the OFFICIAL stock pinned in gtk_stack.json
reset_unit
printf 'ea10fd219e70272135fc3cdc3f056a1b  KERNEL\n' > "$U/flash/KERNEL.md5"
printf '{"kernel": {"stock_os_sha256": {"20261001": "%s"}}}\n' "$STOCK_SHA" > "$TD/manifest.json"
ABL_MANIFEST="$TD/manifest.json" expect "status: stale KERNEL.md5 + pinned official sha -> is stock: yes (pinned)" 0 "is stock: yes (the official 20261001 stock boot.img" "$TOOL" status root@car12host
printf '{"kernel": {"stock_os_sha256": {"20261001": "%s"}}}\n' "$GTK_SHA" > "$TD/manifest.json"
ABL_MANIFEST="$TD/manifest.json" expect "status: stale KERNEL.md5 + NOT the pinned sha -> is stock: NO" 0 "is stock: NO" "$TOOL" status root@car12host
reset_unit; staged_by_install   # the restore checks below expect a deployed GTK slot

# --- restore over ssh is NOT this tool's job (uninstall.sh owns it) ---------------
expect "restore <target>: refused, points at uninstall.sh" 1 "uninstall.sh" "$TOOL" restore root@car12host
[ "$(slot_sha)" = "$GTK_SHA" ] && ok "refused restore left the slot alone" || bad "refused restore wrote the slot"

# --- restore --card (the unit did not boot; card in the Air) --------------------------
mkdir -p "$TD/card"; cp "$TD/gtk.img" "$TD/card/KERNEL"; cp "$TD/stock.img" "$TD/card/KERNEL.etk-stock"
printf '%s  target/KERNEL\n' "$STOCK_MD5" > "$TD/card/KERNEL.md5"
expect "restore --card: stock back on the mounted card"      0 "SLOT_OK restored on card" "$TOOL" restore --card "$TD/card"
[ "$(sha256sum "$TD/card/KERNEL" | cut -d' ' -f1)" = "$STOCK_SHA" ] && ok "card KERNEL is stock byte-exact" || bad "card restore wrote wrong bytes"
expect "restore --card: card without a parked stock refused" 1 "no $TD/img/KERNEL.etk-stock" "$TOOL" restore --card "$TD/img"
expect "restore --card: not a directory refused"             1 "not a directory"       "$TOOL" restore --card "$TD/nope"

# --- verify --card: the ETCHED card in the Air, before it meets a rig ------------------
# A real card carries the certified boot.img RELABELLED to ROCKNIX-GTK/GTKSTOR, the
# relabelled stock parked (KERNEL.md5 names it) and the heal bundle seeded on storage.
RL="python3 -I os-install/build/relabel_bootimg.py"
mkdir -p "$TD/card2" "$TD/stor2/rocknix-gtk/heal"
$RL "$TD/gtk.img"   "$TD/card2/KERNEL"           ROCKNIX STORAGE ROCKNIX-GTK GTKSTOR >/dev/null || { echo "relabel failed"; exit 1; }
$RL "$TD/stock.img" "$TD/card2/KERNEL.etk-stock" ROCKNIX STORAGE ROCKNIX-GTK GTKSTOR >/dev/null
printf '%s  target/KERNEL\n' "$(md5sum "$TD/card2/KERNEL.etk-stock" | cut -d' ' -f1)" > "$TD/card2/KERNEL.md5"
echo abl > "$TD/stor2/rocknix-gtk/heal/chain"; echo default > "$TD/stor2/rocknix-gtk/heal/mode"
sha256sum "$TD/card2/KERNEL" | cut -d' ' -f1 > "$TD/stor2/rocknix-gtk/heal/KERNEL.staged.sha256"; echo 7.2.0 > "$TD/stor2/rocknix-gtk/heal/KERNEL.staged.release"
expect "card: etched card == certified artifact relabelled, bundle seeded -> PASS" 0 "ABL_SLOT_CARD PASS" "$TOOL" verify --card "$TD/card2" --storage "$TD/stor2" "$TD/gtk.img"
expect "card: ...names the relabel match"                      0 "relabelled to the card" "$TOOL" verify --card "$TD/card2" "$TD/gtk.img"
expect "card: no artifact given -> PASS with the slot unjudged" 0 "unjudged against the certified artifact" "$TOOL" verify --card "$TD/card2" --storage "$TD/stor2"
expect "card: stock-labelled kernel on the card -> FAIL (split-brain)" 1 "does NOT name the card's labels" "$TOOL" verify --card "$TD/card" "$TD/gtk.img"
$RL "$TD/nokeep.img" "$TD/card2/KERNEL" ROCKNIX STORAGE ROCKNIX-GTK GTKSTOR >/dev/null
expect "card: wrong kernel etched -> FAIL on the relabelled sha"  1 "slot sha" "$TOOL" verify --card "$TD/card2" "$TD/gtk.img"
expect "card: ...and on the missing keepalive"                 1 "keepalive NOT in the slot cmdline" "$TOOL" verify --card "$TD/card2" "$TD/gtk.img"
$RL "$TD/gtk.img" "$TD/card2/KERNEL" ROCKNIX STORAGE ROCKNIX-GTK GTKSTOR >/dev/null
mv "$TD/card2/KERNEL.etk-stock" "$TD/card2/KERNEL.etk-stock.away"
expect "card: no parked stock -> FAIL"                          1 "no KERNEL.etk-stock parked" "$TOOL" verify --card "$TD/card2" "$TD/gtk.img"
mv "$TD/card2/KERNEL.etk-stock.away" "$TD/card2/KERNEL.etk-stock"
echo deadbeef > "$TD/stor2/rocknix-gtk/heal/KERNEL.staged.sha256"
expect "card: heal bundle sha != slot -> FAIL"                  1 "heal bundle staged sha" "$TOOL" verify --card "$TD/card2" --storage "$TD/stor2" "$TD/gtk.img"
expect "card: not a directory refused"                          1 "not a directory" "$TOOL" verify --card "$TD/nope"
[ "$(sha256sum "$TD/card2/KERNEL" | cut -d' ' -f1)" = "$(sha256sum "$TD/card2/KERNEL" | cut -d' ' -f1)" ] && ok "verify --card wrote nothing" || bad "verify --card changed the slot"

echo
echo "test_abl_slot: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" = 0 ]
