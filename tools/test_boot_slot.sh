#!/bin/bash
# test_boot_slot.sh — pins tools/boot_slot.sh (the OpenABL boot-state instrument).
#
# WHY: verify is the gate on P3 step 3's install round trip. A verify that passes a boot of the
# wrong entry, or a /flash that drifted from the bundle, would close that gate on a broken rig.
#
# HOW: a fake `ssh` runs the remote body locally against a fake unit, with `lsmod`, `dmesg`
# and `uname` stubs. The fixture is rendered by the real bin/bls_deploy.sh, so an entry-format
# change cannot pass here unnoticed. Host GNU tools stand in for BusyBox; the live `status`
# on a rig checks the read body.
#
# DISCRIMINATION: `--against <rev>` takes the tool from that revision. Before the tool existed
# the suite FAILS.
#
#   tools/test_boot_slot.sh                  # working tree — must PASS
#   tools/test_boot_slot.sh --against main   # pre-tool — must FAIL

set -u
cd "$(dirname "$0")/.." || exit 1
REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"
TD=$(mktemp -d); trap 'rm -rf "$TD"' EXIT
mkdir -p "$TD/bin" "$TD/stubs" "$TD/units" "$TD/tools" "$TD/scripts"
if [ -n "$REV" ]; then
    git show "$REV:tools/boot_slot.sh" > "$TD/tools/boot_slot.sh" 2>/dev/null \
        || printf '#!/bin/bash\necho "no tools/boot_slot.sh at %s"; exit 99\n' "$REV" > "$TD/tools/boot_slot.sh"
else cp tools/boot_slot.sh "$TD/tools/boot_slot.sh"; fi
chmod +x "$TD/tools/boot_slot.sh"; cp scripts/etk_car.sh "$TD/scripts/etk_car.sh"
TOOL="$TD/tools/boot_slot.sh"

U="$TD/unit"; F="$U/flash"; H="$U/heal"; E="$F/loader/entries"
OPT='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 video=efifb:off gpt'
GTKOPT="$OPT msm.context_keepalive=1 panic=30"
V=sm8250-retroidpocket-flip2-visionox.dtb
export BS_FLASH="$F" BS_HEAL="$H" BS_PROC="$U/proc" BS_SYS="$U/sys" BS_MODROOT="$U/mods" BS_KIT="$U/kit" BS_OSREL="$U/os-release"
export BLS_NO_REMOUNT=1 BLS_SPLICER="$U/none.py"

mk_pe() { { printf 'MZ'; head -c 4096 /dev/zero; printf 'Linux version 7.2.0 (root@%s) #1\n' "$2"; } > "$1"; }
mk_unit() {   # a healthy car12 on etk-gtk
    rm -rf "$U"; mkdir -p "$E" "$F/dtbs" "$H" "$U/proc/device-tree/sound" "$U/proc/asound" "$U/mods/7.2.0" "$U/kit/bin" "$U/sys/module/msm/parameters" "$U/sys/class/drm/card0-DSI-1"
    cp bin/bls_deploy.sh "$U/kit/bin/bls_deploy.sh"
    printf 'OS_VERSION="20261101"\n' > "$U/os-release"
    printf 'Retroid Pocket Flip2 Visionox\0' > "$U/proc/device-tree/model"
    printf 'Microphone\0Internal Mic\0' > "$U/proc/device-tree/sound/widgets"
    printf 'Linux version 7.2.0 (root@rocknix-gtk) (gcc-16) #1 SMP\n' > "$U/proc/version"
    printf '/dev/mmcblk0p2 /storage ext4 rw 0 0\n' > "$U/proc/mounts"
    printf ' 0 [sound ]: x\n' > "$U/proc/asound/cards"
    printf '%s\n' "$GTKOPT" > "$U/proc/cmdline"
    echo Y > "$U/sys/module/msm/parameters/context_keepalive"
    echo connected > "$U/sys/class/drm/card0-DSI-1/status"
    mk_pe "$F/KERNEL" stock
    printf 'title      ROCKNIX\nversion    20261101\nlinux      /KERNEL\noptions    %s\n' "$OPT" > "$E/rocknix.conf"
    printf 'stock dtb\n' > "$F/dtbs/$V"
    mk_pe "$H/KERNEL.staged" rocknix-gtk
    sha256sum "$H/KERNEL.staged" | cut -d' ' -f1 > "$H/KERNEL.staged.sha256"
    echo KERNEL.rocknix-gtk-20261101-0.7.0 > "$H/KERNEL.staged.name"
    echo 'msm.context_keepalive=1 panic=30' > "$H/cmdline_extra"
    echo default > "$H/mode"; echo bls > "$H/chain"
    BLS_FLASH="$F" BLS_HEAL="$H" BLS_PROC="$U/proc" ETK_ROOT="$U/kit" sh bin/bls_deploy.sh > "$U/deploy.out" 2>&1
}
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
cat > "$TD/stubs/lsmod" <<'FAKE'
#!/bin/bash
echo "Module Size Used"; for i in $(seq 1 ${MODS:-50}); do echo "m$i 1 0"; done
FAKE
cat > "$TD/stubs/uname" <<'FAKE'
#!/bin/bash
echo 7.2.0
FAKE
cat > "$TD/stubs/dmesg" <<'FAKE'
#!/bin/bash
cat <<EOD
[    0.60] msm_dpu ae01000.display-controller: loaded qcom/a650_sqe.fw from new location
[    ${DSI_S:-0.57}] msm_dpu ae01000.display-controller: bound ae94000.dsi (ops 0x0)
[    2.19] EXT4-fs (mmcblk0p2): mounted filesystem x r/w
EOD
FAKE
chmod +x "$TD/bin/"* "$TD/stubs/"*
export PATH="$TD/bin:$PATH" UNITS="$TD/units" STUBS="$TD/stubs"
printf 'car=car12\nmodel=Retroid Pocket Flip2 Visionox\nmemkb=11809768\npanel=vtdr6130,rpflip2\nhost=sm8250-12gb\n' > "$TD/units/car12host"
printf 'car=car8\nmodel=Retroid Pocket Flip2\nmemkb=7735000\npanel=\nhost=SM8250\n' > "$TD/units/car8host"
unset RIG_SSH CAR
T=root@car12host

PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
expect() {   # <label> <want-rc> <want-substring> <command...>
    local label="$1" wrc="$2" wsub="$3"; shift 3
    local out rc; out=$("$@" 2>&1); rc=$?
    if [ "$rc" = "$wrc" ] && printf '%s' "$out" | grep -qF -- "$wsub"; then ok "$label"
    else bad "$label (rc=$rc want $wrc; out: $(printf '%s' "$out" | tail -c 240 | tr '\n' ' '))"; fi
}
snap() { (cd "$U" && find flash heal -type f | LC_ALL=C sort | xargs sha256sum); }

mk_unit
grep -q 'BLS_DEPLOY_OK' "$U/deploy.out" && ok "fixture: rendered by the real bls_deploy.sh" || bad "fixture: rendered by the real bls_deploy.sh ($(cat "$U/deploy.out"))"
S0=$(snap)
expect "verify: a healthy GTK boot passes"            0 "BOOT_SLOT_VERIFY PASS"   "$TOOL" verify --car car12 "$T"
expect "verify: names the entry that booted"          0 "booted etk-gtk"          "$TOOL" verify "$T"
expect "verify: the entry proof is the whole line"    0 "its options line, whole" "$TOOL" verify "$T"
expect "verify: no drift"                             0 "osguard has nothing to heal" "$TOOL" verify "$T"
expect "status: names the entry"                      0 "booted   : etk-gtk"      "$TOOL" status "$T"
expect "status: names the OS"                         0 "ROCKNIX 20261101"        "$TOOL" status "$T"
[ "$(snap)" = "$S0" ] && ok "status and verify wrote nothing" || bad "status and verify wrote nothing"
expect "gate: another car"                            1 "is not car12"            "$TOOL" verify --car car12 root@car8host

mk_pe "$U/art" rocknix-gtk
expect "artifact: matches"                            0 "IS the artifact"         "$TOOL" verify "$U/art" "$T"
mk_pe "$U/art" other
expect "artifact: a different kernel fails"           1 "is not the artifact"     "$TOOL" verify "$U/art" "$T"

mk_unit; printf '%s\n' "$OPT" > "$U/proc/cmdline"
expect "fail: a stock boot on a GTK rig"              1 "this boot is not etk-gtk" "$TOOL" verify "$T"
expect "status: a stock boot says it cannot tell"     0 "cannot tell which"       "$TOOL" status "$T"
mk_unit; printf '%s extra=1\n' "$GTKOPT" > "$U/proc/cmdline"
expect "fail: a cmdline that is not the entry's"      1 "this boot is not etk-gtk" "$TOOL" verify "$T"
expect "status: no entry matches"                     0 "UNKNOWN"                 "$TOOL" status "$T"
mk_unit; echo tampered >> "$F/KERNEL.gtk"
expect "fail: KERNEL.gtk drifted from the bundle"     1 "!= bundle"               "$TOOL" verify "$T"
mk_unit; printf 'default etk-stock.conf\n' > "$F/loader/loader.conf"
expect "fail: mode=default, default names stock"      1 "mode=default, but"       "$TOOL" verify "$T"
mk_unit; sed -i 's/^options .*/& newtoken=1/' "$E/rocknix.conf"
expect "fail: stock's options moved under the entry"  1 "BLS_DEPLOY_DRIFT"        "$TOOL" verify "$T"
mk_unit; rm "$F/dtbs/etk/$V"
expect "fail: the entry names a missing DTB"          1 "names a DTB that is not there" "$TOOL" verify "$T"
mk_unit; rm "$E/etk-stock.conf"
expect "fail: no floor entry"                         1 "etk-stock.conf is missing" "$TOOL" verify "$T"
mk_unit; echo abl > "$H/chain"
expect "fail: install has not deployed on this chain" 1 "chain='abl'"             "$TOOL" verify "$T"
mk_unit; printf 'Linux version 7.2.0 (@stock) #1\n' > "$U/proc/version"
expect "fail: not a GTK build"                        1 "not a GTK build"         "$TOOL" verify "$T"
mk_unit; rm "$U/sys/module/msm/parameters/context_keepalive"
expect "fail: the param is missing"                   1 "param reads ''"          "$TOOL" verify "$T"
mk_unit; MODS=0 expect "fail: zero modules"           1 "zero modules"            env MODS=0 "$TOOL" verify "$T"
mk_unit; rmdir "$U/mods/7.2.0"
expect "fail: no module tree"                         1 "NO module tree"          "$TOOL" verify "$T"
mk_unit; echo disconnected > "$U/sys/class/drm/card0-DSI-1/status"
expect "fail: the panel is down"                      1 "not connected"           "$TOOL" verify "$T"
mk_unit
expect "fail: boot logo after the root mount"         1 "boot logo MISSING"       env DSI_S=3.90 "$TOOL" verify "$T"
mk_unit; : > "$U/proc/device-tree/sound/widgets"
expect "skip: no mic is named, never a pass claim"    0 "SKIP: no Internal Mic"   "$TOOL" verify "$T"

mk_unit; echo test > "$H/mode"; printf 'default etk-stock.conf\n' > "$F/loader/loader.conf"; printf '%s\n' "$OPT" > "$U/proc/cmdline"
printf 'Linux version 7.2.0 (@stock) #1\n' > "$U/proc/version"
expect "mode=test: a stock boot passes"               0 "BOOT_SLOT_VERIFY PASS"   "$TOOL" verify "$T"

mk_unit; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$F/KERNEL"
expect "refuse: an ABL rig points at abl_slot.sh"     1 "tools/abl_slot.sh"       "$TOOL" status "$T"
mk_unit; rm "$E/rocknix.conf"
expect "refuse: no entries"                           1 "not on OpenABL"          "$TOOL" verify "$T"
expect "refuse: an unreachable unit"                  1 "could not read"          "$TOOL" status root@nothere
expect "usage: an unknown mode"                       1 "unknown mode"            "$TOOL" bogus "$T"

echo
echo "test_boot_slot: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
