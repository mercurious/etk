#!/bin/sh
# test_vault_link.sh — pins the Sentry's cache->vault link against the
# self-loop class: $VAULT/<ID>/shaders/shaders -> $VAULT/<ID>/shaders.
#
# WHY: the loop came back three times (2026-06-21, 2026-08-31, 2026-09-27)
# after the 06-21 `ln -sf` -> `ln -sfn` fix, and install.sh's --copy-links
# vault PULL wrote it out on the host as real trees nested 40 levels deep
# (21.9 GB of a 24.1 GB host vault, 14 games). Root cause (2026-09-30, from
# the 32 .pre-etk squatters on the rig — every one held a stray link):
#   1. etk_link_cache left the cache path MISSING (rm -f ... ln, and for
#      seconds during the squatter fold) at ignition, while RPCS3 brought up
#      Vulkan — Mesa mkdir'd a REAL cache dir into the gap;
#   2. `rm -f` can't remove a dir, so `ln -sfn` wrote `shaders -> <vault>`
#      INSIDE it (-n guards a symlink, not a real dir), logging LINKED;
#   3. the next call folded that squatter into the vault with rsync -a,
#      copying the stray link along: same game = a self-loop.
#
# HOW: extracts etk_link_cache / etk_heal_vault_loops VERBATIM from
# install.sh's Sentry heredoc and runs them in a sandbox, with Mesa's mkdir
# simulated at each moment the old code left a gap (shell functions shadow
# rsync/cp/rm/mv for the code under test and call the real applet).
#
# DISCRIMINATION: `--against <rev>` runs the same suite on that revision's
# install.sh. Against the pre-fix code (38f27e1) the squatter-fold, Mesa-race
# and heal cases FAIL; the suite passes only on the fix. `--rig` adds the
# BusyBox leg (bash + the rig's own rm/ln/mv/find/readlink, in /tmp, removed
# after) — host GNU != BusyBox, and `mv -T` refusing a directory is exactly
# the property the fix stands on.
#
#   tools/test_vault_link.sh                    # host leg, working tree
#   tools/test_vault_link.sh --rig              # + rig BusyBox leg
#   tools/test_vault_link.sh --against 38f27e1  # must FAIL (pre-fix)

set -u
cd "$(dirname "$0")/.." || exit 1

RIG_LEG=0; REV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --rig) RIG_LEG=1 ;;
        --against) REV="${2:-}"; shift ;;
        *) echo "usage: $0 [--rig] [--against <rev>]" >&2; exit 2 ;;
    esac
    shift
done

TD=$(mktemp -d "${TMPDIR:-/tmp}/vault_link_test.XXXXXX") || exit 1
trap 'rm -rf "$TD"' EXIT

SRC=install.sh
if [ -n "$REV" ]; then
    git show "$REV:install.sh" > "$TD/install.sh" || { echo "cannot read install.sh at $REV" >&2; exit 2; }
    SRC="$TD/install.sh"
    printf 'note: running against %s (a pre-fix revision is EXPECTED to fail)\n' "$REV"
fi
sed -n '/^etk_link_cache() {/,/^}/p; /^etk_heal_vault_loops() {/,/^}/p' "$SRC" > "$TD/funcs.sh"

cat > "$TD/runner.sh" << 'RUNNER'
#!/bin/bash
# Runs identically on host and rig. $1 = work dir holding funcs.sh, $2 = leg.
W="$1"; LEG="${2:-host}"
[ -n "$W" ] && [ -f "$W/funcs.sh" ] || { echo "runner: no funcs.sh in '$W'" >&2; exit 2; }
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  [%s] %s\n' "$LEG" "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m  [%s] %s\n' "$LEG" "$1"; }
chk() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# --- stand-ins. pkill: NEVER signal the rig's real vault_d.sh from a test.
# mesa_mkdir is Mesa's disk_cache_create: a plain mkdir, EEXIST ignored.
MESA_FOLD=0; MESA_RM=0; MESA_MV=0
pkill() { :; }
mesa_mkdir() { command mkdir "$RPCS3_CACHE_DIR" 2>/dev/null; }
rsync() { command rsync "$@"; _r=$?; [ "$MESA_FOLD" = 1 ] && mesa_mkdir; return $_r; }
cp()    { command cp "$@";    _r=$?; [ "$MESA_FOLD" = 1 ] && mesa_mkdir; return $_r; }
rm()    { command rm "$@";    _r=$?; [ "$MESA_RM" = 1 ] && [ "${!#}" = "$RPCS3_CACHE_DIR" ] && mesa_mkdir; return $_r; }
mv()    { _s="${@:$#-1:1}"; command mv "$@"; _r=$?; [ "$MESA_MV" = 1 ] && [ "$_s" = "$RPCS3_CACHE_DIR" ] && mesa_mkdir; return $_r; }

fresh() {  # a new sandbox; every global the functions read points into it
    SB="$W/sb.$1"; command rm -rf "$SB"
    command mkdir -p "$SB/cache" "$SB/etk/vault/SM8250"
    ETK_ROOT="$SB/etk"; CHIPSET=SM8250; V="$ETK_ROOT/vault/$CHIPSET"
    RPCS3_CACHE_DIR="$SB/cache/mesa_shader_cache"
    TRIPWIRE_LOG="$SB/trip.log"; : > "$TRIPWIRE_LOG"
    MESA_FOLD=0; MESA_RM=0; MESA_MV=0
}
vd()        { echo "$V/$1/shaders"; }
links_in()  { find "$1" -type l 2>/dev/null | wc -l | tr -d ' '; }
is_link()   { [ -L "$1" ] && [ "$(readlink "$1")" = "$2" ]; }
n_linked()  { grep -c 'CACHE LINKED' "$TRIPWIRE_LOG"; }
squat()     { command mkdir -p "$RPCS3_CACHE_DIR/ab"; echo shader > "$RPCS3_CACHE_DIR/ab/cdef"; }

. "$W/funcs.sh"
type etk_link_cache >/dev/null 2>&1 || bad "etk_link_cache extracted from install.sh"

# 1. fresh rig
fresh 1; etk_link_cache "$(vd G1)"; rc=$?
chk "fresh: cache path becomes a link to the game vault" '[ $rc = 0 ] && is_link "$RPCS3_CACHE_DIR" "$(vd G1)"'
chk "fresh: log says CACHE LINKED" 'grep -q "CACHE LINKED -> $(vd G1)" "$TRIPWIRE_LOG"'

# 2. re-point to another game
fresh 2; etk_link_cache "$(vd G1)"; etk_link_cache "$(vd G2)"
chk "re-point G1 -> G2: link swapped" 'is_link "$RPCS3_CACHE_DIR" "$(vd G2)"'
chk "re-point: nothing written inside either vault" '[ "$(links_in "$V")" = 0 ]'

# 3. same game twice
fresh 3; etk_link_cache "$(vd G2)"; n1=$(n_linked); etk_link_cache "$(vd G2)"
chk "same game twice: second call is a no-op" '[ "$(n_linked)" = "$n1" ]'

# 4. a squatter shaped exactly like the rig's 32 .pre-etk dirs: Mesa content
#    plus the stray `shaders -> <vault>` link an earlier ln dropped inside it
fresh 4; squat; command ln -s "$(vd G3)" "$RPCS3_CACHE_DIR/shaders"
etk_link_cache "$(vd G3)"
chk "squatter: cache path becomes the link" 'is_link "$RPCS3_CACHE_DIR" "$(vd G3)"'
chk "squatter: its shaders are folded into the vault" '[ -f "$(vd G3)/ab/cdef" ]'
chk "squatter: the stray link is NOT folded (no G3/shaders/shaders loop)" '[ "$(links_in "$V")" = 0 ]'
chk "squatter: kept as a .pre-etk backup (nothing deleted)" 'ls -d "$RPCS3_CACHE_DIR".pre-etk.* >/dev/null 2>&1'

# 5. Mesa recreates the cache dir while the squatter is being folded; same
#    game again (the 2026-08-31 / 2026-09-27 sequence: loop 7 s after a fold)
fresh 5; squat; MESA_FOLD=1
etk_link_cache "$(vd G4)"; etk_link_cache "$(vd G4)"; MESA_FOLD=0
chk "Mesa mkdir during the fold: cache path is still the link" 'is_link "$RPCS3_CACHE_DIR" "$(vd G4)"'
chk "Mesa mkdir during the fold: no self-loop after two calls" '[ "$(links_in "$V")" = 0 ]'

# 6. Mesa mkdir lands in the rm -> ln gap of a re-point
fresh 6; etk_link_cache "$(vd G5)"; MESA_RM=1; etk_link_cache "$(vd G6)"; MESA_RM=0
chk "Mesa mkdir in the re-point gap: link is G6, not a real dir" 'is_link "$RPCS3_CACHE_DIR" "$(vd G6)"'

# 7. a squatter that comes back every time it is moved
fresh 7; squat; MESA_MV=1; etk_link_cache "$(vd G7)"; rc=$?; MESA_MV=0
chk "persistent squatter: returns failure" '[ $rc != 0 ]'
chk "persistent squatter: logs CACHE LINK FAILED, never LINKED" 'grep -q "CACHE LINK FAILED" "$TRIPWIRE_LOG" && ! grep -q "CACHE LINKED" "$TRIPWIRE_LOG"'
chk "persistent squatter: no link written inside it or the vault" '[ "$(links_in "$RPCS3_CACHE_DIR")" = 0 ] && [ "$(links_in "$V")" = 0 ]'

# 8. heal: self-loop, cross-link, a loop inside a Tier A game dir; data, the
#    Tier A game link itself, and a REAL nested dir must survive
fresh 8
command mkdir -p "$(vd G7)/ab" "$(vd G8)" "$SB/internal/G9/shaders" "$(vd G10)/shaders/ab"
echo shader > "$(vd G7)/ab/cd"
command ln -s "$(vd G7)" "$(vd G7)/shaders"
command ln -s "$(vd G7)" "$(vd G8)/shaders"
command ln -s "$SB/internal/G9" "$V/G9"
command ln -s "$V/G9/shaders" "$SB/internal/G9/shaders/shaders"
if type etk_heal_vault_loops >/dev/null 2>&1; then etk_heal_vault_loops; else bad "etk_heal_vault_loops extracted from install.sh"; fi
chk "heal: self-loop, cross-link and Tier A loop removed" '[ ! -L "$(vd G7)/shaders" ] && [ ! -L "$(vd G8)/shaders" ] && [ ! -L "$SB/internal/G9/shaders/shaders" ]'
chk "heal: shader data, the Tier A game link and a real dir untouched" '[ -f "$(vd G7)/ab/cd" ] && [ -L "$V/G9" ] && [ -d "$(vd G10)/shaders/ab" ]'
chk "heal: each removal logged" '[ "$(grep -c "VAULT LOOP HEALED" "$TRIPWIRE_LOG")" = 3 ]'

# 9. install.sh's PULL exclude: a loop on the rig cannot nest on the host
fresh 9; command mkdir -p "$(vd G7)/ab"; echo shader > "$(vd G7)/ab/cd"
command ln -s "$(vd G7)" "$(vd G7)/shaders"
command rsync -a --copy-links --exclude='/*/shaders/shaders' "$V/" "$SB/host/" 2>/dev/null; rc=$?
chk "PULL with the exclude: clean exit, shaders copied, no nested tree" '[ $rc = 0 ] && [ -f "$SB/host/G7/shaders/ab/cd" ] && [ ! -e "$SB/host/G7/shaders/shaders" ]'
command rsync -a --copy-links "$V/" "$SB/host2/" 2>/dev/null
chk "control: WITHOUT the exclude --copy-links nests (the install symptom)" '[ -d "$SB/host2/G7/shaders/shaders/shaders" ]'

# 10. Tier A game dir: the vault path runs through a symlink
fresh 10; command mkdir -p "$SB/internal/G11/shaders"; command ln -s "$SB/internal/G11" "$V/G11"
etk_link_cache "$V/G11/shaders"; n1=$(n_linked); etk_link_cache "$V/G11/shaders"
chk "Tier A game: second call is a no-op (no re-link window every ignition)" '[ "$(n_linked)" = "$n1" ]'

printf '[%s] %d passed, %d failed\n' "$LEG" "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
RUNNER

RC=0
bash "$TD/runner.sh" "$TD" host || RC=1

# --- host-only: install.sh STEP 2's verdict line (host-side code; ssh stubbed).
# A probe that never ran must not print the OK line — an empty answer from a
# dead ssh once looked exactly like "no loops".
awk '/^VAULT_LOOPS=\$\(ssh/{f=1} f{print} f&&/^fi$/{exit}' "$SRC" > "$TD/step2.sh"
step2() {  # $1 = what the stubbed ssh prints; returns the say() lines
    STUB="$1" bash -c '
        ssh() { [ -n "$STUB" ] && printf "%b" "$STUB"; [ "$STUB" = DEAD ] && return 255; return 0; }
        say() { printf "%s\n" "$1"; }
        RIG_SSH=rig ETK_ROOT=/E CHIPSET=SM8250 Y= G= N=
        . "$0"' "$TD/step2.sh" 2>&1
}
s2() { if eval "$2"; then printf '  \033[32mPASS\033[0m  [host] %s\n' "$1"; else printf '  \033[31mFAIL\033[0m  [host] %s\n' "$1"; RC=1; fi; }
if [ -s "$TD/step2.sh" ]; then
    OUT=$(step2 '/E/vault/SM8250/G1/shaders/shaders -> /E/vault/SM8250/G1/shaders\nVAULT_LOOPS_DONE\n')
    s2 "STEP 2: a healed loop prints [HEAL] with its game" 'printf "%s" "$OUT" | grep -q "\[HEAL\].*G1/shaders/shaders"'
    OUT=$(step2 'VAULT_LOOPS_DONE\n')
    s2 "STEP 2: a clean vault prints [OK]" 'printf "%s" "$OUT" | grep -q "\[OK\] Shader vault: no self-loops"'
    OUT=$(step2 '')
    s2 "STEP 2: a probe that never ran WARNs, never OK" 'printf "%s" "$OUT" | grep -q "\[WARN\]" && ! printf "%s" "$OUT" | grep -q "\[OK\]"'
else
    printf '  \033[31mFAIL\033[0m  [host] STEP 2 self-loop probe not found in install.sh\n'; RC=1
fi

if [ "$RIG_LEG" = 1 ]; then
    RIG="${RIG_SSH:-root@SM8250.local}"
    RT="/tmp/etk_vaultlink_$$"
    tar -C "$TD" -cf - funcs.sh runner.sh \
      | ssh "$RIG" "mkdir -p $RT && tar -C $RT -xf - && bash $RT/runner.sh $RT rig; R=\$?; rm -rf $RT; exit \$R" \
      || RC=1
else
    printf 'note: BusyBox leg skipped (run with --rig for the discrimination pass)\n'
fi
exit $RC
