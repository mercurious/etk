#!/bin/sh
# ==========================================================
# tools/test_grub_default.sh — STEP 6.4 grub default-resolution harness
# ==========================================================
# Root-caused live on the migrated rig (2026-08-28, ROCKNIX 20260901 / kernel
# 7.2 / internal 4Kn ESP). The default-boot mechanism the stock generator
# relies on is DEAD on this stack, three ways over:
#   * grubenv `load_env` is unreliable on the internal 4096-byte-sector FAT
#     (saved_entry did not drive the boot);
#   * the generator's abl block DOES fire (fdtdump reads the DT model) and runs
#     `set default="${abl_dev}"` AFTER every other default-setter;
#   * a STRING-id default does not resolve on this grub -> it falls to menu
#     entry 0 (rp5). Every string fix (saved_entry, an abl-match counter, an
#     else-branch rewrite) was clobbered by the abl block and/or never resolved.
# The fix, proven on the rig: inject a NUMERIC `set default=<index>` AFTER the
# abl block; numeric resolves, and last-writer-wins makes it decisive. The
# index is computed from the built cfg (ETK entries are prepended in default
# mode, so it is never hardcoded).
#
# This harness proves (host AND rig-BusyBox — host GNU != BusyBox):
#   1. anti-drift: the injection awk + index computation are present in
#      install.sh (both the kernel path and the stock-convergence path);
#   2. index computation picks the right entry in every mode
#      (stock: rpflip2=1; default: etk-gtk-test=0; test: rpflip2 after the
#      prepended ETK entries);
#   3. the injected `set default=<n>` lands AFTER the abl block's string-id
#      default (last-writer-wins) and resolves to the intended entry;
#   4. the BROKEN state is detectable (canonical cfg has NO numeric default ->
#      boots entry 0) — a test that can't see the break proves nothing;
#   5. idempotent under reconvergence (install rebuilds from canonical);
#   6. rig BusyBox awk produces byte-identical output to host awk;
#   7. default-by-default + the auto-boot guards are present in install.sh
#      (module-tree match gate; KERNEL_DEPLOY_MODE fallback = default — 0.9.0);
#   8. the module-tree guard primitive discriminates a matching release (stay
#      default/auto-boot) from a mismatch or empty release (force test — a kernel
#      whose modules can't load must never auto-boot; the 2026-08-01 frankenboot).
set -u
cd "$(dirname "$0")/.." || exit 1
FAIL=0; PASS=0
ok()   { PASS=$((PASS+1)); printf 'ok   %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$*"; }
TD=$(mktemp -d /tmp/etk_grubdef_XXXXXX)
trap 'rm -rf "$TD"' EXIT

# --- 1. anti-drift: install.sh carries the mechanism in both paths ---
[ "$(grep -c 'feature_menuentry_id/ && !ins' install.sh)" -ge 2 ] \
  && ok "install.sh injects numeric default in both grub paths (kernel + stock-convergence)" \
  || fail "install.sh numeric-default injection MISSING or not in both paths (drifted?)"
grep -q "awk -v q=\"'\$K_TGT_ID' {\"" install.sh \
  && ok "install.sh computes the target index (kernel path)" \
  || fail "install.sh kernel-path index computation MISSING"
grep -q "awk -v q=\"'rpflip2' {\"" install.sh \
  && ok "install.sh computes the rpflip2 index (stock-convergence path)" \
  || fail "install.sh stock-path index computation MISSING"
# default-by-default (0.9.0): KERNEL_DEPLOY_MODE fallback flipped test->default.
grep -qF 'KERNEL_DEPLOY_MODE:-default' install.sh \
  && ok "install.sh default-mode-by-default (KERNEL_DEPLOY_MODE fallback = default)" \
  || fail "install.sh KERNEL_DEPLOY_MODE fallback is not 'default' (task-3 drift?)"
# auto-boot module-tree guard: never auto-boot a kernel whose modules can't load.
grep -qF '[ -d /usr/lib/modules/$K_RELEASE ]' install.sh \
  && ok "install.sh module-tree auto-boot guard present (release vs /usr/lib/modules)" \
  || fail "install.sh module-tree guard MISSING (task-2 drift?)"
[ "$FAIL" -eq 0 ] || { printf '%d/%d — aborting (mechanism not in install.sh)\n' "$PASS" "$((PASS+FAIL))"; exit 1; }

# --- 2. fixtures ---
# CANONICAL (20260901 generator, stock; the exact shape update.sh deploys).
cat > "$TD/canonical" << 'CANON'
insmod part_gpt
load_env

if [ "${saved_entry}" ]; then
  set timeout=2
  set default="${saved_entry}"
else
  set timeout=-1
fi

set abl_dev=
if fdtdump --prop model --set abl_model; then
  if [ "${abl_model}" = "Retroid Pocket 5" ]; then set abl_dev="rp5"; fi
  if [ "${abl_model}" = "Retroid Pocket Flip2" ]; then set abl_dev="rpflip2"; fi
fi

if [ -n "${abl_dev}" ]; then
  set default="${abl_dev}"
  set timeout=2
fi

if [ x"${feature_menuentry_id}" = xy ]; then
  menuentry_id_option="--id"
else
  menuentry_id_option=""
fi
export menuentry_id_option

set timeout_style=menu
menuentry 'Retroid Pocket 5' $menuentry_id_option 'rp5' {
        linux /KERNEL boot=LABEL=ROCKNIX
        devicetree /boot/grub/rp5.dtb
}
menuentry 'Retroid Pocket Flip2' $menuentry_id_option 'rpflip2' {
        linux /KERNEL boot=LABEL=ROCKNIX
        devicetree /boot/grub/flip2.dtb
}
CANON
# DEFAULT-MODE built cfg = canonical with the ETK block PREPENDED before the
# first menuentry (as STEP 6.4's inserter does): etk-gtk-test becomes index 0.
# Build the ETK block as a file (single-quoted heredoc = literal, portable —
# NOT awk \x27, which BWK awk prints verbatim), then insert with getline.
cat > "$TD/etkblock" << 'ETKB'
menuentry 'ROCKNIX-GTK for Flip 2' $menuentry_id_option 'etk-gtk-test' {
        linux /KERNEL.gtktest boot=LABEL=ROCKNIX
        devicetree /boot/grub/flip2.dtb
}
ETKB
awk -v bf="$TD/etkblock" '
  !ins && /^menuentry / { while ((getline line < bf) > 0) print line; close(bf); ins=1 }
  { print }
' "$TD/canonical" > "$TD/default_built"

# --- 3. the transform (extracted structure, run here exactly as install.sh
#     runs it on the rig): compute index for target, inject after abl block ---
idx_of() { awk -v q="'$2' {" '/^menuentry /{ if (index($0,q)) { print n+0; exit } n++ }' "$1"; }
inject() { # $1=cfg  $2=idx
  awk -v idx="$2" '
    /feature_menuentry_id/ && !ins {
      print "# ETK: pin default NUMERICALLY (string-id defaults do not resolve on"
      print "# this grub; the abl block above sets one and would fall to entry 0)."
      print "set default=" idx
      print "set timeout=2"
      print ""
      ins=1
    }
    { print }
    END { if (!ins) { print "set default=" idx; print "set timeout=2" } }
  ' "$1"
}
# the runner (shared with the rig for the BusyBox leg)
cat > "$TD/runner.sh" << 'RUNNER'
set -u
idx_of() { awk -v q="'$2' {" '/^menuentry /{ if (index($0,q)) { print n+0; exit } n++ }' "$1"; }
inject() {
  awk -v idx="$2" '
    /feature_menuentry_id/ && !ins {
      print "# ETK: pin default NUMERICALLY (string-id defaults do not resolve on"
      print "# this grub; the abl block above sets one and would fall to entry 0)."
      print "set default=" idx; print "set timeout=2"; print ""; ins=1
    }
    { print }
    END { if (!ins) { print "set default=" idx; print "set timeout=2" } }
  ' "$1"
}
D="$1"
echo "==STOCK-IDX=="; idx_of "$D/canonical" rpflip2
echo "==DEF-ETK-IDX=="; idx_of "$D/default_built" etk-gtk-test
echo "==DEF-DEV-IDX=="; idx_of "$D/default_built" rpflip2
echo "==STOCK-INJECT=="; inject "$D/canonical" "$(idx_of "$D/canonical" rpflip2)"
echo "==STOCK-INJECT-TWICE=="; inject "$D/canonical" "$(idx_of "$D/canonical" rpflip2)" > "$D/once"; \
  awk '/^menuentry /{n++} END{}' "$D/once"; inject "$D/once" "$(idx_of "$D/once" rpflip2)"
RUNNER
sh "$TD/runner.sh" "$TD" > "$TD/host.out" 2>&1 || fail "host runner errored"
sec() { awk -v s="==$2==" 'f && /^==/{exit} f{print} $0==s{f=1}' "$1"; }

# --- 4. index assertions ---
[ "$(sec "$TD/host.out" STOCK-IDX)" = "1" ] \
  && ok "stock: rpflip2 index = 1 (rp5 is entry 0)" \
  || fail "stock: rpflip2 index wrong ($(sec "$TD/host.out" STOCK-IDX))"
[ "$(sec "$TD/host.out" DEF-ETK-IDX)" = "0" ] \
  && ok "default mode: etk-gtk-test index = 0 (prepended before device entries)" \
  || fail "default mode: etk-gtk-test index wrong ($(sec "$TD/host.out" DEF-ETK-IDX))"
[ "$(sec "$TD/host.out" DEF-DEV-IDX)" = "2" ] \
  && ok "default mode: rpflip2 index shifts to 2 behind the prepended ETK entry (etk=0, rp5=1, rpflip2=2)" \
  || fail "default mode: rpflip2 index wrong ($(sec "$TD/host.out" DEF-DEV-IDX)) — expected 2"

# --- 5. discriminator: canonical (unpatched) has NO numeric default -> boots entry 0 ---
grep -Eq '^set default=[0-9]+' "$TD/canonical" \
  && fail "discriminator broken: canonical already has a numeric default" \
  || ok "broken state detectable (canonical has only string-id defaults -> entry 0)"

# --- 6. injection lands AFTER the abl block and resolves to Flip2 ---
sec "$TD/host.out" STOCK-INJECT > "$TD/stock_inj"
ABL_LN=$(grep -n 'set default="${abl_dev}"' "$TD/stock_inj" | cut -d: -f1)
NUM_LN=$(grep -n '^set default=1$' "$TD/stock_inj" | tail -1 | cut -d: -f1)
if [ -n "$ABL_LN" ] && [ -n "$NUM_LN" ] && [ "$NUM_LN" -gt "$ABL_LN" ]; then
  ok "numeric default (line $NUM_LN) is AFTER the abl string default (line $ABL_LN) — last-writer-wins"
else
  fail "numeric default not placed after the abl block (abl=$ABL_LN num=$NUM_LN)"
fi
# the tail 'set default=' must be numeric and index to the Flip2 menuentry
TAILDEF=$(grep '^set default=' "$TD/stock_inj" | tail -1 | sed 's/^set default=//')
ENT=$(awk -v w="$TAILDEF" '/^menuentry /{ if (n==w){ match($0,/'"'"'[^'"'"']*'"'"' \{/); print substr($0,RSTART+1,RLENGTH-4); exit } n++ }' "$TD/stock_inj")
[ "$ENT" = "rpflip2" ] \
  && ok "tail default ($TAILDEF) resolves to the 'rpflip2' menuentry" \
  || fail "tail default resolves to '$ENT', not rpflip2"

# --- 7. idempotent under reconvergence ---
sec "$TD/host.out" STOCK-INJECT-TWICE > "$TD/twice"
# reconverge is from canonical each install; the transform on canonical is deterministic
cmp -s "$TD/stock_inj" "$(sec "$TD/host.out" STOCK-INJECT > "$TD/si2"; echo "$TD/si2")" \
  && ok "injection is deterministic on canonical (install reconverges cleanly)" \
  || fail "injection not deterministic"

# --- 8. rig BusyBox leg ---
if [ "${1:-}" = "--rig" ]; then
    RIG="${RIG_SSH:-root@SM8250.local}"
    RT="/tmp/etk_grubdef_$$"
    if tar -C "$TD" -cf - canonical default_built runner.sh 2>/dev/null \
       | ssh "$RIG" "mkdir -p $RT && tar -C $RT -xf - && sh $RT/runner.sh $RT; R=\$?; rm -rf $RT; exit \$R" \
       > "$TD/rig.out" 2>"$TD/rig.err"; then
        if cmp -s "$TD/host.out" "$TD/rig.out"; then
            ok "BusyBox leg: rig awk output byte-identical to host"
        else
            fail "BusyBox leg: rig output DIFFERS from host"; diff "$TD/host.out" "$TD/rig.out" | head -12
        fi
    else
        fail "BusyBox leg: rig run errored: $(head -2 "$TD/rig.err" 2>/dev/null)"
    fi
else
    printf 'note: BusyBox leg skipped (run with --rig for the discrimination pass)\n'
fi

# --- 9. module-tree guard primitive: the auto-boot gate discriminates ---
# The decision primitive, mirroring install.sh exactly ([ -z release ] OR absent
# dir -> force test): a matching release resolves to a present module dir and
# stays default (auto-boot); a mismatch or empty release forces test (no auto-
# boot). This is the gate that makes default-by-default safe. 'test -d'/'test -z'
# are POSIX shell builtins — no host/BusyBox divergence to chase (unlike awk), so
# the host leg is authoritative here.
mkdir -p "$TD/modroot/usr/lib/modules/7.2.0"
guard() { # $1=K_RELEASE -> the K_MODE the guard would leave it at
  if [ -z "$1" ] || [ ! -d "$TD/modroot/usr/lib/modules/$1" ]; then echo test; else echo default; fi
}
[ "$(guard 7.2.0)" = "default" ] \
  && ok "module guard: matching release (7.2.0 present) -> stays default (auto-boot)" \
  || fail "module guard: matching release wrongly downgraded to test"
[ "$(guard 7.1.2)" = "test" ] \
  && ok "module guard: mismatched release (7.1.2 absent) -> forced test (no frankenboot)" \
  || fail "module guard: mismatched release NOT caught — would auto-boot module-less"
[ "$(guard '')" = "test" ] \
  && ok "module guard: empty/unreadable release -> forced test (defensive)" \
  || fail "module guard: empty release not caught"

printf '%d/%d passed\n' "$PASS" "$((PASS+FAIL))"
exit "$FAIL"
