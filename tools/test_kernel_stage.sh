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

echo "== S5: ABL-era boot.img artifact -> release from KS_RELEASE, chain=abl, no grub harvest =="
D="$T/s5"; mkdir -p "$D/iso/bin"; mk_cfg "$D/grub.cfg"
# 0.10.0: with no KS_RELEASE the script reads the manifest beside itself, so the
# refusal case runs an ISOLATED copy (no config/ beside it, ETK_ROOT empty).
cp "$STAGE" "$D/iso/bin/kernel_stage.sh"
printf 'ANDROID!\0\0\0\0\0\0\0\0gzip-image-no-version-string\n' > "$D/art"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$D/iso/bin/kernel_stage.sh" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "boot.img WITHOUT KS_RELEASE and no manifest anywhere is refused" [ "$(cat "$D/rc")" != "0" ]
check "nothing staged on refusal" [ ! -f "$D/heal/KERNEL.staged" ]
printf 'ANDROID!\0\0\0\0\0\0\0\0gzip-image-no-version-string\n' > "$D/art"
KS_RELEASE=7.2.0 KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$STAGE" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "boot.img WITH KS_RELEASE stages (exit 0)" [ "$(cat "$D/rc")" = "0" ]
check "release banked from KS_RELEASE" [ "$(cat "$D/heal/KERNEL.staged.release")" = "7.2.0" ]
check "chain=abl banked" [ "$(cat "$D/heal/chain" 2>/dev/null)" = "abl" ]
check "no grub block harvested on an ABL stage" [ ! -f "$D/heal/grub.block" ]
check "mode defaults to default" grep -q "default" "$D/heal/mode"

echo "== S5b: boot.img WITHOUT KS_RELEASE but with the manifest beside the script (0.9.0 couch update) =="
# A v0.9.0 Pitstop calls the NEW kernel_stage.sh without KS_RELEASE; the update
# tree carries config/gtk_stack.json beside bin/. The release must come from there.
D="$T/s5b"; mkdir -p "$D/tree/bin" "$D/tree/config"; mk_cfg "$D/grub.cfg"
cp "$STAGE" "$D/tree/bin/kernel_stage.sh"
printf '{\n  "gtk_stack": "0.10.0",\n  "kernel": {\n    "asset": "KERNEL.rocknix-gtk-20261001-0.6.4",\n    "kernel_release": "7.2.0",\n    "requires_os": "20261001"\n  }\n}\n' > "$D/tree/config/gtk_stack.json"
printf 'ANDROID!\0\0\0\0\0\0\0\0gzip-image-no-version-string\n' > "$D/art"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$D/tree/bin/kernel_stage.sh" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "boot.img without KS_RELEASE stages when the manifest is beside the script" [ "$(cat "$D/rc")" = "0" ]
check "release banked from the manifest" [ "$(cat "$D/heal/KERNEL.staged.release" 2>/dev/null)" = "7.2.0" ]
check "the fallback is logged" grep -q "kernel_release 7.2.0 read from" "$D/trip.log"
# the installed kit's copy is the second source
D="$T/s5c"; mkdir -p "$D/etk/config"; mk_cfg "$D/grub.cfg"
printf '{ "kernel": { "kernel_release": "7.2.0" } }\n' > "$D/etk/config/gtk_stack.json"
printf 'ANDROID!\0\0\0\0\0\0\0\0gzip-image-no-version-string\n' > "$D/art"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
run_stage "$D" "$D/art" "$SHA"
check "ETK_ROOT/config/gtk_stack.json is the second source" [ "$(cat "$D/heal/KERNEL.staged.release" 2>/dev/null)" = "7.2.0" ]

echo "== S7: card-labels guard — a boot.img for other partition labels than this unit boots is refused (exit 3) =="
# The GTK card image boots a kernel RELABELLED to the card (disk=LABEL=GTKSTOR); the release
# asset names STORAGE. A couch update on a card must not bank that asset (osguard would
# restore it into the card's slot: split-brain with an internal ROCKNIX, or no root). The
# live cmdline is the signature (KS_PROC seam); the artifact's label is read from its
# header (bytes 64..575, the boot.img v0-v2 cmdline field). Exit 3 lets Pitstop say
# "reflash the next card image".
mk_bootimg() { # <path> <cmdline> — a boot.img header with the cmdline in its field (no kernel: never booted)
    { printf 'ANDROID!'; head -c 56 /dev/zero; printf '%s' "$2"; head -c $(( 512 - ${#2} )) /dev/zero; head -c 1056 /dev/zero; } > "$1"
}
mk_proc() { mkdir -p "$1/proc"; printf '%s\n' "$2" > "$1/proc/cmdline"; }
CMD_STOCK='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 gpt msm.context_keepalive=1 panic=30'
CMD_CARD='boot=LABEL=ROCKNIX-GTK disk=LABEL=GTKSTOR quiet rootwait console=tty0 gpt msm.context_keepalive=1 panic=30'
D="$T/s7a"; mkdir -p "$D"; mk_cfg "$D/grub.cfg"; mk_proc "$D" "$CMD_CARD"; mk_bootimg "$D/art" "$CMD_STOCK"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
KS_PROC="$D/proc" KS_RELEASE=7.2.0 KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$STAGE" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "card boot (GTKSTOR) + STORAGE-labelled asset -> exit 3" [ "$(cat "$D/rc")" = "3" ]
check "nothing staged on a card" [ ! -d "$D/heal" ]
check "the artifact is left for the caller to clean up (not consumed)" [ -f "$D/art" ]
check "the refusal is named: reason=card-labels" grep -q "reason=card-labels" "$D/trip.log"
check "the refusal names the two labels" grep -q "disk=LABEL=GTKSTOR, the boot.img names disk=LABEL=STORAGE" "$D/trip.log"
check "the refusal says what to do on a card (reflash)" grep -q "next card image (reflash)" "$D/trip.log"
D="$T/s7b"; mkdir -p "$D"; mk_cfg "$D/grub.cfg"; mk_proc "$D" "$CMD_CARD"; mk_bootimg "$D/art" "$CMD_CARD"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
KS_PROC="$D/proc" KS_RELEASE=7.2.0 KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$STAGE" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "card boot + an asset RELABELLED for the card -> stages (exit 0; the relabel-on-rig follow-up's path)" [ "$(cat "$D/rc")" = "0" ]
check "relabelled asset banked with ITS sha" [ "$(cat "$D/heal/KERNEL.staged.sha256" 2>/dev/null)" = "$SHA" ]
D="$T/s7c"; mkdir -p "$D"; mk_cfg "$D/grub.cfg"; mk_proc "$D" "$CMD_STOCK"; mk_bootimg "$D/art" "$CMD_STOCK"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
KS_PROC="$D/proc" KS_RELEASE=7.2.0 KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$STAGE" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "internal unit (STORAGE) + the release asset -> stages as before" [ "$(cat "$D/rc")" = "0" ]
D="$T/s7d"; mkdir -p "$D"; mk_cfg "$D/grub.cfg"; mk_proc "$D" "$CMD_STOCK"; mk_bootimg "$D/art" "$CMD_CARD"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
KS_PROC="$D/proc" KS_RELEASE=7.2.0 KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$STAGE" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "internal unit + a CARD-relabelled image -> refused too (exit 3; the symmetric mistake)" [ "$(cat "$D/rc")" = "3" ]
D="$T/s7e"; mkdir -p "$D"; mk_cfg "$D/grub.cfg"; mk_proc "$D" "$CMD_STOCK"; mk_bootimg "$D/art" "console=ttyMSM0 quiet"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
KS_PROC="$D/proc" KS_RELEASE=7.2.0 KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$STAGE" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "internal unit + an asset with no disk= token -> accepted (no judgement)" [ "$(cat "$D/rc")" = "0" ]
D="$T/s7f"; mkdir -p "$D"; mk_cfg "$D/grub.cfg"; mk_proc "$D" "$CMD_CARD"; mk_bootimg "$D/art" "console=ttyMSM0 quiet"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
KS_PROC="$D/proc" KS_RELEASE=7.2.0 KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$STAGE" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "card boot + an asset with no disk= token -> refused (a card takes only a proven match)" [ "$(cat "$D/rc")" = "3" ]
D="$T/s7g"; mkdir -p "$D/proc"; mk_cfg "$D/grub.cfg"; mk_bootimg "$D/art" "$CMD_STOCK"     # no cmdline readable at all
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
KS_PROC="$D/proc" KS_RELEASE=7.2.0 KS_HEAL="$D/heal" KS_CFG_LIST="$D/grub.cfg" KS_NO_ACTIVATE=1 TRIPWIRE_LOG="$D/trip.log" ETK_ROOT="$D/etk" \
    sh "$STAGE" "$D/art" "$SHA" >/dev/null 2>&1; echo $? > "$D/rc"
check "unreadable live cmdline -> no judgement, stages (install.sh STEP 6.4 parity)" [ "$(cat "$D/rc")" = "0" ]

echo "== S6: a raw Image still banks chain=grub =="
D="$T/s6"; mkdir -p "$D"; mk_artifact "$D/art"; mk_cfg "$D/grub.cfg"
SHA=$(sha256sum "$D/art" | cut -d' ' -f1)
run_stage "$D" "$D/art" "$SHA"
check "chain=grub banked for a raw Image" [ "$(cat "$D/heal/chain" 2>/dev/null)" = "grub" ]

echo ""
echo "RESULT: $PASS passed, $FAIL failed  ($(basename "$STAGE") @ $(uname -s)/$(uname -m))"
rm -rf "$T"
exit "$FAIL"
