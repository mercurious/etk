#!/bin/bash
# ==========================================================
# ETK VAULT RECURSION PROBE — nested-shaders / self-loop finder
# ==========================================================
# READ-ONLY. Modifies nothing on the rig. Locates recursive shader
# directories in the vault — the `.../shaders/shaders/shaders/...`
# nesting that the vault self-loop class produces (a bare, dereferencing
# `ln -sf` onto the live cache symlink drops `$VAULT/shaders -> $VAULT`
# INSIDE the vault; it recurses to the kernel's 40-link limit and hangs
# the --copy-links vault PULL with "Too many levels of symbolic links").
#
# install.sh's etk_link_cache() was hardened to `ln -sfn` (2026-06-21),
# so the LIVE writer no longer forges the loop — but nothing REAPS an
# existing nested tree: vault_sweep.sh only walks the depth-2 shard tree
# (`-mindepth 2 -maxdepth 2`), so `shaders/shaders/...` is invisible to
# it and persists across reboots and installs. This probe surfaces it.
#
# RUN ON THE RIG (read-only ssh is always fine):
#   bash /storage/games-internal/roms/etk/tools/vault_recursion_probe.sh
# Or pipe the repo copy over ssh WITHOUT deploying to the rig:
#   ssh root@SM8250.local 'bash -s' < tools/vault_recursion_probe.sh
#
# BusyBox-safe: find -type/-maxdepth, readlink, ls, wc, du -k, awk. No
# GNU -printf/-regex, no `stat --format`, no `du -h`, no --long-options.
# `find` runs WITHOUT -L, so a symlink loop is listed once and never
# traversed — the probe cannot hang on the loop it is hunting.
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh

P(){ printf '  [OK]   %s\n' "$1"; }
F(){ printf '  [FAIL] %s\n' "$1"; }
W(){ printf '  [WARN] %s\n' "$1"; }
H(){ printf '\n=== %s ===\n' "$1"; }

VROOT="$ETK_ROOT/vault/$CHIPSET"

H "1. ENV / LAYOUT"
echo "  ETK_ROOT        = $ETK_ROOT"
echo "  CHIPSET         = $CHIPSET"
echo "  TARGET_ID       = $TARGET_ID"
echo "  VAULT root      = $VROOT"
echo "  VAULT_DIR       = $VAULT_DIR"
echo "  RPCS3_CACHE_DIR = $RPCS3_CACHE_DIR"
if [ ! -d "$VROOT" ]; then
    F "$VROOT does not exist — no vault to probe."
    exit 1
fi

H "2. LIVE CACHE SYMLINK (the loop's origin point)"
if [ -L "$RPCS3_CACHE_DIR" ]; then
    RAW="$(readlink "$RPCS3_CACHE_DIR" 2>/dev/null)"
    RES="$(readlink -f "$RPCS3_CACHE_DIR" 2>/dev/null)"
    echo "  raw target (1 level) = $RAW"
    echo "  resolved (readlink -f) = ${RES:-'(EMPTY — resolution failed)'}"
    if [ -z "$RES" ]; then
        F "cache symlink does not resolve — classic self-loop signature"
    elif ls "$RPCS3_CACHE_DIR/" >/dev/null 2>&1; then
        P "cache symlink resolves and is listable"
    else
        LSERR="$(ls "$RPCS3_CACHE_DIR/" 2>&1 | head -n1)"
        F "cache symlink present but unlistable: $LSERR"
    fi
    case "$RAW" in
        */shaders/shaders*|./shaders|shaders) F "cache raw target is itself nested/self-referential" ;;
    esac
elif [ -d "$RPCS3_CACHE_DIR" ]; then
    W "$RPCS3_CACHE_DIR is a REAL dir, not a symlink — a fold of this into the vault is how nesting is seeded"
elif [ -e "$RPCS3_CACHE_DIR" ]; then
    F "$RPCS3_CACHE_DIR exists but is neither symlink nor dir"
else
    W "$RPCS3_CACHE_DIR absent (normal only before the first boot pre-seed)"
fi

H "3. SYMLINK LOOPS UNDER THE VAULT"
# find without -L: a loop link is enumerated once, never followed.
LTMP=$(mktemp 2>/dev/null || echo "/tmp/etk_vrp_links.$$")
find "$VROOT" -maxdepth 50 -type l 2>/dev/null > "$LTMP"
NLINKS=$(wc -l < "$LTMP")
echo "  symlinks found under vault: $NLINKS"
LOOPS=0
while IFS= read -r L; do
    [ -z "$L" ] && continue
    T="$(readlink "$L" 2>/dev/null)"
    R="$(readlink -f "$L" 2>/dev/null)"
    SELF=0
    case "$T" in .|./|shaders|./shaders|"$L"|*/shaders/shaders*) SELF=1 ;; esac
    # A link whose full resolution is empty, or resolves to one of its own
    # ancestors, is a recursion source.
    if [ -z "$R" ]; then SELF=1; fi
    case "$L" in "$R"/*) SELF=1 ;; esac
    if [ "$SELF" = "1" ]; then
        LOOPS=$((LOOPS+1))
        echo "    [LOOP] $L  ->  $T   (resolves: ${R:-EMPTY})"
    fi
done < "$LTMP"
rm -f "$LTMP"
if [ "$LOOPS" -gt 0 ]; then
    F "$LOOPS self-referential / unresolvable symlink(s) — active loop source(s)"
else
    P "no self-referential symlinks under the vault"
fi

H "4. NESTED 'shaders/shaders' DIRECTORIES (real on-disk recursion)"
# Count how many path COMPONENTS are literally 'shaders'. A healthy vault
# dir (.../GAME/shaders) has exactly one; a nested one
# (.../GAME/shaders/shaders/...) has two or more, and that count IS the
# nesting depth — exact, unlike splitting on '/shaders/' which
# under-counts consecutive segments (non-overlapping matches).
NTMP=$(mktemp 2>/dev/null || echo "/tmp/etk_vrp_nest.$$")
find "$VROOT" -maxdepth 50 -type d -name shaders 2>/dev/null \
  | awk -F'/' '{n=0; for(i=1;i<=NF;i++) if($i=="shaders") n++; if(n>1) print n"\t"$0}' \
  | sort -rn > "$NTMP"
NNEST=$(wc -l < "$NTMP")
if [ "$NNEST" -eq 0 ]; then
    P "no nested shaders/shaders directories found"
else
    F "$NNEST nested shaders director(ies) found — deepest first:"
    echo "    depth(shaders-components)  path"
    head -n 20 "$NTMP" | sed 's/^/    /'
    MAXD=$(head -n1 "$NTMP" | cut -f1)
    echo "  max nesting: $MAXD 'shaders' components in one path (loop chains cap near the ~40-link limit)"
fi

H "5. AFFECTED GAME VAULTS + SIZE (read-only)"
if [ "$NNEST" -gt 0 ]; then
    # Name the top-level game dir under $VROOT for each nested path.
    cut -f2- "$NTMP" | awk -v root="$VROOT/" '{
        s=$0; sub(root,"",s); split(s,a,"/"); print a[1];
    }' | sort -u | while read -r GID; do
        [ -z "$GID" ] && continue
        GD="$VROOT/$GID"
        KB=$(du -sk "$GD" 2>/dev/null | awk '{print $1}')
        # depth-1 shaders (the legit vault) vs any deeper (the nest)
        DEEP=$(find "$GD" -maxdepth 50 -type d -name shaders 2>/dev/null \
               | awk -F'/' '{n=0; for(i=1;i<=NF;i++) if($i=="shaders") n++; if(n>1) print}' | wc -l)
        printf "    %-12s  total=%sKB  nested-shaders-dirs=%s\n" "$GID" "${KB:-?}" "$DEEP"
    done
else
    echo "  (none)"
fi

H "6. VAULT PULL SAFETY (does a --copy-links traversal choke?)"
# Mirror install.sh STEP 2's traversal without copying anything: -L makes
# find FOLLOW symlinks, so a live loop surfaces as the same "Too many
# levels of symbolic links" error that hangs the real rsync PULL.
ERR="$(find -L "$VROOT" -maxdepth 45 -type f 2>&1 >/dev/null | grep -i 'too many levels' | head -n1)"
if [ -n "$ERR" ]; then
    F "symlink-loop traversal error present — the --copy-links vault PULL WILL choke:"
    echo "      $ERR"
else
    P "no 'too many levels' error on a link-following traversal"
fi

H "SUMMARY"
VERDICT="CLEAN"
[ "$LOOPS" -gt 0 ] && VERDICT="RECURSION (symlink loop)"
[ "$NNEST" -gt 0 ] && VERDICT="RECURSION (nested real dirs)"
[ "$LOOPS" -gt 0 ] && [ "$NNEST" -gt 0 ] && VERDICT="RECURSION (loop + nested dirs)"
echo "  verdict            : $VERDICT"
echo "  self-ref symlinks  : $LOOPS"
echo "  nested shaders dirs: $NNEST"
rm -f "$NTMP"
echo
echo "=== PROBE COMPLETE (read-only) — paste this whole output back ==="
