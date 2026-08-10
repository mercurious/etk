#!/usr/bin/env bash
# ==========================================================================
# tools/sync_game_configs.sh — refresh the repo's per-game tunes from the rig
# --------------------------------------------------------------------------
# WHAT THIS FIXES. `config/config_<ID>.yml` is the kit's lab notebook: the
# reference RPCS3 tune for every title the rig runs, and the only place a
# fresh clone (or a second rig, or a Paddock subscriber) can read what the
# field actually settled on. But the ONLY writer of a tune is the operator, in
# the Pitstop TUNING tab, ON THE RIG. install.sh has always pulled those tuned
# configs back to the host — into `state/custom_configs/`, which is gitignored
# Tier-B state. So the live tunes came home and stopped one directory short of
# the repo. At the 0.8.5 cut the notebook was frozen at 2026-07-24 while the
# rig had moved on: 24 of 41 titles had drifted, two of them (BCUS98114 and
# NPUA80075 — the GT5P US pair) by ~475 lines each. Published, that is a
# reference tune nobody is running.
#
# WHAT IT DOES. Copies `state/custom_configs/config_<ID>.yml` (the Tier-B
# mirror install.sh STEP 2 just pulled) over `config/config_<ID>.yml`, for
# every real PS3 title id. It never touches the rig, never runs git, and never
# invents a config: no mirror, nothing to say.
#
#   ./tools/sync_game_configs.sh            # apply (prints what moved)
#   ./tools/sync_game_configs.sh --check    # report drift only; rc=1 if any
#
# install.sh calls it (apply) at the end of STEP 2, so a deploy is also a
# notebook refresh; release_sanity.sh calls it (--check) so a cut cannot ship
# a stale notebook without saying so.
#
# TITLE-ID SHAPE IS THE FILTER, and skips are LOUD. A PS3 title id is four
# letters and five digits. The rig's config dir accumulates things that are
# not that — `config_IDLE.yml` (128 B, 2026-05-18: the id resolver once wrote
# its own IDLE sentinel as a game key) and `config_RXSTR3179.yml` (a
# golden-seed against a filename tag that never resolved to a serial). Those
# are not tunes and must not enter the notebook, but they are also not
# nothing: every skip is printed with its reason, because a silent filter is
# how a real title would go missing without anyone noticing.
# ==========================================================================
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${ETK_REPO_ROOT:-$(cd "$HERE/.." && pwd)}"
SRC_DIR="${ETK_CONFIG_MIRROR:-$REPO_ROOT/state/custom_configs}"
DST_DIR="$REPO_ROOT/config"

MODE=apply
for a in "$@"; do
    case "$a" in
        --check)   MODE=check ;;
        --apply)   MODE=apply ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) printf 'sync_game_configs: unknown argument: %s\n' "$a" >&2; exit 2 ;;
    esac
done

c_ok=''; c_new=''; c_warn=''; c_off=''
if [ -t 1 ]; then
    c_ok='\033[0;32m'; c_new='\033[0;36m'; c_warn='\033[1;33m'; c_off='\033[0m'
fi

if [ ! -d "$SRC_DIR" ]; then
    printf "${c_warn}[SKIP]${c_off} no rig config mirror at %s\n" "$SRC_DIR"
    printf "       run install.sh once (STEP 2 pulls it) and re-run.\n"
    exit 0
fi

UPDATED=0; ADDED=0; SAME=0; SKIPPED=0
DRIFT_LIST=""

for SRC in "$SRC_DIR"/config_*.yml; do
    [ -f "$SRC" ] || continue
    BASE=$(basename "$SRC")
    ID=${BASE#config_}; ID=${ID%.yml}

    # Real title ids only — see the shape note in the header. Loud skip.
    if ! printf '%s' "$ID" | grep -qE '^[A-Z]{4}[0-9]{5}$'; then
        printf "${c_warn}[skip]${c_off} %-12s not a PS3 title id (%s B) — not a tune\n" \
            "$ID" "$(wc -c < "$SRC" | tr -d ' ')"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # An empty or truncated mirror file is a failed pull, not a tune. Copying
    # it would destroy the good notebook entry with the bad one.
    if [ ! -s "$SRC" ]; then
        printf "${c_warn}[skip]${c_off} %-12s mirror file is empty\n" "$ID"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    DST="$DST_DIR/$BASE"
    if [ ! -f "$DST" ]; then
        DRIFT_LIST="$DRIFT_LIST $ID(new)"
        if [ "$MODE" = apply ]; then
            cp "$SRC" "$DST" && printf "${c_new}[add ]${c_off} %-12s new title\n" "$ID"
        else
            printf "${c_new}[new ]${c_off} %-12s not in config/ yet\n" "$ID"
        fi
        ADDED=$((ADDED + 1))
    elif cmp -s "$SRC" "$DST"; then
        SAME=$((SAME + 1))
    else
        N=$(diff "$DST" "$SRC" | grep -c '^[<>]')
        DRIFT_LIST="$DRIFT_LIST $ID($N)"
        if [ "$MODE" = apply ]; then
            cp "$SRC" "$DST" && printf "${c_ok}[sync]${c_off} %-12s %s changed line(s) from the rig\n" "$ID" "$N"
        else
            printf "${c_warn}[drift]${c_off} %-12s %s line(s) newer on the rig\n" "$ID" "$N"
        fi
        UPDATED=$((UPDATED + 1))
    fi
done

TOTAL=$((UPDATED + ADDED + SAME))
printf -- "-- game tunes: %d in sync, %d refreshed, %d added, %d skipped (of %d rig configs)\n" \
    "$SAME" "$UPDATED" "$ADDED" "$SKIPPED" "$((TOTAL + SKIPPED))"

if [ "$MODE" = check ] && [ $((UPDATED + ADDED)) -gt 0 ]; then
    printf "${c_warn}   stale notebook:%s${c_off}\n" "$DRIFT_LIST"
    printf "   run ./tools/sync_game_configs.sh and commit before cutting.\n"
    exit 1
fi
exit 0
