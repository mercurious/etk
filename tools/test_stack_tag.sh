#!/bin/sh
# test_stack_tag.sh — pins etk_stack_tag()'s RPCS3 r-component parsing.
#
# WHY: every ledger row after the 0.9.0.x core mints read `r?` — the parser
# matched the banner's `etk/<ver>` field, which was the BUILD BRANCH NAME of
# the 0.8.x docker builds (PROVENANCE-0.8.x.md shows it drifting from the
# actual source: "three fields, two wrong"). Forge mints build from a detached
# tree, so their banners carry no etk/ field at all. The fix matches the
# `GTK Edition v<ver>` stamp — the one field every lineage carries and the one
# PROVENANCE names as truthful — with etk/ kept as a fallback for old archives.
#
# DISCRIMINATION: fixture 1 (a forge-minted 0.9.0.x banner) yields r? under the
# pre-fix regex and r0.9.0.2 under the fix — this suite fails on the broken
# version. Fixture 4 pins the honest tell: stock banners must STAY r?
# (2026-08-27: r? was the only sign of a rotted core marker).
#
# BusyBox-safe (sh, no bashisms; grep -m/-a/-o/-E, head -c, cut, sed only —
# every flag already load-bearing in scripts/env.sh on the rig).

set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }

# Evaluate the SHIPPED function body verbatim (sourcing env.sh mkdirs rig
# paths; the extract keeps the test side-effect-free on host and rig alike).
eval "$(sed -n '/^etk_stack_tag()/,/^}/p' scripts/env.sh)"
command -v etk_stack_tag >/dev/null 2>&1 || { bad "etk_stack_tag not extractable from scripts/env.sh"; exit 1; }

TMP="${TMPDIR:-/tmp}/stack_tag_test.$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

r_of() {  # run the tag against one fixture log, print the r component
    RPCS3_LOG="$1" etk_stack_tag | sed 's/.*\/r//'
}

check() { # label expected fixture
    _got=$(r_of "$3")
    if [ "$_got" = "$2" ]; then ok "$1 -> r$_got"
    else bad "$1 -> r$_got (expected r$2)"; fi
}

# 1. Forge-minted 0.9.0.x core (detached tree: no etk/ branch field).
#    THE discriminator: pre-fix parser returns "?" here.
printf 'RPCS3 v0.0.41-19638-a1deb2921 | GTK Edition v0.9.0.2 (armsx3-a74a0f3e0) | local_build\n' > "$TMP/mint.log"
check "forge mint banner (0.9.0.x)" "0.9.0.2" "$TMP/mint.log"

# 2. Legacy 0.8.x docker build: BOTH fields present, and per PROVENANCE the
#    etk/ branch field is the drifted one — the GTK stamp must win.
printf 'RPCS3 v0.0.41-19642-f7a5d6eb | etk/0.8.2-dev | GTK Edition v0.8.4-dev\n' > "$TMP/legacy.log"
check "legacy banner, both fields (GTK stamp wins)" "0.8.4-dev" "$TMP/legacy.log"

# 3. Archive with ONLY the etk/ field: the fallback still attributes it.
printf 'RPCS3 v0.0.40-19544-60c9705a | etk/0.7.5 | Alpha\n' > "$TMP/old.log"
check "etk/-only archive (fallback path)" "0.7.5" "$TMP/old.log"

# 4. Stock RPCS3: neither field. r stays "?" — the honest tell. A parser
#    "improvement" that invents a value here breaks the 2026-08-27 detector.
printf 'RPCS3 v0.0.42-19895-c6e96729c Alpha | local_build\n' > "$TMP/stock.log"
check "stock banner stays r? (honest tell)" "?" "$TMP/stock.log"

# 5. Missing log (pre-launch postmortem, wiped cache): r? not a crash.
check "missing log stays r?" "?" "$TMP/nonexistent.log"

# 6. Marker beyond the 256K read bound is NOT attributed (the banner lives on
#    line 1; a chat string deep in the log must not spoof the stack).
{ head -c 300000 /dev/zero | tr '\0' 'x'; printf '\nGTK Edition v9.9.9\n'; } > "$TMP/deep.log"
check "deep-log spoof beyond bound ignored" "?" "$TMP/deep.log"

echo
if [ "$FAIL" -eq 0 ]; then
    echo "ALL STACK-TAG CHECKS PASSED ($PASS)"
else
    echo "STACK-TAG CHECKS FAILED: $FAIL of $((PASS+FAIL))"
    exit 1
fi
