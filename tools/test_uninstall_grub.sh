#!/bin/sh
# ==========================================================
# tools/test_uninstall_grub.sh — uninstall.sh GRUB-era boot-menu restore harness
# ==========================================================
# Found 2026-10-09 while planning the 20261001 migration for public v0.9.0
# users (the README advisory sends them through uninstall.sh first). The grub
# block lived inside uninstall.sh's UNQUOTED `CLEAN` heredoc, so the HOST
# expanded its $CFG / $GE / awk $0 before ssh: the rig received
# `[ -f "" ] || continue`, edited no grub.cfg, kept `set default=0` on
# 'etk-gtk-test' — and then deleted /flash/KERNEL.gtktest, the file that
# default entry boots. The fix moved it into its own QUOTED heredoc
# (GRUBRESTORE) that rebuilds both twins from the SYSTEM's canonical grub.cfg
# plus the numeric device-entry pin (install.sh KERNELCFGREMOTE's transform).
#
# Fixtures are car8's REAL files (ROCKNIX 20260901, default-mode install,
# read 2026-10-09): tools/fixtures/uninstall_grub/.
#
# This harness proves:
#   1. default-mode install, Flip2: both twins lose every ETK entry and every
#      reference to the GTK kernel files, equal canonical + the pin, and the
#      pinned default resolves to 'rpflip2'; the GTK files are gone, stock
#      KERNEL untouched, grubenv saved_entry=rpflip2 at 1024 bytes;
#   2. a sibling (Retroid Pocket 5) pins ITS entry, not the Flip2's;
#   3. no canonical cfg (older OS): ETK entries + pin + fallback stripped in place;
#   4. INVARIANT — a twin that still boots a kernel file never loses that file
#      (write failure: verdict FAIL, files kept, twins untouched);
#   5. idempotent: a second run converges to the same bytes;
#   6. ABL chain (no grub): nothing touched;
#   7. anti-drift: the rendered CLEAN heredoc carries no host-emptied operands,
#      uninstall's pin awk is byte-identical to install.sh's, and the
#      PowerShell port runs both kernel-restore bodies;
#   8. (--rig) the rig's BusyBox produces byte-identical results to the host.
#
# DISCRIMINATION: `--against <rev>` takes uninstall.sh from that revision (a
# pre-fix one has no GRUBRESTORE body: the CLEAN block is rendered the way ssh
# received it, /flash pointed at the sandbox).
#   tools/test_uninstall_grub.sh                     # working tree — must PASS
#   tools/test_uninstall_grub.sh --against 43741cd   # pre-fix — must FAIL
#   tools/test_uninstall_grub.sh --rig               # + BusyBox leg (writes only /tmp on the rig)
set -u
cd "$(dirname "$0")/.." || exit 1
REV=""; RIGLEG=0
while [ $# -gt 0 ]; do
    case "$1" in
        --against) REV="${2:?--against needs a revision}"; shift 2 ;;
        --rig) RIGLEG=1; shift ;;
        *) echo "usage: $0 [--against <rev>] [--rig]"; exit 2 ;;
    esac
done
FAIL=0; PASS=0
ok()   { PASS=$((PASS+1)); printf 'ok   %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$*"; }
TD=$(mktemp -d /tmp/etk_ungrub_XXXXXX)
[ -n "${KEEP:-}" ] && echo "sandbox: $TD" || trap 'rm -rf "$TD"' EXIT
mkdir -p "$TD/fx" "$TD/render"
cp tools/fixtures/uninstall_grub/* "$TD/fx/"
if [ -n "$REV" ]; then git show "$REV:uninstall.sh" > "$TD/uninstall.sh" || exit 1
    git show "$REV:windows_installer/etk-uninstall.ps1" > "$TD/etk-uninstall.ps1" 2>/dev/null
else cp uninstall.sh "$TD/uninstall.sh"; cp windows_installer/etk-uninstall.ps1 "$TD/etk-uninstall.ps1"; fi

# --- the rig body, exactly as ssh would deliver it ---
# rendered CLEAN heredoc (unquoted: the host expands it) — run in an empty cwd
s=$(grep -n '<< CLEAN$' "$TD/uninstall.sh" | head -1 | cut -d: -f1)
{ echo 'cat << CLEAN'; awk -v s="$s" 'NR>s { print; if ($0 == "CLEAN") exit }' "$TD/uninstall.sh"; } > "$TD/render/clean.sh"
(cd "$TD/render" && env -i PATH="$PATH" ZAP_VAULT=0 ETK_ROOT=/storage/etk bash clean.sh > clean.rendered 2>/dev/null)
if grep -q "<< 'GRUBRESTORE'" "$TD/uninstall.sh"; then
    awk 'index($0, "<< \x27GRUBRESTORE\x27") {inb=1; next} inb && $0 == "GRUBRESTORE" {exit} inb {print}' "$TD/uninstall.sh" > "$TD/body.sh"
else
    echo "note: no GRUBRESTORE body (pre-fix revision) — running the CLEAN block as rendered"
    awk '/# Custom kernel \(Tier K/ {inb=1} inb {print} inb && /Removed: GTK kernel entries/ {getline; print; exit}' "$TD/render/clean.rendered" > "$TD/body.sh"
fi
[ -s "$TD/body.sh" ] || { echo "FAIL no rig body extracted"; exit 1; }

# --- the runner: builds every case sandbox under $1/cases, runs the body, and
# prints a deterministic report (shared with the rig for the BusyBox leg) ---
cat > "$TD/runner.sh" << 'RUNNER'
#!/bin/sh
D=$1; cd "$D" || exit 1
mk() {  # mk <case> <canonical 1|0> [abl]
    C=$D/cases/$1; rm -rf "$C"; mkdir -p "$C/flash/boot/grub" "$C/stubs"
    printf 'STOCK-KERNEL\n' > "$C/flash/KERNEL"
    printf 'STOCK-KERNEL\n' > "$C/flash/KERNEL.etk-stock"
    if [ "${3:-}" = abl ]; then printf 'ANDROID!stock\n' > "$C/flash/KERNEL.etk-stock"; else
        mkdir -p "$C/flash/EFI/BOOT"
        printf 'GTK-KERNEL\n' > "$C/flash/KERNEL.gtktest"
        printf 'DTB\n' > "$C/flash/boot/grub/etk-flip2.dtb"
        printf 'DTB\n' > "$C/flash/boot/grub/sm8250-retroidpocket-flip2.dtb"
        for t in "$C/flash/EFI/BOOT" "$C/flash/boot/grub"; do
            cp "$D/fx/installed_default.cfg" "$t/grub.cfg"; cp "$D/fx/grubenv_default" "$t/grubenv"
        done
    fi
    [ "$2" = 1 ] && cp "$D/fx/canonical.cfg" "$C/canon.cfg"
    printf '#!/bin/sh\nexit 0\n' > "$C/stubs/mount"; chmod +x "$C/stubs/mount"
}
run() {  # run <case> <model> — a pre-fix body hardcodes /flash: point it at the sandbox
    C=$D/cases/$1
    sed "s#\\([ \"]\\)/flash#\\1$C/flash#g" "$D/body.sh" > "$C/body.sh"
    env FLASH="$C/flash" CANON="$C/canon.cfg" MODEL="$2" PATH="$C/stubs:$PATH" sh "$C/body.sh" >> "$C/out" 2>&1
    echo "rc=$?" >> "$C/out"
}
mk flip2 1;    run flip2 "Retroid Pocket Flip2"
cp -r "$D/cases/flip2" "$D/cases/twice"; : > "$D/cases/twice/out"; run twice "Retroid Pocket Flip2"
mk rp5 1;      run rp5 "Retroid Pocket 5"
mk nocanon 0;  run nocanon "Retroid Pocket Flip2"
mk rofs 1;     printf '#!/bin/sh\nexit 1\n' > "$D/cases/rofs/stubs/mv"; chmod +x "$D/cases/rofs/stubs/mv"
               run rofs "Retroid Pocket Flip2"
mk abl 1 abl;  run abl "Retroid Pocket Flip2"
for c in flip2 twice rp5 nocanon rofs abl; do
    C=$D/cases/$c
    echo "== $c $(grep -o '^GRUBRESTORE_[A-Z]*' "$C/out" | head -1) $(grep '^rc=' "$C/out" | tail -1)"
    (cd "$C/flash" && find . -type f ! -name '*.etkbak-*' ! -name '*.new' | LC_ALL=C sort | while read -r f; do
        printf '%s %s\n' "$(md5sum < "$f" | cut -c1-32)" "$f"; done)
done
RUNNER
sh "$TD/runner.sh" "$TD" > "$TD/host.out" 2>&1

# --- assertions (host) ---
K="$TD/cases"
twins() { echo "$K/$1/flash/EFI/BOOT/grub.cfg $K/$1/flash/boot/grub/grub.cfg"; }
entry_id() {  # entry_id <cfg> -> menuentry id the col-0 numeric default resolves to ("" if none)
    d=$(grep '^set default=' "$1" | tail -1 | sed 's/^set default=//')
    printf '%s' "$d" | grep -Eq '^[0-9]+$' || return 0
    awk -v d="$d" '/^menuentry /{ if (n == d+0) { print; exit } n++ }' "$1" | sed -n "s/.*menuentry_id_option '\([^']*\)'.*/\1/p"
}
invariant() {  # every kernel file a twin boots still exists
    for f in $(twins "$1"); do
        [ -f "$f" ] || continue
        for k in KERNEL.gtktest KERNEL.etk-stock; do
            if grep -q "/$k" "$f" && [ ! -f "$K/$1/flash/$k" ]; then echo "$(basename "$(dirname "$f")")/grub.cfg boots /$k but it was deleted"; fi
        done
    done
}
nopin() { awk '/^# ETK: pin default NUMERICALLY/ {skip=1} skip && /^$/ {skip=0; next} !skip' "$1"; }

# 1. Flip2, default-mode install
c=flip2
grep -q '^GRUBRESTORE_OK base=canonical' "$K/$c/out" && ok "flip2: verdict OK, rebuilt from canonical" || fail "flip2: no OK verdict ($(grep -v '^rc=' "$K/$c/out" | tail -1))"
for f in $(twins $c); do
    t=$(basename "$(dirname "$f")")
    grep -q -e "'etk-gtk" -e "'etk-fallback" -e "'etk-sdcard" "$f" && fail "flip2 $t: ETK menu entries survive" || ok "flip2 $t: no ETK menu entries"
    grep -q -e '/KERNEL.gtktest' -e '/KERNEL.etk-stock' "$f" && fail "flip2 $t: still boots a GTK kernel file" || ok "flip2 $t: boots no GTK kernel file"
    id=$(entry_id "$f"); [ "$id" = rpflip2 ] && ok "flip2 $t: numeric default resolves to rpflip2" || fail "flip2 $t: default resolves to '${id:-nothing}', want rpflip2"
    nopin "$f" | cmp -s - "$TD/fx/canonical.cfg" && ok "flip2 $t: == canonical + the pin" || fail "flip2 $t: differs from canonical beyond the pin"
done
v=$(invariant $c); [ -z "$v" ] && ok "flip2: invariant — no twin boots a deleted kernel" || fail "flip2: INVARIANT broken — $v"
[ ! -f "$K/$c/flash/KERNEL.gtktest" ] && [ ! -f "$K/$c/flash/KERNEL.etk-stock" ] && [ ! -f "$K/$c/flash/boot/grub/etk-flip2.dtb" ] \
  && ok "flip2: GTK kernel, parked stock and kit DTB removed" || fail "flip2: GTK files left behind"
[ "$(cat "$K/$c/flash/KERNEL")" = STOCK-KERNEL ] && [ -f "$K/$c/flash/boot/grub/sm8250-retroidpocket-flip2.dtb" ] \
  && ok "flip2: stock KERNEL + stock DTB untouched" || fail "flip2: stock files disturbed"
for t in EFI/BOOT boot/grub; do
    g="$K/$c/flash/$t/grubenv"
    grep -q '^saved_entry=rpflip2$' "$g" && [ "$(wc -c < "$g" | tr -d ' ')" = 1024 ] \
      && ok "flip2 $t/grubenv: saved_entry=rpflip2, 1024 bytes" || fail "flip2 $t/grubenv: $(grep saved_entry "$g") $(wc -c < "$g") bytes"
done

# 2. sibling
id=$(entry_id "$K/rp5/flash/boot/grub/grub.cfg"); [ "$id" = rp5 ] && ok "rp5: a sibling pins its own entry" || fail "rp5: default resolves to '${id:-nothing}', want rp5"

# 3. no canonical cfg
c=nocanon
grep -q '^GRUBRESTORE_OK base=stripped' "$K/$c/out" && ok "nocanon: verdict OK, stripped in place" || fail "nocanon: no stripped verdict"
f="$K/$c/flash/EFI/BOOT/grub.cfg"
grep -q -e "'etk-gtk" -e "'etk-fallback" -e "'etk-sdcard" -e '^# ETK: pin default' -e '^set fallback=' -e '/KERNEL.gtktest' "$f" \
  && fail "nocanon: ETK entries / pin / fallback / GTK kernel survive" || ok "nocanon: ETK entries, pin, fallback and GTK kernel refs stripped"
v=$(invariant $c); [ -z "$v" ] && ok "nocanon: invariant holds" || fail "nocanon: INVARIANT broken — $v"

# 4. write failure
c=rofs
grep -q '^GRUBRESTORE_FAIL' "$K/$c/out" && ! grep -q '^rc=0' "$K/$c/out" && ok "rofs: write failure reported (FAIL verdict, rc!=0)" || fail "rofs: write failure not reported"
[ -f "$K/$c/flash/KERNEL.gtktest" ] && [ -f "$K/$c/flash/KERNEL.etk-stock" ] && ok "rofs: GTK kernel files KEPT (menu still boots them)" || fail "rofs: deleted a kernel the menu still boots"
cmp -s "$K/$c/flash/EFI/BOOT/grub.cfg" "$TD/fx/installed_default.cfg" && ok "rofs: twins untouched" || fail "rofs: twins changed"
v=$(invariant $c); [ -z "$v" ] && ok "rofs: invariant holds" || fail "rofs: INVARIANT broken — $v"

# 5. idempotent
if cmp -s "$K/flip2/flash/boot/grub/grub.cfg" "$K/twice/flash/boot/grub/grub.cfg" && grep -q '^GRUBRESTORE_OK' "$K/twice/out"; then
    ok "twice: a second run converges to the same bytes"; else fail "twice: second run differs or failed"; fi

# 6. ABL chain
[ -f "$K/abl/flash/KERNEL.etk-stock" ] && ! grep -q '^GRUBRESTORE_' "$K/abl/out" && ok "abl: no grub -> untouched (ABLRESTORE owns the slot)" || fail "abl: touched an ABL unit"

# 7. anti-drift
grep -nE -e '\[ -[fde] "" \]' -e "^[^#]*' \"\" >" -e 'mv "\.tmp"' -e 'index\([^$"]*,"etk' "$TD/render/clean.rendered" > "$TD/empties"
[ -s "$TD/empties" ] && fail "CLEAN heredoc: host-emptied rig operands in what ssh sends ($(head -1 "$TD/empties"))" || ok "CLEAN heredoc: no host-emptied rig operands"
pin() { awk '/awk -v idx="\$IDX"/ {inb=1} inb {print} inb && /END \{ if \(!ins\)/ {exit}' "$1" | sed 's/^ *//'; }
if [ -z "$(pin "$TD/uninstall.sh")" ]; then fail "pin awk: none in uninstall.sh"
elif [ "$(pin "$TD/uninstall.sh")" = "$(pin install.sh)" ]; then ok "pin awk: uninstall == install.sh KERNELCFGREMOTE"
else fail "pin awk: uninstall drifted from install.sh"; fi

# the PowerShell port pulls rig bodies by marker: it must call the kernel restores
for m in GRUBRESTORE ABLRESTORE; do
    grep -q "Marker \"$m\"" "$TD/etk-uninstall.ps1" 2>/dev/null && ok "PS port: runs the $m body" || fail "PS port: never runs $m (lockstep drift)"
done

# 8. rig BusyBox leg
if [ "$RIGLEG" = 1 ]; then
    RIG="${RIG_SSH:-root@SM8250.local}"; RT="/tmp/etk_ungrub_$$"
    if tar -C "$TD" -cf - fx body.sh runner.sh \
       | ssh -o BatchMode=yes "$RIG" "mkdir -p $RT && tar -C $RT -xf - && sh $RT/runner.sh $RT; R=\$?; rm -rf $RT; exit \$R" \
       > "$TD/rig.out" 2> "$TD/rig.err"; then
        sed "s#$TD#@#g" "$TD/host.out" > "$TD/h"; sed "s#$RT#@#g" "$TD/rig.out" > "$TD/r"
        cmp -s "$TD/h" "$TD/r" && ok "BusyBox leg: rig results byte-identical to host" \
          || { fail "BusyBox leg: rig results DIFFER from host"; diff "$TD/h" "$TD/r" | head -12; }
    else
        fail "BusyBox leg: rig run errored: $(head -2 "$TD/rig.err")"
    fi
else
    echo "note: BusyBox leg skipped (--rig for the rig pass)"
fi

echo "test_uninstall_grub: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" = 0 ]
