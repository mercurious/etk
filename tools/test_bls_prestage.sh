#!/bin/bash
# test_bls_prestage.sh — pins tools/bls_prestage.sh (the OpenABL pre-stage harness) and
# tools/abl_bank.sh (the read-only abl_a/abl_b bank).
#
# WHY: ROCKNIX 20261101 boots the SM8250 through OpenABL + Boot Loader Spec entries, and
# the stock entry names no devicetree: a unit whose model was never set boots dark. The
# fix is an entry pre-staged BEFORE the update (plan §3 P3 step 0/1). The tool that
# writes it must write exactly the owned set and nothing else (KERNEL, rocknix.conf and
# the stock DTBs are the OS's), render the stock options line verbatim, name the running
# model's DTB file, hold exactly one `default` line, and undo exactly what it did. The
# bank tool must copy both slots sha-verified and write nothing on the rig.
#
# HOW: sandbox with a fake `ssh` on PATH that runs the tools' remote bodies LOCALLY
# against a fake unit tree ($TD/unit: flash/ proc/ sysblk/ dev/), with a `mount` stub
# that only logs. Both Flip 2 models, both layouts (20261001: boot.img KERNEL, no loader/;
# 20261101: PE KERNEL, loader/entries/rocknix.conf, dtbs/). No rig, no network. Host GNU
# tools stand in for BusyBox: the remote bodies are written POSIX-only (manual §Q); the
# rig run is the discriminating check for that.
#
# DISCRIMINATION: `--against <rev>` takes the tools from that revision. Against any
# revision before the tools existed (HEAD~ at their commit) every case FAILS.
#
#   tools/test_bls_prestage.sh                  # working tree — must PASS
#   tools/test_bls_prestage.sh --against HEAD~  # pre-tool — must FAIL

set -u
cd "$(dirname "$0")/.." || exit 1
REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"

TD=$(mktemp -d); [ -n "${KEEP:-}" ] || trap 'rm -rf "$TD"' EXIT; [ -n "${KEEP:-}" ] && echo "sandbox: $TD"
mkdir -p "$TD/bin" "$TD/stubs" "$TD/units" "$TD/tools" "$TD/scripts" "$TD/state" "$TD/payload/dtbs"
for t in bls_prestage.sh abl_bank.sh; do
    if [ -n "$REV" ]; then
        git show "$REV:tools/$t" > "$TD/tools/$t" 2>/dev/null || printf '#!/bin/bash\necho "no tools/%s at %s"; exit 99\n' "$t" "$REV" > "$TD/tools/$t"
    else cp "tools/$t" "$TD/tools/$t"; fi
    chmod +x "$TD/tools/$t"
done
cp scripts/etk_car.sh "$TD/scripts/etk_car.sh"
TOOL="$TD/tools/bls_prestage.sh"; BANK="$TD/tools/abl_bank.sh"

# --- the fake unit -------------------------------------------------------------------
U="$TD/unit"
export BLS_FLASH="$U/flash" BLS_PROC="$U/proc" ABL_BANK_SYSBLK="$U/sysblk" ABL_BANK_DEV="$U/dev"
STOCK_OPT_20261101='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 video=efifb:off gpt'
RIG_OPT='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 video=efifb:off gpt rocknix.extra=1'
mk_unit() {   # <20261001|20261101> <model>
    rm -rf "$U"; mkdir -p "$U/flash" "$U/proc/device-tree" "$U/dev/disk/by-partlabel" "$U/sysblk"
    printf '%s\0' "$2" > "$U/proc/device-tree/model"
    printf '/dev/sda25 %s vfat ro,relatime 0 0\n' "$U/flash" > "$U/proc/mounts"
    if [ "$1" = 20261001 ]; then
        { printf 'ANDROID!'; head -c 4096 /dev/zero; printf 'fake boot.img\n'; } > "$U/flash/KERNEL"
    else
        { printf 'MZ'; head -c 54 /dev/zero; printf 'ARM\x64'; head -c 4096 /dev/zero; printf 'Linux version 7.2.0 stock\n'; } > "$U/flash/KERNEL"
        mkdir -p "$U/flash/loader/entries" "$U/flash/dtbs" "$U/flash/openabl"
        printf 'title      ROCKNIX\nversion    20261101\nlinux      /KERNEL\noptions    %s\n' "$RIG_OPT" > "$U/flash/loader/entries/rocknix.conf"
        printf 'stock dtb flip2\n' > "$U/flash/dtbs/sm8250-retroidpocket-flip2.dtb"
        printf 'stock dtb visionox\n' > "$U/flash/dtbs/sm8250-retroidpocket-flip2-visionox.dtb"
        printf 'elf\n' > "$U/flash/openabl/abl_signed-SM8250.elf"
    fi
    printf '%s  target/KERNEL\n' "$(md5sum "$U/flash/KERNEL" | cut -d' ' -f1)" > "$U/flash/KERNEL.md5"
    # bootloader slots: abl_a via the by-partlabel symlink, abl_b only via sysfs PARTNAME
    { printf 'ELF stock abl'; head -c 65536 /dev/urandom; } > "$U/dev/sda10"; cp "$U/dev/sda10" "$U/dev/sda11"
    ln -s "$U/dev/sda10" "$U/dev/disk/by-partlabel/abl_a"
    for p in 10 11; do mkdir -p "$U/sysblk/sda$p"; printf 'MAJOR=8\nMINOR=%s\nDEVNAME=sda%s\nDEVTYPE=partition\nPARTNAME=abl_%s\n' "$p" "$p" "$([ $p = 10 ] && echo a || echo b)" > "$U/sysblk/sda$p/uevent"; echo 128 > "$U/sysblk/sda$p/size"; done
    : > "$U/mount.log"
}
snap() { (cd "$U" && find flash -mindepth 1 | LC_ALL=C sort; find flash -type f | LC_ALL=C sort | xargs sha256sum); }
written() {   # paths under flash/ present now that were not in $1 (a snap)
    diff <(printf '%s\n' "$1" | grep '^flash/' | LC_ALL=C sort) <(find "$U" -path "$U/flash*" -mindepth 2 | sed "s|^$U/||" | LC_ALL=C sort) | sed -n 's/^> //p' | tr '\n' ' ' | sed 's/ $//'
}

# --- payload: a fake PE GTK kernel + kit DTBs ------------------------------------------------
{ printf 'MZ'; head -c 54 /dev/zero; printf 'ARM\x64'; head -c 8192 /dev/zero; printf 'Linux version 7.2.0 (root@rocknix-gtk)\n'; } > "$TD/payload/KERNEL.gtk"
{ printf 'ANDROID!'; head -c 2048 /dev/zero; } > "$TD/payload/KERNEL.bootimg"
printf 'kit dtb flip2 mic\n' > "$TD/payload/dtbs/sm8250-retroidpocket-flip2.dtb"
printf 'kit dtb visionox mic\n' > "$TD/payload/dtbs/sm8250-retroidpocket-flip2-visionox.dtb"
GTK_SHA=$(sha256sum "$TD/payload/KERNEL.gtk" | cut -d' ' -f1)

# --- fakes: ssh runs the remote body locally (stdin passes through) ---------------------------
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
cat > "$TD/stubs/mount" <<'FAKE'
#!/bin/bash
echo "$*" >> "$UNIT/mount.log"
exit 0
FAKE
chmod +x "$TD/bin/"* "$TD/stubs/"*
export PATH="$TD/bin:$PATH" UNITS="$TD/units" STUBS="$TD/stubs" UNIT="$U"
printf 'car=car12\nmodel=Retroid Pocket Flip2 Visionox\nmemkb=11809768\npanel=vtdr6130,rpflip2\nhost=sm8250-12gb\n' > "$TD/units/car12host"
printf 'car=car8\nmodel=Retroid Pocket Flip2\nmemkb=7735000\npanel=\nhost=SM8250\n' > "$TD/units/car8host"
printf 'car=\nmodel=Odd Board\nmemkb=7735000\npanel=\nhost=odd\n' > "$TD/units/oddhost"
unset RIG_SSH CAR

PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
expect() {   # <label> <want-rc> <want-substring> <command...>
    local label="$1" wrc="$2" wsub="$3"; shift 3
    local out rc; out=$("$@" 2>&1); rc=$?
    if [ "$rc" = "$wrc" ] && printf '%s' "$out" | grep -qF -- "$wsub"; then ok "$label"
    else bad "$label (rc=$rc want $wrc; out: $(printf '%s' "$out" | tail -c 260 | tr '\n' ' '))"; fi
}
same() { [ "$1" = "$2" ] && ok "$3" || bad "$3 (got: $(printf '%s' "$1" | head -c 300 | tr '\n' '~') want: $(printf '%s' "$2" | head -c 300 | tr '\n' '~'))"; }
ksha() { sha256sum "$U/flash/KERNEL" | cut -d' ' -f1; }

# ============ 20261001 rig (boot.img slot, no loader/), plain Flip 2 ===========================
mk_unit 20261001 "Retroid Pocket Flip2"; S0=$(snap); K0=$(ksha)
expect "plan: reads the 20261001 rig, prints the set, rc 0"        0 "nothing written"         "$TOOL" --plan --car car8 root@car8host
expect "plan: names the plain Flip 2 DTB"                           0 "/dtbs/sm8250-retroidpocket-flip2.dtb" "$TOOL" --plan root@car8host
expect "plan: options come from the 20261101 text (no rocknix.conf)" 0 "20261101 text"         "$TOOL" --plan root@car8host
same "$(snap)" "$S0" "plan: wrote nothing to /flash"
[ -s "$U/mount.log" ] && bad "plan: remounted /flash" || ok "plan: never remounted /flash"

expect "write: 20261001 rig, stock entry + loader.conf, rc 0"       0 "BLS_PRESTAGE_OK"         "$TOOL" --car car8 root@car8host
same "$(written "$S0")" "flash/loader flash/loader/entries flash/loader/entries/etk-stock.conf flash/loader/loader.conf" "write: exactly loader/, entries/, etk-stock.conf, loader.conf appeared"
WANT_STOCK=$(printf 'title      ROCKNIX (stock kernel, ETK DTB line)\nversion    etk-prestage\nlinux      /KERNEL\ndevicetree /dtbs/sm8250-retroidpocket-flip2.dtb\noptions    %s\nsort-key   etk-stock\n' "$STOCK_OPT_20261101")
same "$(cat "$U/flash/loader/entries/etk-stock.conf")" "$WANT_STOCK" "write: etk-stock.conf text byte-exact (stock kernel, named DTB, 20261101 options, sort-key)"
same "$(cat "$U/flash/loader/loader.conf")" "default etk-stock.conf" "write: loader.conf is the one default line"
same "$(grep -c '^default' "$U/flash/loader/loader.conf")" "1" "write: exactly one default line"
same "$(wc -l < "$U/flash/loader/loader.conf")" "1" "write: loader.conf has one line"
same "$(ksha)" "$K0" "write: KERNEL untouched"
same "$(sed -n '1p;$p' "$U/mount.log" | tr '\n' ' ')" "-o remount,rw $U/flash -o remount,ro $U/flash " "write: /flash remounted rw then left ro"
[ -e "$U/flash/etk-prestage.tmp" ] && bad "write: temp dir left on /flash" || ok "write: no temp dir left on /flash"
expect "write: re-run over our own files is idempotent"              0 "BLS_PRESTAGE_OK"         "$TOOL" root@car8host
same "$(written "$S0")" "flash/loader flash/loader/entries flash/loader/entries/etk-stock.conf flash/loader/loader.conf" "write: re-run adds nothing"

expect "remove: deletes the set, rc 0"                               0 "BLS_PRESTAGE_REMOVED"    "$TOOL" --remove root@car8host
same "$(snap)" "$S0" "remove: /flash back to the 20261001 original (loader/ gone: we made it)"
same "$(tail -n1 "$U/mount.log")" "-o remount,ro $U/flash" "remove: /flash left ro"

# ============ 20261101 rig (PE slot, rocknix.conf, dtbs/), Visionox ============================
mk_unit 20261101 "Retroid Pocket Flip2 Visionox"; S1=$(snap); K1=$(ksha); R1=$(sha256sum "$U/flash/loader/entries/rocknix.conf" | cut -d' ' -f1)
expect "plan: 20261101 rig names the Visionox DTB"                   0 "/dtbs/sm8250-retroidpocket-flip2-visionox.dtb" "$TOOL" --plan --car car12 root@car12host
expect "plan: options read verbatim from the rig's rocknix.conf"     0 "rocknix.conf (verbatim)" "$TOOL" --plan root@car12host
expect "write: 20261101 rig, rc 0"                                   0 "layout=20261101+bls-entries" "$TOOL" --car car12 root@car12host
same "$(written "$S1")" "flash/loader/entries/etk-stock.conf flash/loader/loader.conf" "write: on 20261101 only the two files appear (loader/ already there)"
WANT_STOCK_V=$(printf 'title      ROCKNIX (stock kernel, ETK DTB line)\nversion    20261101\nlinux      /KERNEL\ndevicetree /dtbs/sm8250-retroidpocket-flip2-visionox.dtb\noptions    %s\nsort-key   etk-stock\n' "$RIG_OPT")
same "$(cat "$U/flash/loader/entries/etk-stock.conf")" "$WANT_STOCK_V" "write: stock entry carries the rig's options line VERBATIM (rocknix.extra=1) + its version"
same "$(ksha)" "$K1" "write: PE KERNEL untouched"
same "$(sha256sum "$U/flash/loader/entries/rocknix.conf" | cut -d' ' -f1)" "$R1" "write: rocknix.conf untouched"
expect "remove: 20261101 rig"                                         0 "BLS_PRESTAGE_REMOVED"    "$TOOL" --remove root@car12host
same "$(snap)" "$S1" "remove: rocknix.conf, dtbs/, loader/ all still there; only ours gone"

# ============ the GTK payload ===================================================================
mk_unit 20261001 "Retroid Pocket Flip2 Visionox"; S2=$(snap); K2=$(ksha)
expect "gtk: --gtk without --dtbs refused"                            1 "go together"             "$TOOL" --gtk "$TD/payload/KERNEL.gtk" root@car12host
expect "gtk: a boot.img is refused (OpenABL cannot load it)"          1 "Android boot.img"        "$TOOL" --gtk "$TD/payload/KERNEL.bootimg" --dtbs "$TD/payload/dtbs" root@car12host
expect "gtk: --default gtk without --gtk refused"                     1 "not staged"              "$TOOL" --default gtk root@car12host
mkdir -p "$TD/payload/dtbs_flip2only"; cp "$TD/payload/dtbs/sm8250-retroidpocket-flip2.dtb" "$TD/payload/dtbs_flip2only/"
expect "gtk: dtbs dir without the running model's file refused"       1 "has no sm8250-retroidpocket-flip2-visionox.dtb" "$TOOL" --plan --gtk "$TD/payload/KERNEL.gtk" --dtbs "$TD/payload/dtbs_flip2only" root@car12host
same "$(snap)" "$S2" "gtk: every refusal wrote nothing"
expect "gtk: plan prints KERNEL.gtk sha and the entry"               0 "sha256 $GTK_SHA"         "$TOOL" --plan --gtk "$TD/payload/KERNEL.gtk" --dtbs "$TD/payload/dtbs" root@car12host
same "$(snap)" "$S2" "gtk: plan wrote nothing"
expect "gtk: write with --default gtk, rc 0"                          0 "gtk=$GTK_SHA"            "$TOOL" --gtk "$TD/payload/KERNEL.gtk" --dtbs "$TD/payload/dtbs" --default gtk --car car12 root@car12host
same "$(written "$S2")" "flash/KERNEL.gtk flash/dtbs flash/dtbs/etk flash/dtbs/etk/sm8250-retroidpocket-flip2-visionox.dtb flash/dtbs/etk/sm8250-retroidpocket-flip2.dtb flash/loader flash/loader/entries flash/loader/entries/etk-gtk.conf flash/loader/entries/etk-stock.conf flash/loader/loader.conf" "gtk: exactly KERNEL.gtk, dtbs/etk/*, both entries, loader.conf appeared"
same "$(sha256sum "$U/flash/KERNEL.gtk" | cut -d' ' -f1)" "$GTK_SHA" "gtk: KERNEL.gtk byte-exact"
WANT_GTK=$(printf 'title      ROCKNIX-GTK\nversion    KERNEL.gtk\nlinux      /KERNEL.gtk\ndevicetree /dtbs/etk/sm8250-retroidpocket-flip2-visionox.dtb\noptions    %s msm.context_keepalive=1 panic=30\n' "$STOCK_OPT_20261101")
same "$(cat "$U/flash/loader/entries/etk-gtk.conf")" "$WANT_GTK" "gtk: etk-gtk.conf = our kernel, kit DTB under dtbs/etk/, stock options + keepalive + panic=30"
same "$(cat "$U/flash/loader/loader.conf")" "default etk-gtk.conf" "gtk: --default gtk -> one line naming etk-gtk.conf"
same "$(ksha)" "$K2" "gtk: KERNEL untouched"
expect "gtk: remove"                                                  0 "removed dtbs/etk/"       "$TOOL" --remove root@car12host
same "$(snap)" "$S2" "gtk: remove took KERNEL.gtk, dtbs/etk/, both entries, loader.conf, and the dirs we made"

# ============ refusals ========================================================================
mk_unit 20261001 "Odd Board"; S3=$(snap)
expect "unknown model refused"                                        1 "unknown model 'Odd Board'" "$TOOL" root@oddhost
same "$(snap)" "$S3" "unknown model: nothing written"
mk_unit 20261101 "Retroid Pocket Flip2"; mkdir -p "$U/flash/loader"; printf 'default rocknix.conf\ntimeout 3\n' > "$U/flash/loader/loader.conf"; S4=$(snap)
expect "foreign loader.conf: write refused"                           1 "not ours"                 "$TOOL" root@car8host
expect "foreign loader.conf: remove refused"                          1 "nothing removed"          "$TOOL" --remove root@car8host
same "$(snap)" "$S4" "foreign loader.conf: untouched either way"
mk_unit 20261001 "Retroid Pocket Flip2"; S5=$(snap)
expect "wrong car refused before anything is written"                 1 "is not car12"             "$TOOL" --car car12 root@car8host
expect "unreachable unit -> rc 1"                                     1 "could not read"           "$TOOL" root@nowhere
same "$(snap)" "$S5" "wrong car / unreachable: nothing written"

# ============ abl_bank: read-only copy of both slots ============================================
mk_unit 20261001 "Retroid Pocket Flip2 Visionox"; S6=$(snap); SLOT_SHA=$(sha256sum "$U/dev/sda10" | cut -d' ' -f1)
expect "bank: both slots copied, rc 0"                                0 "ABL_BANK_OK car=car12"    "$BANK" --car car12 --dir "$TD/state/abl/car12" root@car12host
same "$(sha256sum "$TD/state/abl/car12/abl_a.img" | cut -d' ' -f1)" "$SLOT_SHA" "bank: abl_a.img == the device bytes (via by-partlabel)"
same "$(sha256sum "$TD/state/abl/car12/abl_b.img" | cut -d' ' -f1)" "$SLOT_SHA" "bank: abl_b.img == the device bytes (via sysfs PARTNAME)"
(cd "$TD/state/abl/car12" && sha256sum -c SHA256SUMS >/dev/null 2>&1) && ok "bank: SHA256SUMS checks" || bad "bank: SHA256SUMS does not check"
grep -q "abl_a.*$U/dev/sda10" "$TD/state/abl/car12/README" && grep -q "abl_b.*$U/dev/sda11" "$TD/state/abl/car12/README" && grep -q "^date      : 20" "$TD/state/abl/car12/README" \
    && ok "bank: README names both source devices and the date" || bad "bank: README lacks device/date ($(cat "$TD/state/abl/car12/README" | tr '\n' '~' | head -c 400))"
grep -q "abl_a == abl_b" "$TD/state/abl/car12/README" && ok "bank: README records the slots agree" || bad "bank: README does not compare the slots"
same "$(snap)" "$S6" "bank: /flash untouched"
[ -s "$U/mount.log" ] && bad "bank: remounted /flash" || ok "bank: never remounted anything"
same "$(sha256sum "$U/dev/sda10" "$U/dev/sda11" | cut -d' ' -f1 | tr '\n' ' ')" "$SLOT_SHA $SLOT_SHA " "bank: the slots themselves untouched"
expect "bank: an existing bank is never overwritten"                  1 "never overwritten"        "$BANK" --car car12 --dir "$TD/state/abl/car12" root@car12host
expect "bank: wrong car refused"                                      1 "is not car8"              "$BANK" --car car8 --dir "$TD/state/abl/x" root@car12host
[ -e "$TD/state/abl/x" ] && bad "bank: wrong car still created a dir" || ok "bank: wrong car created nothing"
rm -f "$U/dev/disk/by-partlabel/abl_a"; rm -rf "$U/sysblk/sda10"
expect "bank: a slot that cannot be resolved is named, nothing banked" 1 "no partition labelled abl_a" "$BANK" --car car12 --dir "$TD/state/abl/car12b" root@car12host
[ -e "$TD/state/abl/car12b/abl_a.img" ] && bad "bank: partial bank left" || ok "bank: no partial image left"

echo; echo "test_bls_prestage: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" -eq 0 ]
