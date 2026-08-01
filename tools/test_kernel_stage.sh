#!/bin/sh
# ============================================================
# test_kernel_stage.sh — discrimination suite for bin/kernel_stage.sh
# Runs on host AND rig BusyBox (same contract as test_osguard.sh):
#   sh tools/test_kernel_stage.sh [path-to-kernel_stage.sh]
# ============================================================
set -u
STAGE="${1:-$(dirname "$0")/../bin/kernel_stage.sh}"
[ -f "$STAGE" ] || { echo "FATAL: kernel_stage.sh not found at $STAGE"; exit 99; }

T="${TMPDIR:-/tmp}/kstage_test.$$"
mkdir -p "$T/bin"
FAIL=0; PASS=0
if ! command -v sha256sum >/dev/null 2>&1; then
    printf '#!/bin/sh\nshasum -a 256 "$@"\n' > "$T/bin/sha256sum"
    chmod +x "$T/bin/sha256sum"
    PATH="$T/bin:$PATH"
fi
ok()  { PASS=$((PASS + 1)); echo "  PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
check() { _desc="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$_desc"; else bad "$_desc"; fi; }

mk_artifact() { # <path> — fake Image with a version string
    printf 'BLOB\nLinux version 7.1.2 (root@rocknix-gtk) (gcc) # SMP PREEMPT\nBLOB\n' > "$1"
}
mk_cfg() { # <path> — grub.cfg WITH live ETK entries + stock entries
    cat > "$1" <<'CFG'
menuentry 'ROCKNIX-GTK for Flip 2' $menuentry_id_option 'etk-gtk-test' {
        linux /KERNEL.gtktest quiet panic=30 msm.context_keepalive=1
}
menuentry 'ROCKNIX-GTK fallback -- stock kernel' $menuentry_id_option 'etk-fallback-stock' {
        linux /KERNEL.etk-stock loglevel=7
}
# etk-sdcard entries (managed): SD boot, pick-persistent, self-healing
set fallback='etk-gtk-test'
menuentry 'ROCKNIX-GTK from SD card' $menuentry_id_option 'etk-sdcard' {
        linux /KERNEL.gtktest quiet
}
menuentry 'Retroid Pocket Flip2' $menuentry_id_option 'rpflip2' {
        linux /KERNEL boot=LABEL=ROCKNIX
}
CFG
}
run_stage() { # <dir> <artifact> <sha> [mode]
    KS_HEAL="$1/heal" KS_CFG_LIST="$1/grub.cfg" KS_NO_ACTIVATE=1 \
    TRIPWIRE_LOG="$1/trip.log" ETK_ROOT="$1/noetk" \
    sh "$STAGE" "$2" "$3" "${4:-}" > "$1/out.log" 2>&1
    echo $? > "$1/rc"
}

echo "== S1: good artifact + live entries -> staged + block banked =="
D="$T/s1"; mkdir -p "$D"; mk_artifact "$D/art"; mk_cfg "$D/grub.cfg"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
run_stage "$D" "$D/art" "$SHA"
check "exit 0" [ "$(cat "$D/rc")" = "0" ]
check "KERNEL.staged placed" [ -f "$D/heal/KERNEL.staged" ]
check "artifact consumed (moved, not copied)" [ ! -f "$D/art" ]
check "sha banked" grep -q "$SHA" "$D/heal/KERNEL.staged.sha256"
check "release extracted (7.1.2)" grep -q "7.1.2" "$D/heal/KERNEL.staged.release"
check "mode defaulted" grep -q "default" "$D/heal/mode"
check "grub block harvested" [ -s "$D/heal/grub.block" ]
check "block has the ETK entries" sh -c "[ \"\$(grep -c menuentry '$D/heal/grub.block')\" -eq 3 ]"
check "block kept the fallback global" grep -q "set fallback=" "$D/heal/grub.block"
check "block excludes stock entries" sh -c "! grep -q rpflip2 '$D/heal/grub.block'"

echo "== S2: sha mismatch -> refused, nothing staged =="
D="$T/s2"; mkdir -p "$D"; mk_artifact "$D/art"; mk_cfg "$D/grub.cfg"
run_stage "$D" "$D/art" "deadbeef0000"
check "exit nonzero" [ "$(cat "$D/rc")" != "0" ]
check "no KERNEL.staged" [ ! -f "$D/heal/KERNEL.staged" ]

echo "== S3: no live ETK entries -> staged, no block, noted =="
D="$T/s3"; mkdir -p "$D"; mk_artifact "$D/art"
printf "menuentry 'Retroid Pocket Flip2' \$menuentry_id_option 'rpflip2' {\n}\n" > "$D/grub.cfg"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
run_stage "$D" "$D/art" "$SHA"
check "exit 0" [ "$(cat "$D/rc")" = "0" ]
check "staged anyway" [ -f "$D/heal/KERNEL.staged" ]
check "no block banked" [ ! -f "$D/heal/grub.block" ]
check "note logged" grep -q "none banked" "$D/trip.log"

echo "== S4: explicit mode + existing mode preserved on omit =="
D="$T/s4"; mkdir -p "$D"; mk_artifact "$D/art"; mk_cfg "$D/grub.cfg"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
run_stage "$D" "$D/art" "$SHA" test
check "mode=test honored" grep -q "test" "$D/heal/mode"
mk_artifact "$D/art2"
run_stage "$D" "$D/art2" "$SHA"
check "existing mode preserved when arg omitted" grep -q "test" "$D/heal/mode"

echo ""
echo "RESULT: $PASS passed, $FAIL failed  ($(basename "$STAGE") @ $(uname -s)/$(uname -m))"
rm -rf "$T"
exit "$FAIL"
