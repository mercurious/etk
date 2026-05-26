#!/bin/bash
# ==========================================================
# ETK VAULT SWEEP — prune shaders orphaned by a Mesa rebuild
# ==========================================================
# Run ON THE RIG:
#   bash /storage/games-internal/roms/etk/tools/vault_sweep.sh           # dry-run, current game
#   bash /storage/games-internal/roms/etk/tools/vault_sweep.sh --apply   # commit
#   bash /storage/games-internal/roms/etk/tools/vault_sweep.sh --all-games --apply
#
# WHY
#   Every Rocknix nightly rebuilds Mesa from source; the resulting
#   libvulkan_freedreno.so build ID drifts even when upstream is unchanged,
#   invalidating the prior Turnip cache layer. Without active management,
#   the per-game vault grows linearly with the count of Mesa-touching
#   nightlies (empirical: GT5P doubled 9.5k -> 19k files in one session
#   after the 20260525 nightly).
#
# ALGORITHM (deviation from addendum §G.5 spec — read before editing)
#   Spec said: parse Mesa's `index` file for live hash entries, delete
#   files whose hash key is no longer indexed. Empirical probe (2026-05-26)
#   found the index is a binary hash table that stores a TRANSFORMED key
#   (not the raw on-disk filename hash); the transform lives in Mesa's
#   disk_cache_db.c and drifts across Mesa versions. A literal parser is
#   brittle.
#
#   Substitute: use the mtime of $ETK_ROOT/vault/.last_mesa.hash as the
#   rebuild boundary. install.sh writes that file every time it detects a
#   new libvulkan_freedreno.so build ID. After a rebuild, every shader
#   file with mtime < rebuild_epoch is orphaned BY DEFINITION — Mesa keyed
#   it against the old build ID and will never read it again. This is
#   conservative (won't sweep anything Mesa has rewritten post-rebuild)
#   and self-explanatory (no Mesa internals to track).
#
# SAFETY
#   - Dry-run by default. --apply required to delete.
#   - Per-game scope by default. --all-games extends.
#   - Refuses to run if vault/.last_mesa.hash is absent (no boundary).
#   - Refuses to run on the currently-running game's vault (Mesa cache is
#     open; mtimes are mid-flux). Quit the game first.
# ==========================================================

source /storage/games-internal/roms/etk/scripts/env.sh

APPLY=0
ALL_GAMES=0
for arg in "$@"; do
    case "$arg" in
        --apply)     APPLY=1 ;;
        --all-games) ALL_GAMES=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown arg: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'

MESA_HASH_FILE="$ETK_ROOT/vault/.last_mesa.hash"
if [ ! -f "$MESA_HASH_FILE" ]; then
    echo -e "${R}[ABORT] $MESA_HASH_FILE absent — no rebuild boundary to sweep against.${N}"
    echo -e "        Run \`install.sh\` once from the host so Step 0 seeds the fingerprint,"
    echo -e "        then re-run this sweep AFTER the next Rocknix nightly bumps Mesa."
    exit 1
fi

CUTOFF_EPOCH=$(stat -c '%Y' "$MESA_HASH_FILE" 2>/dev/null)
case "$CUTOFF_EPOCH" in ''|*[!0-9]*)
    echo -e "${R}[ABORT] cannot read mtime of $MESA_HASH_FILE${N}"
    exit 1
;; esac
CUTOFF_HUMAN=$(date -d "@$CUTOFF_EPOCH" 2>/dev/null || date -r "$CUTOFF_EPOCH" 2>/dev/null || echo "epoch=$CUTOFF_EPOCH")

# Refuse if a PS3 game is running — Mesa has the cache open, mtimes are
# mid-flux, and sweeping a hot shader would mid-write delete a file Mesa
# is actively reading. Quit the game first.
if pgrep -f "rpcs3|AppRun.wrapped" >/dev/null 2>&1; then
    echo -e "${R}[ABORT] RPCS3 is running. Quit the game and re-run this sweep.${N}"
    exit 1
fi

# Build the list of game IDs to sweep.
VAULT_ROOT="$ETK_ROOT/vault/$CHIPSET"
if [ ! -d "$VAULT_ROOT" ]; then
    echo -e "${R}[ABORT] $VAULT_ROOT does not exist.${N}"
    exit 1
fi
if [ "$ALL_GAMES" = "1" ]; then
    GAMES=$(ls "$VAULT_ROOT" 2>/dev/null)
else
    # Resolve current TARGET_ID via env.sh's logic. Reject IDLE/UNKNOWN_ID
    # — sweeping the IDLE sentinel would walk a non-game vault and surprise
    # nothing, but explicit refusal beats silent no-op.
    case "$TARGET_ID" in
        ""|IDLE|UNKNOWN_ID)
            echo -e "${R}[ABORT] TARGET_ID is '$TARGET_ID' — no game vault to sweep.${N}"
            echo -e "        Use --all-games to sweep every vault, or launch the target game once"
            echo -e "        then quit so env.sh resolves to a real ID."
            exit 1
        ;;
    esac
    GAMES="$TARGET_ID"
fi

if [ "$APPLY" = "1" ]; then
    MODE_BANNER="${R}APPLY MODE — files WILL be deleted${N}"
else
    MODE_BANNER="${Y}DRY-RUN — no files will be deleted (pass --apply to commit)${N}"
fi

echo -e "${C}=========================================================="
echo -e "  ETK VAULT SWEEP"
echo -e "==========================================================${N}"
echo -e "  mode    : $MODE_BANNER"
echo -e "  cutoff  : ${Y}$CUTOFF_HUMAN${N}  (from mtime of .last_mesa.hash)"
echo -e "  scope   : $(echo $GAMES | wc -w) game(s)"
echo ""

TOTAL_FILES=0
TOTAL_BYTES=0

for GAME in $GAMES; do
    SHADER_DIR="$VAULT_ROOT/$GAME/shaders"
    [ -d "$SHADER_DIR" ] || { echo -e "  ${Y}[SKIP] $GAME — no shaders dir${N}"; continue; }

    # Find shader cache files older than the rebuild epoch. Restrict to the
    # 256-shard tree (paths like .../shaders/<hex>/<hex>) so the `index`
    # and `marker` files at the shaders/ root are never candidates — those
    # are Mesa's own bookkeeping and rewritten on every cache open.
    # BusyBox find supports -mindepth/-maxdepth on Rocknix.
    TMP=$(mktemp 2>/dev/null || echo "/tmp/etk_sweep.$$")
    find "$SHADER_DIR" -mindepth 2 -maxdepth 2 -type f \
         ! -newer "$MESA_HASH_FILE" 2>/dev/null > "$TMP"
    GAME_FILES=$(wc -l < "$TMP")
    GAME_BYTES=0
    if [ "$GAME_FILES" -gt 0 ]; then
        # du -bc / -A not available on BusyBox. Sum via stat per-file.
        GAME_BYTES=$(xargs -a "$TMP" stat -c '%s' 2>/dev/null | awk '{s+=$1} END{print s+0}')
    fi
    GAME_MB=$(( GAME_BYTES / 1048576 ))

    printf "  %-12s  orphan files: %7d   reclaim: %5d MB\n" "$GAME" "$GAME_FILES" "$GAME_MB"

    if [ "$APPLY" = "1" ] && [ "$GAME_FILES" -gt 0 ]; then
        # xargs -r so an empty list is a no-op. -0 unavailable on BusyBox
        # xargs; rely on the find above producing newline-safe paths (shard
        # tree filenames are hex digits — no whitespace possible).
        xargs -a "$TMP" rm -f 2>/dev/null
        echo -e "    ${G}[DELETED] $GAME_FILES files${N}"
    fi
    rm -f "$TMP"

    TOTAL_FILES=$(( TOTAL_FILES + GAME_FILES ))
    TOTAL_BYTES=$(( TOTAL_BYTES + GAME_BYTES ))
done

TOTAL_MB=$(( TOTAL_BYTES / 1048576 ))
echo ""
echo -e "${C}----------------------------------------------------------${N}"
printf "  TOTAL ORPHANS:  %d files,  ~%d MB\n" "$TOTAL_FILES" "$TOTAL_MB"
if [ "$APPLY" = "1" ]; then
    echo -e "  ${G}swept.${N}"
else
    echo -e "  ${Y}dry-run; re-run with --apply to delete.${N}"
fi
