#!/bin/bash
# test_relabel_bootimg.sh — pins os-install/build/relabel_bootimg.py (the card's boot.img labels).
#
# WHY: under ROCKNIX-ABL the cmdline is baked in the boot.img header and the ABL appends
# nothing, so the flashable GTK card (unique labels ROCKNIX-GTK / GTKSTOR) needs the
# certified boot.img with ONLY its two LABEL tokens rewritten. A relabel that touched
# anything else would ship an unparity'd kernel; one that missed a token would boot a
# card that mounts the internal ROCKNIX (the split-brain the labels exist to prevent).
#
# HOW: real mkbootimg v0 images around a fake Image (+ the certified artifact when it is
# on this host, read-only). Needs mkbootimg + python3. No rig, no network.
#
#   tools/test_relabel_bootimg.sh              # must PASS
set -u
cd "$(dirname "$0")/.." || exit 1
command -v mkbootimg >/dev/null || { echo "SKIP: mkbootimg not on PATH"; exit 0; }
TOOL=os-install/build/relabel_bootimg.py
TD=$(mktemp -d); trap 'rm -rf "$TD"' EXIT
PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
expect() { local label="$1" wrc="$2" wsub="$3"; shift 3; local out rc; out=$("$@" 2>&1); rc=$?
    if [ "$rc" = "$wrc" ] && printf '%s' "$out" | grep -qF -- "$wsub"; then ok "$label"; else bad "$label (rc=$rc want $wrc; out: $(printf '%s' "$out" | tail -c 200))"; fi; }

STOCK='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 video=efifb:off gpt msm.context_keepalive=1 panic=30'
head -c 4096 /dev/urandom > "$TD/Image"; printf 'dummy' > "$TD/rd"
mk() { mkbootimg --kernel "$TD/Image" --ramdisk "$TD/rd" --header_version 0 --pagesize 2048 --cmdline "$2" -o "$1" >/dev/null 2>&1; }
mk "$TD/a.img" "$STOCK"
R() { python3 -I "$TOOL" "$@"; }

expect "relabel: ROCKNIX/STORAGE -> ROCKNIX-GTK/GTKSTOR" 0 "RELABEL_OK" R "$TD/a.img" "$TD/b.img" ROCKNIX STORAGE ROCKNIX-GTK GTKSTOR
expect "show: new cmdline carries the card labels"         0 "boot=LABEL=ROCKNIX-GTK disk=LABEL=GTKSTOR quiet" R show "$TD/b.img"
expect "show: every other token kept (keepalive, panic)"   0 "msm.context_keepalive=1 panic=30" R show "$TD/b.img"
[ "$(stat -c %s "$TD/a.img")" = "$(stat -c %s "$TD/b.img")" ] && ok "size unchanged" || bad "size changed"
cmp -s <(head -c 64 "$TD/a.img") <(head -c 64 "$TD/b.img") && ok "header before the cmdline field identical" || bad "header head differs"
cmp -s <(tail -c +577 "$TD/a.img") <(tail -c +577 "$TD/b.img") && ok "everything after the cmdline field identical (kernel, ramdisk, id)" || bad "payload differs"
expect "relabel back -> byte-identical to the original"     0 "RELABEL_OK" R "$TD/b.img" "$TD/c.img" ROCKNIX-GTK GTKSTOR ROCKNIX STORAGE
cmp -s "$TD/a.img" "$TD/c.img" && ok "round trip is lossless" || bad "round trip differs"
expect "same labels in and out = identical copy"            0 "RELABEL_OK" R "$TD/a.img" "$TD/d.img" ROCKNIX STORAGE ROCKNIX STORAGE
cmp -s "$TD/a.img" "$TD/d.img" && ok "identity relabel is byte-identical" || bad "identity relabel changed bytes"
expect "missing token (wrong from-label) refused"          1 "occurs 0 times" R "$TD/a.img" "$TD/x.img" NOPE STORAGE ROCKNIX-GTK GTKSTOR
[ -e "$TD/x.img" ] && bad "refusal left an output file" || ok "refusal wrote nothing"
mk "$TD/dup.img" "boot=LABEL=ROCKNIX boot=LABEL=ROCKNIX disk=LABEL=STORAGE"
expect "duplicate token refused"                           1 "occurs 2 times" R "$TD/dup.img" "$TD/x.img" ROCKNIX STORAGE A B
LONG=$(head -c 460 /dev/zero | tr '\0' 'x')        # 38 + 460 = 498 B fits; +18 B of label growth does not
mk "$TD/long.img" "boot=LABEL=ROCKNIX disk=LABEL=STORAGE $LONG"
expect "cmdline that would overflow 511 B refused"         1 "the header field holds 511" R "$TD/long.img" "$TD/x.img" ROCKNIX STORAGE ROCKNIXGTKLABELX GTKSTORAGELABELX
expect "bad label (space) refused"                         1 "bad label" R "$TD/a.img" "$TD/x.img" ROCKNIX STORAGE "ROCK NIX" GTKSTOR
printf 'not a boot image' > "$TD/raw"
expect "non-boot.img refused"                              1 "not an Android boot image" R "$TD/raw" "$TD/x.img" ROCKNIX STORAGE A B
expect "usage without args -> rc 2"                        2 "relabel_bootimg.py" R

ART=$(ls "$HOME"/rocknix-gtk/artifacts/KERNEL.rocknix-gtk-20261001-*.* 2>/dev/null | grep -v sha256 | sort | tail -1)
if [ -n "$ART" ]; then
    expect "certified artifact: relabels cleanly ($(basename "$ART"))" 0 "RELABEL_OK" R "$ART" "$TD/art.img" ROCKNIX STORAGE ROCKNIX-GTK GTKSTOR
    cmp -s <(tail -c +577 "$ART") <(tail -c +577 "$TD/art.img") && ok "certified artifact: kernel + DTBs + ramdisk untouched" || bad "certified artifact payload changed"
    R "$TD/art.img" "$TD/art2.img" ROCKNIX-GTK GTKSTOR ROCKNIX STORAGE >/dev/null && cmp -s "$ART" "$TD/art2.img" && ok "certified artifact: round trip lossless" || bad "certified artifact round trip differs"
else
    echo "SKIP: no certified 20261001 artifact on this host"
fi
echo; echo "test_relabel_bootimg: $PASS passed, $FAIL failed"; [ "$FAIL" = 0 ]
