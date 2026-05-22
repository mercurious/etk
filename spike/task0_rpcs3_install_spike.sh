#!/bin/sh
# ============================================================================
# Task 0 feasibility spike -- RPCS3 headless PKG install characterization.
# DISPOSABLE. Not part of ETK. Run on the rig, paste the full output back.
#
#   sh task0_rpcs3_install_spike.sh [path-to-pkg-or-dir] 2>&1 | tee /tmp/task0.log
#
# Default search path: /storage/games-internal/roms/temp  (GT5 TT Challenge)
# Override the binary with:  RPCS3_BIN=/path/to/rpcs3 sh task0_...sh
#
# NOTE: this launches RPCS3, which trips the Sentry -> one spurious ABORTED
# ledger row + brief daemon spawn. Expected for a one-off spike; ignore it.
# ============================================================================

PKG_ROOT="${1:-/storage/games-internal/roms/temp}"
DEV_HDD0="/storage/games-internal/roms/bios/rpcs3/dev_hdd0"
GAME_DIR="$DEV_HDD0/game"
EXDATA_DIR="$DEV_HDD0/home/00000001/exdata"
RPCS3_LOG="/storage/.cache/rpcs3/RPCS3.log"
INSTALL_TIMEOUT=240

line() { echo "------------------------------------------------------------"; }
sec()  { echo; line; echo "=== $1"; line; }

# ---------------------------------------------------------------------------
sec "[0] ENVIRONMENT"
echo "date          : $(date 2>/dev/null)"
echo "uname         : $(uname -a 2>/dev/null)"
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
echo "DISPLAY       =$DISPLAY"
echo "PKG_ROOT      : $PKG_ROOT"

# ---------------------------------------------------------------------------
sec "[1] RPCS3 BINARY DISCOVERY  (dossier Q1)"
echo "-- command -v --"
for c in rpcs3 rpcs3-sa; do
  p=$(command -v "$c" 2>/dev/null); [ -n "$p" ] && echo "  $c -> $p"
done
echo "-- find rpcs3* / AppRun.wrapped under /usr /storage --"
find /usr /storage -maxdepth 7 \( -name 'rpcs3*' -o -name 'AppRun.wrapped' \) 2>/dev/null

# ---------------------------------------------------------------------------
sec "[2] HOW ROCKNIX LAUNCHES RPCS3  (dossier Q1 -- the real argv)"
echo "-- files under /usr/bin mentioning rpcs3 --"
grep -rls "rpcs3" /usr/bin 2>/dev/null
echo "-- candidate runner scripts (dumped) --"
for f in /usr/bin/start_rpcs3.sh /usr/bin/runemu.sh; do
  [ -f "$f" ] && { echo ">>> $f"; grep -n -i "rpcs3\|installpkg\|QT_QPA\|WAYLAND" "$f" 2>/dev/null; }
done

# ---------------------------------------------------------------------------
# Resolve the binary to test.
RPCS3="${RPCS3_BIN:-}"
if [ -z "$RPCS3" ]; then
  for cand in "$(command -v rpcs3-sa 2>/dev/null)" "$(command -v rpcs3 2>/dev/null)" \
              $(find /usr /storage -maxdepth 7 -name 'rpcs3-sa' 2>/dev/null) \
              $(find /usr /storage -maxdepth 7 -name 'AppRun.wrapped' 2>/dev/null); do
    [ -n "$cand" ] && [ -x "$cand" ] && { RPCS3="$cand"; break; }
  done
fi
echo
echo ">>> RESOLVED RPCS3 BINARY: ${RPCS3:-<NONE FOUND>}"
[ -z "$RPCS3" ] && { echo "STOP: no binary found. Set RPCS3_BIN= and re-run."; exit 1; }

# ---------------------------------------------------------------------------
sec "[3] RPCS3 VERSION + CLI FLAGS  (dossier Q2/Q3 -- does --installpkg / --no-gui exist?)"
"$RPCS3" --version 2>&1 | head -n 3
echo "-- grep of --help for relevant flags --"
"$RPCS3" --help 2>&1 | grep -i -E 'installpkg|installfw|no-gui|headless|gui' || echo "  (no matching flags -- dump full --help below)"
echo "-- full --help --"
"$RPCS3" --help 2>&1 | head -n 60

# ---------------------------------------------------------------------------
sec "[4] STAGING SCAN"
echo "-- .pkg files under $PKG_ROOT --"
PKG=$(find "$PKG_ROOT" -type f \( -name '*.pkg' -o -name '*.PKG' \) 2>/dev/null | head -n 1)
find "$PKG_ROOT" -type f \( -name '*.pkg' -o -name '*.PKG' \) 2>/dev/null
echo "-- .rap files under $PKG_ROOT --"
find "$PKG_ROOT" -type f \( -name '*.rap' -o -name '*.RAP' \) 2>/dev/null
echo ">>> PKG selected for install attempt: ${PKG:-<NONE>}"
[ -z "$PKG" ] && { echo "STOP: no .pkg found under $PKG_ROOT"; exit 1; }
echo "pkg size: $(ls -l "$PKG" 2>/dev/null | awk '{print $5}') bytes"

# ---------------------------------------------------------------------------
sec "[5] HEADLESS INSTALL ATTEMPT  (QT_QPA_PLATFORM=offscreen)  (dossier Q2/Q5)"
[ -d "$GAME_DIR" ] && ls -1 "$GAME_DIR" 2>/dev/null | sort > /tmp/task0_game_before.txt || : > /tmp/task0_game_before.txt
echo "game/ dir entries BEFORE: $(wc -l < /tmp/task0_game_before.txt)"
echo
echo ">>> EXEC: env QT_QPA_PLATFORM=offscreen $RPCS3 --installpkg $PKG"
echo ">>> (timeout ${INSTALL_TIMEOUT}s -- a timeout/kill means it hung, likely needs a GUI)"
echo "----- begin install stdout/stderr -----"
START=$(date +%s 2>/dev/null)
env QT_QPA_PLATFORM=offscreen "$RPCS3" --installpkg "$PKG" 2>&1 &
IPID=$!
( sleep "$INSTALL_TIMEOUT"; kill -0 "$IPID" 2>/dev/null && { echo "[spike] TIMEOUT -- killing $IPID"; kill -TERM "$IPID" 2>/dev/null; sleep 3; kill -KILL "$IPID" 2>/dev/null; } ) &
WPID=$!
wait "$IPID"; RC=$?
kill "$WPID" 2>/dev/null
END=$(date +%s 2>/dev/null)
echo "----- end install stdout/stderr -----"
echo
echo ">>> INSTALL EXIT CODE : $RC   (0=clean exit; 124/137/143=timed out/killed = HUNG)"
echo ">>> WALL TIME         : $((END - START))s"

# ---------------------------------------------------------------------------
sec "[6] DISK DIFF  (dossier Q5 -- where did files land? what is the TITLE_ID?)"
[ -d "$GAME_DIR" ] && ls -1 "$GAME_DIR" 2>/dev/null | sort > /tmp/task0_game_after.txt || : > /tmp/task0_game_after.txt
echo "game/ dir entries AFTER : $(wc -l < /tmp/task0_game_after.txt)"
echo "-- NEW entries under game/ (the install target / TITLE_ID) --"
comm -13 /tmp/task0_game_before.txt /tmp/task0_game_after.txt
NEWID=$(comm -13 /tmp/task0_game_before.txt /tmp/task0_game_after.txt | head -n 1)
if [ -n "$NEWID" ]; then
  echo
  echo ">>> NEW TITLE DIR: $GAME_DIR/$NEWID"
  ls -la "$GAME_DIR/$NEWID" 2>/dev/null | head -n 20
  echo "-- PARAM.SFO present? (dossier Q5 -- source of human title) --"
  if [ -f "$GAME_DIR/$NEWID/PARAM.SFO" ]; then
    echo "  YES: $GAME_DIR/$NEWID/PARAM.SFO"
    echo "  -- strings TITLE-ish --"
    strings "$GAME_DIR/$NEWID/PARAM.SFO" 2>/dev/null | head -n 30
  else
    echo "  NO PARAM.SFO at expected path. Searching subtree:"
    find "$GAME_DIR/$NEWID" -iname 'PARAM.SFO' 2>/dev/null
  fi
else
  echo ">>> NO new game/ dir -- install may have failed, OR this PKG is an"
  echo "    update/DLC (merges into an existing dir). Check stdout + log above."
fi

# ---------------------------------------------------------------------------
sec "[7] RPCS3 LOG TAIL  (install progress lines -- feeds the progress bar)"
echo "log path: $RPCS3_LOG"
if [ -f "$RPCS3_LOG" ]; then
  echo "-- lines mentioning install/pkg/progress --"
  strings "$RPCS3_LOG" 2>/dev/null | grep -i -E 'install|\.pkg|progress|%|extract' | tail -n 40
  echo "-- last 25 log lines --"
  strings "$RPCS3_LOG" 2>/dev/null | tail -n 25
else
  echo "  (no RPCS3.log found)"
fi

sec "[8] EXDATA / RAP TARGET  (dossier Q4)"
echo "exdata dir: $EXDATA_DIR"
[ -d "$EXDATA_DIR" ] && ls -la "$EXDATA_DIR" 2>/dev/null || echo "  (exdata dir does not exist yet)"

sec "SPIKE COMPLETE -- paste this entire output back."
