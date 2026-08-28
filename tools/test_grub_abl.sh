#!/bin/sh
# ==========================================================
# tools/test_grub_abl.sh — STEP 6.4 abl_dev counter harness
# ==========================================================
# The 20260901 ROCKNIX grub generator adds an ABL model auto-select that runs
# AFTER load_env and OVERRIDES saved_entry (distributions/ROCKNIX/config/
# functions, generate_grub_cfg_body — commits 405240bf/b5a7a06d/ff8058e3).
# install.sh STEP 6.4 counters it in default mode by re-pointing the Flip-2
# match line at the ETK entry; uninstall.sh restores it. This harness proves:
#   1. anti-drift: the transforms under test are extracted VERBATIM from
#      install.sh/uninstall.sh — an edit there fails here first;
#   2. the counter is a no-op on the 20260801 (old-generator) artifact;
#   3. on the new-generator artifact it patches / restores idempotently and
#      touches ONLY the Flip-2 match line;
#   4. the failure it fixes is detectable (a test that cannot see the broken
#      state proves nothing — feedback law: tests must discriminate);
#   5. --rig: the same transforms produce byte-identical output under the
#      rig's BusyBox sh/sed (host GNU != BusyBox; read-only ssh + /tmp
#      scratch = the §1.4 disposable-harness clause, dies at next boot).
# The new-generator fixture is synthesized from the upstream generator source;
# AFTER the rig migrates, diff the REAL /flash/boot/grub/grub.cfg preamble
# against it — the fixture is the prediction, the rig file is the artifact.
set -u
cd "$(dirname "$0")/.." || exit 1
FAIL=0
PASS=0
ok()   { PASS=$((PASS+1)); printf 'ok   %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$*"; }
TD=$(mktemp -d /tmp/etk_ablharness_XXXXXX)
trap 'rm -rf "$TD"' EXIT

# --- 1. anti-drift extraction: the shipped transforms, verbatim ---
SED_DEF=$(grep -o "sed 's/set abl_dev=\"rpflip2\"/set abl_dev=\"etk-gtk-test\"/'" install.sh | head -1)
SED_TST=$(grep -o "sed 's/set abl_dev=\"etk-gtk-test\"/set abl_dev=\"rpflip2\"/'" install.sh | head -1)
SED_UNI=$(grep -o "sed 's/set abl_dev=\"etk-gtk-test\"/set abl_dev=\"rpflip2\"/'" uninstall.sh | head -1)
GRD_CNT=$(grep -c "grep -q 'set abl_dev=\"' \"\$CFG\"" install.sh)
[ -n "$SED_DEF" ] && ok "install.sh carries the default-mode counter sed" \
                  || fail "install.sh default-mode counter sed MISSING (drifted?)"
[ -n "$SED_TST" ] && ok "install.sh carries the test-mode restore sed" \
                  || fail "install.sh test-mode restore sed MISSING (drifted?)"
[ -n "$SED_UNI" ] && ok "uninstall.sh carries the abl restore sed" \
                  || fail "uninstall.sh abl restore sed MISSING (drifted?)"
[ "$GRD_CNT" -ge 1 ] && ok "install.sh guards the counter on block presence" \
                     || fail "install.sh block-presence guard MISSING"
# Fallback-default rewrite (2026-08-28: grubenv INERT on the internal 4Kn ESP
# under the new grub — the else-branch rewrite is THE default-boot mechanism).
FB_CORE='if ($0=="  set timeout=-1") { print "  set default=\"" id "\""; print "  set timeout=2" } else print'
FB_HARD='if ($0=="  set timeout=-1") { print "  set default=\"rpflip2\""; print "  set timeout=2" } else print'
[ "$(grep -cF "$FB_CORE" install.sh)" = "1" ] && ok "install.sh carries the parameterized fallback rewrite (kernel path)" \
                                              || fail "install.sh parameterized fallback rewrite MISSING (drifted?)"
[ "$(grep -cF "$FB_HARD" install.sh)" = "1" ] && ok "install.sh carries the stock-convergence fallback rewrite" \
                                              || fail "install.sh stock-convergence fallback rewrite MISSING (drifted?)"
[ "$FAIL" -eq 0 ] || { printf '%d/%d — aborting (transforms unextractable)\n' "$PASS" "$((PASS+FAIL))"; exit 1; }

# --- 2. fixtures ---
# OLD generator (20260801-class): no abl block; ETK entries present. Trimmed
# from the live rig artifact pulled 2026-08-28.
cat > "$TD/fx_old" << 'OLDGEN'
if [ "${saved_entry}" ]; then
  set timeout=2
  set default="${saved_entry}"
else
  set timeout=-1
fi
set timeout_style=menu
menuentry 'ROCKNIX-GTK for Flip 2' $menuentry_id_option 'etk-gtk-test' {
        savedefault
        search --set -f /KERNEL.gtktest
        linux /KERNEL.gtktest boot=LABEL=ROCKNIX disk=LABEL=STORAGE grub_portable rootwait console=tty0 quiet loglevel=3 panic=30 gpt msm.context_keepalive=1
        devicetree /boot/grub/sm8250-retroidpocket-flip2.dtb
}
menuentry 'Retroid Pocket 5' $menuentry_id_option 'rp5' {
        search --set -f /KERNEL
        linux /KERNEL boot=LABEL=ROCKNIX disk=LABEL=STORAGE grub_portable quiet rootwait console=tty0 video=efifb:off
}
OLDGEN
# NEW generator (20260901-class): synthesized from generate_grub_cfg_body
# (distributions/ROCKNIX/config/functions @ next 13e18947). The abl block and
# entry shape are the prediction to diff against the migrated rig's artifact.
cat > "$TD/fx_new" << 'NEWGEN'
insmod part_gpt
insmod part_msdos
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
  if [ "${abl_model}" = "Retroid Pocket Mini" ]; then set abl_dev="rpmini"; fi
  if [ "${abl_model}" = "Retroid Pocket Mini V2" ]; then set abl_dev="rpminiv2"; fi
fi

if [ -n "${abl_dev}" ]; then
  set default="${abl_dev}"
  set timeout=2
fi
set timeout_style=menu
menuentry 'Retroid Pocket Flip2' $menuentry_id_option 'rpflip2' {
        savedefault
        search --set -f /KERNEL
        linux /KERNEL boot=LABEL=ROCKNIX disk=LABEL=STORAGE grub_portable quiet rootwait console=tty0 video=efifb:off gpt
        devicetree /boot/grub/sm8250-retroidpocket-flip2.dtb
}
NEWGEN

# --- 3. the runner (shared by host + BusyBox legs): applies the extracted
#     transforms with markers so outputs can be byte-compared across shells ---
cat > "$TD/runner.sh" << RUNNER
set -u
apply_def() { $SED_DEF; }
apply_tst() { $SED_TST; }
apply_uni() { $SED_UNI; }
apply_fb()  { awk -v id="\$2" '{ $FB_CORE }' "\$1"; }
echo '==DEF-OLD=='; apply_def < "\$1/fx_old"
echo '==DEF-NEW=='; apply_def < "\$1/fx_new"
echo '==DEF-NEW-TWICE=='; apply_def < "\$1/fx_new" | apply_def
echo '==TST-AFTER-DEF=='; apply_def < "\$1/fx_new" | apply_tst
echo '==UNI-AFTER-DEF=='; apply_def < "\$1/fx_new" | apply_uni
echo '==FB-NEW-ETK=='; apply_fb "\$1/fx_new" etk-gtk-test
echo '==FB-NEW-DEV=='; apply_fb "\$1/fx_new" rpflip2
echo '==FB-OLD-DEV=='; apply_fb "\$1/fx_old" rpflip2
echo '==FB-TWICE=='; apply_fb "\$1/fx_new" rpflip2 > "\$1/fb_once"; apply_fb "\$1/fb_once" rpflip2
RUNNER

sh "$TD/runner.sh" "$TD" > "$TD/host.out" 2>&1 || fail "host runner errored"

# --- 4. host-leg assertions ---
sec() { awk -v s="==$2==" 'f && /^==/{exit} f{print} $0==s{f=1}' "$1"; }
# (2) old artifact: counter is a byte-identical no-op
sec "$TD/host.out" DEF-OLD > "$TD/def_old"
cmp -s "$TD/def_old" "$TD/fx_old" && ok "old-generator artifact: counter is a no-op" \
                                  || fail "old-generator artifact CHANGED by the counter"
# (4) discriminator first: the unpatched new artifact IS the broken state
grep -q 'set abl_dev="rpflip2"' "$TD/fx_new" \
  && ! grep -q 'set abl_dev="etk-gtk-test"' "$TD/fx_new" \
  && ok "broken state detectable (unpatched block overrides saved_entry)" \
  || fail "broken-state detector cannot see the unpatched block"
# (3) default mode: only the Flip-2 match line moves
sec "$TD/host.out" DEF-NEW > "$TD/def_new"
grep -q 'set abl_dev="etk-gtk-test"' "$TD/def_new" \
  && ! grep -q 'set abl_dev="rpflip2"' "$TD/def_new" \
  && ok "default mode: Flip-2 match re-pointed at etk-gtk-test" \
  || fail "default mode: Flip-2 match NOT re-pointed"
grep -q 'set abl_dev="rp5"' "$TD/def_new" && grep -q 'set abl_dev="rpminiv2"' "$TD/def_new" \
  && ok "default mode: other device match lines untouched" \
  || fail "default mode: collateral damage beyond the Flip-2 line"
[ "$(diff "$TD/fx_new" "$TD/def_new" | grep -c '^[<>]')" = "2" ] \
  && ok "default mode: exactly one line differs" \
  || fail "default mode: more than one line changed"
# idempotence
sec "$TD/host.out" DEF-NEW-TWICE > "$TD/def_new2"
cmp -s "$TD/def_new" "$TD/def_new2" && ok "default mode: idempotent" \
                                    || fail "default mode: NOT idempotent"
# test mode restores the stock artifact byte-identically
sec "$TD/host.out" TST-AFTER-DEF > "$TD/tst_out"
cmp -s "$TD/tst_out" "$TD/fx_new" && ok "test mode: stock line restored byte-identically" \
                                  || fail "test mode: restore is NOT byte-identical"
# uninstall restores the stock artifact byte-identically
sec "$TD/host.out" UNI-AFTER-DEF > "$TD/uni_out"
cmp -s "$TD/uni_out" "$TD/fx_new" && ok "uninstall: stock line restored byte-identically" \
                                  || fail "uninstall: restore is NOT byte-identical"
# fallback rewrite: default mode -> etk entry; stock/test -> device entry;
# exactly the else-branch changes; idempotent; safe on the old generator too
sec "$TD/host.out" FB-NEW-ETK > "$TD/fb_etk"
grep -q 'set default="etk-gtk-test"' "$TD/fb_etk" && ! grep -q 'set timeout=-1' "$TD/fb_etk" \
  && ok "fallback: default mode points the else-branch at etk-gtk-test" \
  || fail "fallback: default-mode rewrite wrong"
sec "$TD/host.out" FB-NEW-DEV > "$TD/fb_dev"
grep -q 'set default="rpflip2"' "$TD/fb_dev" && ! grep -q 'set timeout=-1' "$TD/fb_dev" \
  && ok "fallback: stock/test mode points the else-branch at rpflip2" \
  || fail "fallback: device-mode rewrite wrong"
[ "$(diff "$TD/fx_new" "$TD/fb_dev" | grep -c '^[<>]')" = "3" ] \
  && ok "fallback: exactly the 1-line-to-2-line else-branch change" \
  || fail "fallback: unexpected diff footprint"
sec "$TD/host.out" FB-OLD-DEV > "$TD/fb_old"
grep -q 'set default="rpflip2"' "$TD/fb_old" \
  && ok "fallback: applies safely on the old-generator artifact" \
  || fail "fallback: old-generator artifact not handled"
sec "$TD/host.out" FB-TWICE > "$TD/fb_twice"
cmp -s "$TD/fb_twice" "$TD/fb_dev" && ok "fallback: idempotent" \
                                   || fail "fallback: NOT idempotent"

# --- 5. --rig: BusyBox discrimination leg (read-only ssh; /tmp scratch) ---
if [ "${1:-}" = "--rig" ]; then
    RIG="${RIG_SSH:-root@169.254.170.2}"
    RT="/tmp/etk_ablharness_$$"
    if tar -C "$TD" -cf - fx_old fx_new runner.sh 2>/dev/null \
       | ssh "$RIG" "mkdir -p $RT && tar -C $RT -xf - && sh $RT/runner.sh $RT; R=\$?; rm -rf $RT; exit \$R" \
       > "$TD/rig.out" 2>"$TD/rig.err"; then
        if cmp -s "$TD/host.out" "$TD/rig.out"; then
            ok "BusyBox leg: rig output byte-identical to host"
        else
            fail "BusyBox leg: rig output DIFFERS from host (GNU/BusyBox sed divergence)"
            diff "$TD/host.out" "$TD/rig.out" | head -10
        fi
    else
        fail "BusyBox leg: rig run errored: $(head -2 "$TD/rig.err" 2>/dev/null)"
    fi
else
    printf 'note: BusyBox leg skipped (run with --rig for the discrimination pass)\n'
fi

printf '%d/%d passed\n' "$PASS" "$((PASS+FAIL))"
exit "$FAIL"
