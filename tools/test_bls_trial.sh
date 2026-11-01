#!/bin/bash
# test_bls_trial.sh — pins tools/bls_trial.sh (OpenABL fallthrough and boot-count trials).
#
# WHY: the trials move KERNEL.gtk and etk-gtk.conf on a rig's /flash. A tool that leaves a
# trial half-done, stacks a second one on the first, touches stock's files or cannot put the
# set back exactly would turn a test of the fallback into the failure it was testing for.
#
# HOW: same sandbox as test_bls_prestage.sh: a fake `ssh` runs the remote bodies locally
# against a fake 20261101 unit, with a `mount` stub that logs. OpenABL's own rename is
# simulated. Host GNU tools stand in for BusyBox; `status` on a live rig checks the read body.
#
# DISCRIMINATION: `--against <rev>` takes the tool from that revision. Before the tool
# existed the suite FAILS (37 of 64; the "wrote nothing" cases pass trivially).
#
#   tools/test_bls_trial.sh                  # working tree — must PASS
#   tools/test_bls_trial.sh --against HEAD~  # pre-tool — must FAIL

set -u
cd "$(dirname "$0")/.." || exit 1
REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"

TD=$(mktemp -d); trap 'rm -rf "$TD"' EXIT
mkdir -p "$TD/bin" "$TD/stubs" "$TD/units" "$TD/tools" "$TD/scripts"
if [ -n "$REV" ]; then
    git show "$REV:tools/bls_trial.sh" > "$TD/tools/bls_trial.sh" 2>/dev/null \
        || printf '#!/bin/bash\necho "no tools/bls_trial.sh at %s"; exit 99\n' "$REV" > "$TD/tools/bls_trial.sh"
else
    cp tools/bls_trial.sh "$TD/tools/bls_trial.sh"
fi
chmod +x "$TD/tools/bls_trial.sh"
cp scripts/etk_car.sh "$TD/scripts/etk_car.sh"
TOOL="$TD/tools/bls_trial.sh"

U="$TD/unit"
export BLS_FLASH="$U/flash" BLS_PROC="$U/proc"
OPT='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 video=efifb:off gpt'
mk_unit() {   # a car12-shaped 20261101 rig, pre-staged, default stock
    rm -rf "$U"; mkdir -p "$U/flash/loader/entries" "$U/flash/dtbs/etk" "$U/proc"
    { printf 'MZ'; head -c 4096 /dev/zero; printf 'stock PE\n'; } > "$U/flash/KERNEL"
    { printf 'MZ'; head -c 4096 /dev/zero; printf 'gtk PE\n'; } > "$U/flash/KERNEL.gtk"
    printf 'title ROCKNIX\nlinux /KERNEL\noptions %s\n' "$OPT" > "$U/flash/loader/entries/rocknix.conf"
    printf 'title stock\nlinux /KERNEL\noptions %s\nsort-key etk-stock\n' "$OPT" > "$U/flash/loader/entries/etk-stock.conf"
    printf 'title GTK\nlinux /KERNEL.gtk\noptions %s msm.context_keepalive=1\n' "$OPT" > "$U/flash/loader/entries/etk-gtk.conf"
    printf 'default etk-stock.conf\n' > "$U/flash/loader/loader.conf"
    printf 'kit dtb\n' > "$U/flash/dtbs/etk/x.dtb"
    printf '%s\n' "$OPT" > "$U/proc/cmdline"
    : > "$U/mount.log"
}
snap() { (cd "$U" && find flash -type f | LC_ALL=C sort | xargs sha256sum; find flash -type d | LC_ALL=C sort); }

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
same() { [ "$1" = "$2" ] && ok "$3" || bad "$3"; }
has()  { [ -f "$U/flash/$1" ] && ok "$2" || bad "$2"; }
hasnt() { [ ! -e "$U/flash/$1" ] && ok "$2" || bad "$2"; }
dflt() { cat "$U/flash/loader/loader.conf"; }
core() { (cd "$U/flash" && sha256sum KERNEL loader/entries/rocknix.conf loader/entries/etk-stock.conf dtbs/etk/x.dtb); }
T=root@car12host

# ---- status: reads, writes nothing, names what booted ------------------------------------
mk_unit; S0=$(snap)
expect "status: stock boot says it cannot tell the two stock entries apart" 0 "cannot tell which" "$TOOL" status --car car12 "$T"
expect "status: no trial on a clean rig"                  0 "trial    : none"    "$TOOL" status "$T"
same "$(snap)" "$S0" "status: wrote nothing"
[ -s "$U/mount.log" ] && bad "status: remounted /flash" || ok "status: never remounted /flash"
printf '%s msm.context_keepalive=1 panic=30\n' "$OPT" > "$U/proc/cmdline"
expect "status: keepalive on the cmdline names etk-gtk"   0 "booted   : etk-gtk" "$TOOL" status "$T"

# ---- the car gate --------------------------------------------------------------------------
mk_unit; S0=$(snap)
expect "gate: refuses another car"                        1 "is not car12"       "$TOOL" fallthrough --car car12 root@car8host
same "$(snap)" "$S0" "gate: wrote nothing"

# ---- fallthrough ---------------------------------------------------------------------------
mk_unit; S0=$(snap); C0=$(core); GTK0=$(sha256sum "$U/flash/KERNEL.gtk" | cut -d' ' -f1)
expect "fallthrough: rc 0"                                0 "BLS_TRIAL_OK fallthrough" "$TOOL" fallthrough --car car12 "$T"
hasnt KERNEL.gtk "fallthrough: KERNEL.gtk gone, so the entry fails to load"
has KERNEL.gtk.aside "fallthrough: moved aside, not deleted"
same "$(sha256sum "$U/flash/KERNEL.gtk.aside" | cut -d' ' -f1)" "$GTK0" "fallthrough: the aside copy is the kernel"
same "$(dflt)" "default etk-gtk.conf" "fallthrough: default names the entry under test"
has loader/entries/etk-gtk.conf "fallthrough: the entry itself is untouched"
same "$(core)" "$C0" "fallthrough: KERNEL, rocknix.conf, etk-stock.conf and DTBs untouched"
same "$(cut -d' ' -f3 "$U/mount.log" | tr '\n' ' ')" "$U/flash $U/flash " "fallthrough: remounted exactly twice"
same "$(sed -n 1p "$U/mount.log" | cut -d' ' -f2)" "remount,rw" "fallthrough: rw first"
same "$(sed -n 2p "$U/mount.log" | cut -d' ' -f2)" "remount,ro" "fallthrough: ro again"
expect "fallthrough: status names the trial"              0 "trial    : fallthrough" "$TOOL" status "$T"
expect "fallthrough: a second trial is refused"           1 "already in place"   "$TOOL" count 3 "$T"
expect "fallthrough: a repeat is refused"                 1 "already in place"   "$TOOL" fallthrough "$T"
expect "restore: rc 0"                                    0 "BLS_TRIAL_OK restore" "$TOOL" restore --car car12 "$T"
same "$(snap)" "$S0" "restore: the tree is byte-identical to before the trial"
expect "restore: a repeat on a clean rig is harmless"     0 "BLS_TRIAL_OK restore" "$TOOL" restore "$T"
same "$(snap)" "$S0" "restore: a repeat changes nothing"

# ---- count ---------------------------------------------------------------------------------
mk_unit; S0=$(snap); C0=$(core); E="$U/flash/loader/entries"
expect "count: default is 3 tries"                        0 "BLS_TRIAL_OK count 3" "$TOOL" count --car car12 "$T"
has loader/entries/etk-gtk+3.conf "count: the entry carries the counter"
hasnt loader/entries/etk-gtk.conf "count: the plain name is gone"
same "$(dflt)" "default etk-gtk.conf" "count: default keeps matching (OpenABL strips the counter from the id)"
same "$(core)" "$C0" "count: stock's files untouched"
expect "count: status marks it counting"                  0 "<- counting"        "$TOOL" status "$T"
expect "count: a second trial is refused"                 1 "already in place"   "$TOOL" fallthrough "$T"
mv "$E/etk-gtk+3.conf" "$E/etk-gtk+0-3.conf"              # OpenABL ran it out
expect "count: status calls an exhausted entry bad"       0 "out of tries"       "$TOOL" status "$T"
expect "count: restore from an exhausted entry"           0 "BLS_TRIAL_OK restore" "$TOOL" restore "$T"
same "$(snap)" "$S0" "count: restore is byte-identical from +0-3"
mk_unit; S0=$(snap)
expect "count: takes a number"                            0 "BLS_TRIAL_OK count 2" "$TOOL" count 2 "$T"
has loader/entries/etk-gtk+2.conf "count: N reaches the file name"
"$TOOL" restore "$T" >/dev/null 2>&1
same "$(snap)" "$S0" "count: restore from an unspent counter"
mk_unit; S0=$(snap)
expect "count: refuses 0 tries"                           1 "takes 1-9"          "$TOOL" count 0 "$T"
expect "count: refuses a non-digit"                       1 "takes 1-9"          "$TOOL" count 12 "$T"
same "$(snap)" "$S0" "count: a refused N wrote nothing"

# ---- refusals: nothing written, no remount -------------------------------------------------
refused() {   # <label> <substring> <verb> ; the fixture is already bent
    local s; s=$(snap); : > "$U/mount.log"
    expect "$1" 1 "$2" "$TOOL" "$3" "$T"
    same "$(snap)" "$s" "$1: wrote nothing"
    [ -s "$U/mount.log" ] && bad "$1: remounted /flash" || ok "$1: never remounted /flash"
}
mk_unit; { printf 'ANDROID!'; head -c 64 /dev/zero; } > "$U/flash/KERNEL"
refused "refuse: a boot.img rig"                  "not a PE image"       fallthrough
mk_unit; rm "$U/flash/loader/entries/rocknix.conf"
refused "refuse: no rocknix.conf"                 "not on OpenABL"       count
mk_unit; rm "$U/flash/loader/entries/etk-stock.conf"
refused "refuse: no fallback entry to land on"    "no etk-stock.conf"    fallthrough
mk_unit; printf 'default rocknix.conf\ntimeout 3\n' > "$U/flash/loader/loader.conf"
refused "refuse: a foreign loader.conf"           "not ours"             count
mk_unit; rm "$U/flash/KERNEL.gtk"
refused "refuse: no KERNEL.gtk to move"           "no KERNEL.gtk"        fallthrough
mk_unit; rm "$U/flash/loader/entries/etk-gtk.conf"
refused "refuse: no etk-gtk.conf to count"        "no etk-gtk.conf"      count
mk_unit; cp "$U/flash/loader/entries/etk-gtk.conf" "$U/flash/loader/entries/etk-gtk+1-2.conf"
refused "restore refuses two etk-gtk entries"     "both etk-gtk.conf"    restore
mk_unit; cp "$U/flash/KERNEL.gtk" "$U/flash/KERNEL.gtk.aside"
refused "restore refuses two GTK kernels"         "both KERNEL.gtk"      restore

echo
echo "test_bls_trial: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
