#!/bin/bash
# ==========================================================
# KEXEC SPIKE — can SM8250 kexec into the GTK kernel? (K2 decision 1, option (d))
# ==========================================================
# The ABL-era fallback question. Option (a) is live (the GTK boot.img owns
# /flash/KERNEL). Option (d) would leave STOCK in the slot and have an early
# unit kexec the GTK Image with the ETK cmdline + kit DTB: a panic reboots into
# stock and a one-shot flag skips the kexec = automatic fallback, GRUB's job in
# userspace. Unknown until tried on hardware: does a kexec'd kernel bring up
# GPU / ADSP / display on this SoC (firmware already loaded by the first
# kernel), and what does it cost in boot time. This tool is the disposable
# harness for exactly that question (TRACK_MANUAL §1.4) — nothing here is a
# boot path yet.
#
#   stage  <target>     push the kexec binary (the Air's own aarch64 kexec-tools
#                       runs on ROCKNIX: glibc <= 2.38 symbols, libzstd/lzma/z
#                       present), the raw GTK Image + Flip2 Visionox DTB carved
#                       out of the certified boot.img, and the rig-side go
#                       script; sha-verify everything. Read-only on the boot
#                       chain: /flash is never touched.
#   go     <target>     OPERATOR: load the GTK Image with the running cmdline +
#                       `etk_kexec=1` (the proof token) and kexec into it via
#                       systemd's orderly `systemctl kexec` (filesystems
#                       unmounted first; `kexec -e` alone would yank /storage rw).
#                       THIS REBOOTS THE UNIT. Refuses while RPCS3 runs.
#   verify <target>     after it comes back: is the running kernel the kexec'd
#                       one (etk_kexec=1 on /proc/cmdline, GTK build string,
#                       low uptime), and did GPU / panel / audio / modules come
#                       up — plus the kernel's own kexec lines in dmesg.
#   unload <target>     drop a loaded-but-not-executed image (kexec -u).
#
# Spike 1 reuses the RUNNING device tree (/sys/firmware/fdt, which kexec-tools
# rewrites with the new bootargs) — the pure "does kexec work" question. Spike 2
# (later) passes --dtb with the kit DTB splice. GO_DTB=1 passes the staged stock
# Visionox DTB now.
#
# Source of the Image/DTB: KEXEC_BOOTIMG (default the etk.conf KERNEL_IMAGE).
# Rig dir: /storage/rocknix-gtk/kexec/ (persists; nothing boots from it).
# ==========================================================
set -u
ETK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8"
RIGDIR="/storage/rocknix-gtk/kexec"
KEXEC_BIN="${KEXEC_BIN:-/usr/sbin/kexec}"
die() { echo "KEXEC_SPIKE_FAIL: $*" >&2; exit 1; }
usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

MODE="${1:-}"; TARGET="${2:-}"
[ -n "$MODE" ] && [ -n "$TARGET" ] || usage
rssh() { ssh $SSH_OPTS "$TARGET" "$@"; }

bootimg_default() {
    [ -f "$ETK_ROOT/etk.conf" ] && sed -n 's/^KERNEL_IMAGE="\(.*\)"/\1/p' "$ETK_ROOT/etk.conf" | tail -1
}

case "$MODE" in
stage)
    BOOTIMG="${KEXEC_BOOTIMG:-$(bootimg_default)}"
    [ -f "$BOOTIMG" ] || die "no boot.img at '$BOOTIMG' (set KEXEC_BOOTIMG or etk.conf KERNEL_IMAGE)"
    [ -x "$KEXEC_BIN" ] || die "no kexec binary at $KEXEC_BIN (Fedora: dnf install kexec-tools)"
    W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
    python3 -I - "$BOOTIMG" "$W" <<'PY' || die "cannot carve the Image out of the boot.img"
import struct, sys, zlib, os
d = open(sys.argv[1], 'rb').read(); w = sys.argv[2]
assert d[:8] == b'ANDROID!', 'not a boot.img'
f = struct.unpack_from('<8s10I16s512s32s1024s', d, 0); ksz, ps = f[1], f[8]
z = zlib.decompressobj(31); img = z.decompress(d[ps:ps + ksz]); tail = z.unused_data
assert img[0x38:0x3c] == b'ARM\x64', 'payload is not an arm64 Image'
open(os.path.join(w, 'Image'), 'wb').write(img)
p, n, picked = 0, 0, None
while p + 8 <= len(tail):
    m, t = struct.unpack_from('>II', tail, p)
    if m != 0xd00dfeed: break
    blob = tail[p:p + t]
    if b'Retroid Pocket Flip2 Visionox' in blob and picked is None:
        open(os.path.join(w, 'flip2-visionox.dtb'), 'wb').write(blob); picked = n
    n += 1; p += t
assert picked is not None, 'no Flip2 Visionox DTB in the boot.img'
open(os.path.join(w, 'cmdline'), 'w').write(f[12].rstrip(b'\0').decode())
print(f'Image {len(img)} B, dtb[{picked}] = Flip2 Visionox, {n} DTBs total')
PY
    cp "$KEXEC_BIN" "$W/kexec"
    cat > "$W/kexec_go.sh" <<'GO'
#!/bin/sh
# kexec_go.sh — rig side of tools/kexec_spike.sh `go`. OPERATOR-RUN: reboots the unit.
D=/storage/rocknix-gtk/kexec
fail() { echo "KEXEC_GO_FAIL $*"; exit 1; }
for p in /proc/[0-9]*; do [ "$(cat $p/comm 2>/dev/null)" = AppRun.wrapped ] && fail "RPCS3 is running -- exit the game first"; done
[ "$(cat /proc/sys/kernel/kexec_load_disabled 2>/dev/null)" = 0 ] || fail "kexec_load_disabled=1"
[ -x "$D/kexec" ] && [ -f "$D/Image" ] || fail "staged files missing under $D"
[ "$(sha256sum "$D/Image" | cut -d' ' -f1)" = "$(cat "$D/Image.sha256")" ] || fail "Image sha mismatch"
CMD="$(cat /proc/cmdline | sed 's/ *etk_kexec=1//') etk_kexec=1"
DTB=""; [ "${GO_DTB:-0}" = 1 ] && DTB="--dtb=$D/flip2-visionox.dtb"
echo "loading: $D/Image"; echo "cmdline: $CMD"; echo "dtb    : ${DTB:-running /sys/firmware/fdt (rewritten by kexec-tools)}"
"$D/kexec" -c -l "$D/Image" --command-line="$CMD" $DTB || fail "kexec -l refused (rc $?)"
[ "$(cat /sys/kernel/kexec_loaded)" = 1 ] || fail "kexec_loaded != 1 after load"
echo "KEXEC_LOADED ok -- orderly shutdown + kexec in 3 s (systemctl kexec)"
date '+%H:%M:%S' > "$D/last_go"
sync; sleep 3
if systemctl kexec 2>/dev/null; then exit 0; fi
echo "systemctl kexec unavailable -- falling back to sync + kexec -e (filesystems not unmounted)"
sync; sync; "$D/kexec" -e
GO
    sha256sum "$W/Image" | cut -d' ' -f1 > "$W/Image.sha256"
    echo "staging -> $TARGET:$RIGDIR"
    rssh "mkdir -p $RIGDIR" || die "cannot create $RIGDIR"
    scp -q $SSH_OPTS "$W/kexec" "$W/Image" "$W/Image.sha256" "$W/flip2-visionox.dtb" "$W/cmdline" "$W/kexec_go.sh" "$TARGET:$RIGDIR/" || die "scp failed"
    rssh "cd $RIGDIR && chmod +x kexec kexec_go.sh && [ \"\$(sha256sum Image | cut -d' ' -f1)\" = \"\$(cat Image.sha256)\" ] && ./kexec --version && echo STAGE_OK" | tail -2
    echo "baked cmdline: $(cat "$W/cmdline")"
    echo
    echo "NEXT (operator, reboots the unit):  tools/kexec_spike.sh go $TARGET"
    ;;
go)
    echo "This kexecs $TARGET into the staged GTK Image NOW (orderly shutdown first)."
    rssh "GO_DTB='${GO_DTB:-0}' sh $RIGDIR/kexec_go.sh" 2>&1 | grep -v '^Connection to .* closed'
    echo "...link dropped as expected. Give it ~60 s, then: tools/kexec_spike.sh verify $TARGET"
    ;;
verify)
    p=$(rssh "echo cmdline=\$(cat /proc/cmdline); echo version=\$(cat /proc/version | cut -c1-90); echo uptime=\$(cut -d' ' -f1 /proc/uptime); echo last_go=\$(cat $RIGDIR/last_go 2>/dev/null); echo kexec_loaded=\$(cat /sys/kernel/kexec_loaded); echo dsi=\$(cat /sys/class/drm/card*-DSI-1/status | head -1); echo mode=\$(grep -m1 'mode: \"[0-9]' /sys/kernel/debug/dri/0/state | awk '{print \$2, \$3\"Hz\"}'); echo gpu=\$(dmesg | grep -m1 -E 'bound [0-9a-f]+\\.gpu|loaded qcom/a[0-9]+_sqe' | sed 's/^\\[[^]]*\\] //'); echo sound=\$(grep -c '^ *[0-9]' /proc/asound/cards); echo modules=\$(lsmod | tail -n +2 | wc -l); echo modtree=\$([ -d /usr/lib/modules/\$(uname -r) ] && echo present || echo MISSING); echo keepalive=\$(cat /sys/module/msm/parameters/context_keepalive 2>/dev/null); echo kexec_dmesg=\$(dmesg | grep -ciE 'kexec'); echo wifi=\$(ls /sys/class/net | tr '\\n' ' '); echo errors=\$(dmesg | grep -ciE 'fail|error'); echo ABL_OK" 2>/dev/null) || die "cannot reach $TARGET"
    field() { printf '%s\n' "$p" | sed -n "s/^$1=//p" | head -n1; }
    printf '%s\n' "$p" | grep -q '^ABL_OK$' || die "could not read $TARGET"
    PASS=0; FAIL=0
    ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
    bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
    echo "running : $(field version)"
    echo "cmdline : $(field cmdline)"
    echo "uptime  : $(field uptime) s · last go $(field last_go | sed 's/^$/never/') · kexec_loaded now $(field kexec_loaded) · dmesg kexec lines $(field kexec_dmesg) · dmesg fail/error lines $(field errors)"
    printf '%s' "$(field cmdline)" | grep -q 'etk_kexec=1' && ok "running kernel carries the kexec token (this boot came through kexec)" || bad "no etk_kexec=1 on /proc/cmdline -- this is a firmware boot, not a kexec"
    printf '%s' "$(field version)" | grep -q 'rocknix-gtk' && ok "GTK build string" || bad "not the GTK build: $(field version)"
    printf '%s' "$(field cmdline)" | grep -q 'msm.context_keepalive=1' && ok "keepalive on the cmdline (param $(field keepalive))" || bad "keepalive missing"
    [ "$(field modtree)" = present ] && ok "module tree matches" || bad "NO module tree for the running release"
    [ "$(field modules)" -gt 0 ] && ok "$(field modules) modules loaded" || bad "zero modules loaded"
    [ "$(field dsi)" = connected ] && ok "panel DSI-1 connected · $(field mode)" || bad "panel DSI-1 not connected after kexec"
    [ -n "$(field gpu)" ] && ok "GPU: $(field gpu | cut -c1-70)" || bad "no adreno/GPU bind line after kexec"
    [ "$(field sound)" -gt 0 ] && ok "audio: $(field sound) card(s)" || bad "no sound card after kexec (ADSP/q6 did not come back)"
    printf '%s' "$(field wifi)" | grep -q 'wlan' && ok "wifi interface present ($(field wifi))" || bad "no wlan interface after kexec"
    echo
    [ "$FAIL" = 0 ] && { echo "KEXEC_SPIKE_VERIFY PASS ($PASS checks)"; exit 0; } || { echo "KEXEC_SPIKE_VERIFY FAIL ($FAIL failed, $PASS passed)"; exit 1; }
    ;;
unload)
    rssh "$RIGDIR/kexec -u && echo UNLOADED; cat /sys/kernel/kexec_loaded"
    ;;
*) usage ;;
esac
