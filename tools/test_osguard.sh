#!/bin/sh
# ============================================================
# test_osguard.sh — discrimination suite for bin/osguard.sh
# ------------------------------------------------------------
# Replays the 2026-08-01 ROCKNIX-update frankenboot as fixtures and asserts
# the guard's behavior in every scenario, INCLUDING the broken ones (tests
# must discriminate — a suite that only sees healthy input proves nothing).
# Runs on the host (mac/linux) AND on the rig's BusyBox sh:
#   host:  sh tools/test_osguard.sh [path-to-osguard.sh]
#   rig:   scp both to /tmp, ssh 'sh /tmp/test_osguard.sh /tmp/osguard.sh'
# Creates all fixtures under a private tmp dir; writes NOTHING outside it.
# Exit 0 = all scenarios pass; nonzero = failures (count in exit code).
# ============================================================
set -u

GUARD="${1:-$(dirname "$0")/../bin/osguard.sh}"
[ -f "$GUARD" ] || { echo "FATAL: osguard.sh not found at $GUARD"; exit 99; }

T="${TMPDIR:-/tmp}/osguard_test.$$"
mkdir -p "$T/bin"
FAIL=0
PASS=0

# Host portability: BusyBox/linux have sha256sum; macOS has shasum only.
if ! command -v sha256sum >/dev/null 2>&1; then
    cat > "$T/bin/sha256sum" <<'SHIM'
#!/bin/sh
shasum -a 256 "$@"
SHIM
    chmod +x "$T/bin/sha256sum"
    PATH="$T/bin:$PATH"
fi

ok()   { PASS=$((PASS + 1)); echo "  PASS: $1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
check() { # check <desc> <cmd...>
    D="$1"; shift
    if "$@" >/dev/null 2>&1; then ok "$D"; else bad "$D"; fi
}

# fake_kernel <file> <release> <flavor: gtk|stock> <filler>
# Embeds a real-looking version string in a small binary-ish file. The
# filler byte makes same-release kernels byte-distinct (sha discriminates).
fake_kernel() {
    printf 'GARBAGE%s\nLinux version %s (%s) (gcc) # SMP PREEMPT\nMORE%s\n' \
        "$4" "$2" \
        "$([ "$3" = "gtk" ] && echo 'root@rocknix-gtk' || echo '@1234deadbeef')" \
        "$4" > "$1"
}

# build_fixture <name> — common skeleton; scenarios then mutate it
build_fixture() {
    F="$T/$1"
    rm -rf "$F"
    mkdir -p "$F/flash/EFI/BOOT" "$F/flash/boot/grub" "$F/modules" "$F/heal" "$F/storage"
    cat > "$F/flash/EFI/BOOT/grub.cfg" <<'CFG'
menuentry 'Retroid Pocket 5' $menuentry_id_option 'rp5' {
        linux /KERNEL boot=LABEL=ROCKNIX
}
menuentry 'Retroid Pocket Flip2' $menuentry_id_option 'rpflip2' {
        linux /KERNEL boot=LABEL=ROCKNIX
}
menuentry 'Retroid Pocket Flip2 RECOVERY' $menuentry_id_option 'rpflip2-recovery' {
        linux /KERNEL boot=LABEL=ROCKNIX recovery
}
CFG
    cp "$F/flash/EFI/BOOT/grub.cfg" "$F/flash/boot/grub/grub.cfg"
    printf '# GRUB Environment Block\nsaved_entry=rp5\n' > "$F/flash/EFI/BOOT/grubenv"
    cp "$F/flash/EFI/BOOT/grubenv" "$F/flash/boot/grub/grubenv"
    printf 'qcom,sm8250 retroidpocket,rpflip2' > "$F/dt_compat"
}

# run_guard <fixture> <run_rel> [args...]
run_guard() {
    F="$T/$1"; REL="$2"; shift 2
    ETK_ROOT="$F/noetk" ETK_OS_GUARD=1 \
    OSG_FLASH="$F/flash" OSG_MOD_BASE="$F/modules" OSG_RUN_REL="$REL" \
    OSG_HEAL="$F/heal" OSG_DT="$F/dt_compat" OSG_NO_REMOUNT=1 \
    OSG_MARKER="$F/marker" OSG_BACKUP_DIR="$F/storage" \
    OSG_GRUBENV_LIST="$F/flash/EFI/BOOT/grubenv $F/flash/boot/grub/grubenv" \
    TRIPWIRE_LOG="$F/trip.log" \
    sh "$GUARD" "$@" > "$F/out.log" 2>&1
    echo $? > "$F/rc"
}
rc_of() { cat "$T/$1/rc"; }

echo "== SCENARIO 1: the 2026-08-01 frankenboot (Phase A heal) =="
build_fixture s1
fake_kernel "$T/s1/flash/KERNEL"           7.0.11 gtk   AAA   # old GTK build booted
fake_kernel "$T/s1/flash/KERNEL.gtktest"   7.1.2  stock BBB   # updater wrote new stock here
fake_kernel "$T/s1/flash/KERNEL.etk-stock" 7.0.11 stock CCC   # stale snapshot
mkdir -p "$T/s1/modules/7.1.2"                                 # SYSTEM ships 7.1.2 only
run_guard s1 7.0.11
check "exit 0 (healed)" [ "$(rc_of s1)" = "0" ]
check "KERNEL promoted to 7.1.2 donor bytes" cmp -s "$T/s1/flash/KERNEL" "$T/s1/flash/KERNEL.gtktest"
check "etk-stock refreshed from stock donor" cmp -s "$T/s1/flash/KERNEL.etk-stock" "$T/s1/flash/KERNEL.gtktest"
check "displaced old GTK kernel backed up" grep -q "rocknix-gtk" "$T/s1/storage/KERNEL.osguard-displaced"
check "grubenv EFI twin re-pointed at rpflip2" grep -q "saved_entry=rpflip2" "$T/s1/flash/EFI/BOOT/grubenv"
check "grubenv boot twin re-pointed at rpflip2" grep -q "saved_entry=rpflip2" "$T/s1/flash/boot/grub/grubenv"
check "grubenv padded to 1024 bytes" [ "$(wc -c < "$T/s1/flash/EFI/BOOT/grubenv")" -eq 1024 ]
check "marker written" [ -f "$T/s1/marker" ]

echo "== SCENARIO 2: same frankenboot, --check mode (no writes) =="
build_fixture s2
fake_kernel "$T/s2/flash/KERNEL"           7.0.11 gtk   AAA
fake_kernel "$T/s2/flash/KERNEL.gtktest"   7.1.2  stock BBB
mkdir -p "$T/s2/modules/7.1.2"
run_guard s2 7.0.11 --check
check "exit 2 (findings, check mode)" [ "$(rc_of s2)" = "2" ]
check "KERNEL untouched in check mode" grep -q "rocknix-gtk" "$T/s2/flash/KERNEL"
check "grubenv untouched in check mode" grep -q "saved_entry=rp5" "$T/s2/flash/EFI/BOOT/grubenv"

echo "== SCENARIO 3: healthy coherent boot (no-op) =="
build_fixture s3
fake_kernel "$T/s3/flash/KERNEL"         7.1.2 stock BBB
fake_kernel "$T/s3/flash/KERNEL.gtktest" 7.1.2 gtk   DDD
mkdir -p "$T/s3/modules/7.1.2"
cp "$T/s3/flash/KERNEL.gtktest" "$T/s3/heal/KERNEL.staged"
sha256sum "$T/s3/heal/KERNEL.staged" | cut -d' ' -f1 > "$T/s3/heal/KERNEL.staged.sha256"
echo 7.1.2 > "$T/s3/heal/KERNEL.staged.release"
echo default > "$T/s3/heal/mode"
printf "menuentry 'ROCKNIX-GTK for Flip 2' \$menuentry_id_option 'etk-gtk-test' {\n}\n" > "$T/s3/heal/grub.block"
# ETK entries already present in the twins
sed -i.bak "1s/^/menuentry 'ROCKNIX-GTK for Flip 2' \$menuentry_id_option 'etk-gtk-test' {\n}\n/" \
    "$T/s3/flash/EFI/BOOT/grub.cfg" "$T/s3/flash/boot/grub/grub.cfg" 2>/dev/null \
    || { for C in "$T/s3/flash/EFI/BOOT/grub.cfg" "$T/s3/flash/boot/grub/grub.cfg"; do
           printf "menuentry 'x' \$menuentry_id_option 'etk-gtk-test' {\n}\n%s" "$(cat "$C")" > "$C"; done; }
run_guard s3 7.1.2
check "exit 0" [ "$(rc_of s3)" = "0" ]
check "no marker on healthy boot" [ ! -f "$T/s3/marker" ]
check "log says coherent" grep -q "coherent" "$T/s3/trip.log"

echo "== SCENARIO 4: Phase B replay (coherent stock boot, GTK not live) =="
build_fixture s4
fake_kernel "$T/s4/flash/KERNEL"         7.1.2 stock BBB
fake_kernel "$T/s4/flash/KERNEL.gtktest" 7.1.2 stock BBB   # updater's stock copy squatting
mkdir -p "$T/s4/modules/7.1.2"
fake_kernel "$T/s4/heal/KERNEL.staged" 7.1.2 gtk EEE        # the rebased GTK artifact
sha256sum "$T/s4/heal/KERNEL.staged" | cut -d' ' -f1 > "$T/s4/heal/KERNEL.staged.sha256"
echo 7.1.2 > "$T/s4/heal/KERNEL.staged.release"
echo default > "$T/s4/heal/mode"
printf "menuentry 'ROCKNIX-GTK for Flip 2' \$menuentry_id_option 'etk-gtk-test' {\n        linux /KERNEL.gtktest quiet\n}\n" > "$T/s4/heal/grub.block"
run_guard s4 7.1.2
check "exit 0 (replayed)" [ "$(rc_of s4)" = "0" ]
check "gtktest restored to staged GTK bytes" cmp -s "$T/s4/flash/KERNEL.gtktest" "$T/s4/heal/KERNEL.staged"
check "ETK entries re-inserted (EFI twin)" grep -q "etk-gtk-test" "$T/s4/flash/EFI/BOOT/grub.cfg"
check "ETK entries re-inserted (boot twin)" grep -q "etk-gtk-test" "$T/s4/flash/boot/grub/grub.cfg"
check "ETK block leads the menu" sh -c "head -1 '$T/s4/flash/EFI/BOOT/grub.cfg' | grep -q ROCKNIX-GTK"
check "grubenv re-seeded to etk-gtk-test (mode=default)" grep -q "saved_entry=etk-gtk-test" "$T/s4/flash/EFI/BOOT/grubenv"
check "marker written" [ -f "$T/s4/marker" ]

echo "== SCENARIO 5: mismatch with NO donor (fail loud, write nothing) =="
build_fixture s5
fake_kernel "$T/s5/flash/KERNEL"           7.0.11 gtk   AAA
fake_kernel "$T/s5/flash/KERNEL.gtktest"   7.0.11 stock CCC
mkdir -p "$T/s5/modules/7.1.2"
run_guard s5 7.0.11
check "exit 1 (no donor)" [ "$(rc_of s5)" = "1" ]
check "KERNEL untouched" grep -q "rocknix-gtk" "$T/s5/flash/KERNEL"
check "marker written (user needs to act)" [ -f "$T/s5/marker" ]

echo "== SCENARIO 6: staged artifact sha-corrupt — never trusted as donor =="
build_fixture s6
fake_kernel "$T/s6/flash/KERNEL"         7.0.11 gtk   AAA
fake_kernel "$T/s6/flash/KERNEL.gtktest" 7.1.2  stock BBB
mkdir -p "$T/s6/modules/7.1.2"
fake_kernel "$T/s6/heal/KERNEL.staged" 7.1.2 gtk FFF
echo "deadbeef_wrong_sha" > "$T/s6/heal/KERNEL.staged.sha256"
echo 7.1.2 > "$T/s6/heal/KERNEL.staged.release"
run_guard s6 7.0.11
check "exit 0 (healed via flash donor instead)" [ "$(rc_of s6)" = "0" ]
check "corrupt staged NOT used; gtktest stock promoted" cmp -s "$T/s6/flash/KERNEL" "$T/s6/flash/KERNEL.gtktest"
check "log notes sha failure" grep -q "FAILED sha" "$T/s6/trip.log"

echo "== SCENARIO 7: kill-switch ETK_OS_GUARD=0 =="
build_fixture s7
fake_kernel "$T/s7/flash/KERNEL"         7.0.11 gtk   AAA
fake_kernel "$T/s7/flash/KERNEL.gtktest" 7.1.2  stock BBB
mkdir -p "$T/s7/modules/7.1.2" "$T/s7/noetk"
printf 'ETK_OS_GUARD=0\n' > "$T/s7/noetk/etk.conf"
F="$T/s7" REL=7.0.11
ETK_ROOT="$T/s7/noetk" \
OSG_FLASH="$T/s7/flash" OSG_MOD_BASE="$T/s7/modules" OSG_RUN_REL=7.0.11 \
OSG_HEAL="$T/s7/heal" OSG_DT="$T/s7/dt_compat" OSG_NO_REMOUNT=1 \
OSG_MARKER="$T/s7/marker" OSG_BACKUP_DIR="$T/s7/storage" \
TRIPWIRE_LOG="$T/s7/trip.log" \
sh "$GUARD" > "$T/s7/out.log" 2>&1
RC7=$?
check "exit 0 (disabled)" [ "$RC7" = "0" ]
check "nothing healed while disabled" grep -q "rocknix-gtk" "$T/s7/flash/KERNEL"

echo ""
echo "RESULT: $PASS passed, $FAIL failed  ($(basename "$GUARD") @ $(uname -s)/$(uname -m))"
rm -rf "$T"
exit "$FAIL"
