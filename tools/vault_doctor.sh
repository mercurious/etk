#!/bin/bash
# ==========================================================
# ETK VAULT DOCTOR — end-to-end shader-pipeline diagnostic
# ==========================================================
# Walks every link between MESA Turnip and the HUD "NEW" count
# and reports exactly where the chain breaks. Run ON THE RIG:
#   bash /storage/games-internal/roms/etk/scripts/vault_doctor.sh
# It does not modify anything. The live probe (section 7) asks
# you to trigger an in-game shader compile so we can see whether
# new shaders actually land in the vault vault_d.sh is watching.
# ==========================================================
source /storage/games-internal/roms/etk/scripts/env.sh

P(){ printf '  [PASS] %s\n' "$1"; }
F(){ printf '  [FAIL] %s\n' "$1"; }
W(){ printf '  [WARN] %s\n' "$1"; }
H(){ printf '\n=== %s ===\n' "$1"; }

H "1. ENV RESOLUTION"
echo "  TARGET_ID       = $TARGET_ID"
echo "  ID_FILE         = $ID_FILE  ->  $(cat "$ID_FILE" 2>/dev/null || echo '(missing)')"
echo "  VAULT_DIR       = $VAULT_DIR"
echo "  RPCS3_CACHE_DIR = $RPCS3_CACHE_DIR"
echo "  ETK_BUILD_TYPE  = $ETK_BUILD_TYPE"
[ "$ETK_BUILD_TYPE" = "FULL" ] && P "build tier FULL (vault_d.sh is supposed to run)" \
                               || W "build tier is $ETK_BUILD_TYPE — vault_d.sh only spawns on FULL"
case "$TARGET_ID" in
  ""|IDLE|UNKNOWN_ID) W "TARGET_ID is '$TARGET_ID' — vault path is not game-locked yet";;
  *) P "TARGET_ID resolved";;
esac

H "2. PROCESSES"
for proc in "01-etk-sentry.sh" "vault_d.sh" "mango_bridge.sh" "thermal_d.sh"; do
  if pgrep -af "$proc" >/dev/null 2>&1; then P "$proc running"; else F "$proc NOT running"; fi
done
if pgrep -af "rpcs3|AppRun.wrapped" >/dev/null 2>&1; then P "RPCS3 running"; else W "RPCS3 not running (start the game for the live probe)"; fi

H "3. CACHE -> VAULT SYMLINK"
if [ -L "$RPCS3_CACHE_DIR" ]; then
  TGT="$(readlink -f "$RPCS3_CACHE_DIR" 2>/dev/null)"
  echo "  symlink -> $TGT"
  if [ "$TGT" = "$VAULT_DIR" ]; then P "cache symlink points at the watched VAULT_DIR"
  else F "cache symlink points ELSEWHERE than VAULT_DIR ($VAULT_DIR) — counts will never move"; fi
elif [ -d "$RPCS3_CACHE_DIR" ]; then
  F "$RPCS3_CACHE_DIR is a REAL directory, not a symlink — Turnip writes here, vault_d never sees it"
elif [ -e "$RPCS3_CACHE_DIR" ]; then
  F "$RPCS3_CACHE_DIR exists but is neither symlink nor dir"
else
  F "$RPCS3_CACHE_DIR does not exist — symlink was never created"
fi

H "4. WHERE DOES TURNIP ACTUALLY WRITE?"
RP=$(pgrep -f rpcs3 | head -n1)
if [ -n "$RP" ]; then
  ENVV=$(tr '\0' '\n' < /proc/$RP/environ 2>/dev/null)
  for k in MESA_SHADER_CACHE_DIR MESA_GLSL_CACHE_DIR MESA_SHADER_CACHE_DISABLE XDG_CACHE_HOME HOME; do
    v=$(echo "$ENVV" | grep "^${k}=" | head -n1)
    [ -n "$v" ] && echo "  $v"
  done
  echo "$ENVV" | grep -q '^MESA_SHADER_CACHE_DISABLE=1' && F "MESA_SHADER_CACHE_DISABLE=1 — Turnip disk cache is OFF; nothing will ever be harvested" || P "Turnip disk cache not explicitly disabled"
  MD=$(echo "$ENVV" | grep '^MESA_SHADER_CACHE_DIR=' | cut -d= -f2-)
  if [ -n "$MD" ] && [ "$MD" != "$RPCS3_CACHE_DIR" ]; then
    F "RPCS3 env forces MESA_SHADER_CACHE_DIR=$MD — our symlink at $RPCS3_CACHE_DIR is BYPASSED"
  fi
else
  W "RPCS3 not running — cannot inspect its environment"
fi
echo "  -- full cache-relevant environ of EVERY rpcs3/AppImage process --"
for pid in $(pgrep -f 'rpcs3|AppRun|AppImage' 2>/dev/null); do
  nm=$(cat /proc/$pid/comm 2>/dev/null)
  echo "  [pid $pid : $nm]  cwd=$(readlink /proc/$pid/cwd 2>/dev/null)"
  tr '\0' '\n' < /proc/$pid/environ 2>/dev/null \
    | grep -iE '^(HOME|XDG_CACHE_HOME|XDG_DATA_HOME|MESA_|RADV_|TU_|TMPDIR|APPDIR|APPIMAGE|OWD)=' \
    | sed 's/^/      /'
  echo "    open shader/cache fds:"
  ls -l /proc/$pid/fd 2>/dev/null | grep -iE 'cache|shader|\.foz|\.bin|\.tmp' | sed 's/^/      /' | head -n 8
done
echo "  -- single-file (Fossilize) caches anywhere under /storage/.cache, /tmp, /run --"
for r in /storage/.cache /tmp /run /var/cache; do
  find "$r" -maxdepth 4 \( -name '*.foz' -o -name 'mesa_shader_cache*' \) 2>/dev/null \
    | while read -r p; do echo "    $p  ($(du -sh "$p" 2>/dev/null | awk '{print $1}'))"; done
done
echo "  -- every real 'mesa_shader_cache' dir, all mounts --"
find / -xdev -maxdepth 7 -type d -name 'mesa_shader_cache' 2>/dev/null | sed 's/^/    /'
for mp in /tmp /run /var; do
  find "$mp" -xdev -maxdepth 7 -type d -name 'mesa_shader_cache' 2>/dev/null | sed 's/^/    /'
done

H "5. SHM LIVE STATE"
for f in vault_count vault_new.txt vault_size.txt; do
  printf '  %-16s = %s\n' "$f" "$(cat "$SHM_DIR/$f" 2>/dev/null || echo '(missing)')"
done
echo "  live_stat       = $(cat "$LIVE_STAT" 2>/dev/null || echo '(missing)')"
c1=$(cat "$SHM_DIR/vault_count" 2>/dev/null); sleep 3; c2=$(cat "$SHM_DIR/vault_count" 2>/dev/null)
echo "  vault_count 3s apart: $c1 -> $c2"

H "6. SYMLINK STATE / SENTRY SPY LOG"
# The spy log is truncated (echo > log) on every sentry restart, so a
# missing CACHE line is NOT proof the link is wrong. Judge by reality.
LK="$(readlink -f "$RPCS3_CACHE_DIR" 2>/dev/null)"
if [ "$LK" = "$VAULT_DIR" ]; then
  P "cache symlink resolves to VAULT_DIR — link is correct (independent of log)"
elif [ -n "$LK" ] && [ "$TARGET_ID" != "IDLE" ]; then
  W "symlink resolves to $LK (mismatch only expected while idle/between games)"
elif [ -z "$LK" ]; then
  F "cache path resolves nowhere — symlink missing"
else
  echo "  symlink -> $LK (TARGET_ID idle; pinned to last game, normal)"
fi
tail -n 6 "$TRIPWIRE_LOG" 2>/dev/null
grep -qE 'CACHE LINKED|CACHE DIR MOVED' "$TRIPWIRE_LOG" 2>/dev/null \
  && echo "  (sentry logged the link this boot)" \
  || echo "  (no CACHE log this boot: old sentry or log rotated — not a fault if link is correct above)"

H "7. GLOBAL WRITE TRACE (definitive: finds the REAL write location)"
RESOLVED="$(readlink -f "$RPCS3_CACHE_DIR" 2>/dev/null || echo "$RPCS3_CACHE_DIR")"
MARK="/tmp/etk_doctor_mark.$$"
: > "$MARK"; sleep 1
vf0=$(find "$VAULT_DIR" -type f 2>/dev/null | wc -l)
vc0=$(cat "$SHM_DIR/vault_count" 2>/dev/null || echo 0)
SESS_NEW=$(cat "$SHM_DIR/vault_new.txt" 2>/dev/null || echo 0)
echo "  session harvest so far (vault_new.txt) = ${SESS_NEW}   (absolute count=${vc0})"
echo "  marker dropped: $MARK   (VAULT_DIR baseline files=$vf0)"
echo "  >>> NOW: in the game, drive to a NEW area / pick a NEW car so"
echo "      the shader spinner runs hard. Capturing 30s..."
sleep 30
vf1=$(find "$VAULT_DIR" -type f 2>/dev/null | wc -l)
vc1=$(cat "$SHM_DIR/vault_count" 2>/dev/null || echo 0)
echo "  VAULT_DIR after: files=$vf1  (window delta=$((vf1-vf0)), count $vc0->$vc1)"
echo "  -- ALL files written since the marker, every non-virtual mount --"
WROTE="/tmp/etk_doctor_wrote.$$"; : > "$WROTE"
# Enumerate real mountpoints from /proc/mounts, skip kernel-virtual fs.
awk '$3!~/^(proc|sysfs|cgroup|cgroup2|devpts|debugfs|tracefs|securityfs|pstore|bpf|autofs|configfs|fusectl|mqueue|rpc_pipefs|binfmt_misc)$/{print $2}' /proc/mounts \
  | sort -u | while read -r mp; do
  find "$mp" -xdev -newer "$MARK" -type f 2>/dev/null \
    | grep -ivE '/(proc|sys)/' >> "$WROTE"
done
sort -u "$WROTE" > "${WROTE}.u" && mv "${WROTE}.u" "$WROTE"
total=$(wc -l < "$WROTE")
echo "  total files written during capture: $total"
echo "  -- shader/cache-looking writes (path or extension) --"
grep -iE 'shader|mesa|\.foz|fossilize|pipeline|spirv|/cache/|rpcs3.*cache' "$WROTE" \
  | sed 's/^/    /' | head -n 40
echo "  -- write hot-dirs (top 12 dirs by new-file count) --"
sed 's#/[^/]*$##' "$WROTE" | sort | uniq -c | sort -rn | head -n 12 | sed 's/^/    /'
rm -f "$MARK" "$WROTE"
echo
NOW_NEW=$(cat "$SHM_DIR/vault_new.txt" 2>/dev/null || echo 0)
if [ "$vf1" -gt "$vf0" ] || [ "$vc1" -gt "$vc0" ]; then
  P "shaders landed during capture (VAULT_DIR +$((vf1-vf0)), count $vc0->$vc1) — pipeline LIVE"
elif [ "${NOW_NEW:-0}" -gt 0 ]; then
  P "pipeline WORKS: session has harvested ${NOW_NEW} new shaders this run (vault_new.txt)."
  echo "       No compile in THIS 30s window is normal — Turnip only writes a cache"
  echo "       entry for a genuinely-new pipeline, and a mature 41k vault is mostly"
  echo "       pre-cached. Sparse increments != broken. The HUD NEW count is accurate."
else
  F "VAULT_DIR did not grow AND session NEW=0. Real write location (if any) is in the hot-dirs above."
  echo "       -> 'mesa_shader_cache' / '*.foz' under /tmp,/run,AppImage mount != $RESOLVED"
  echo "          means the build redirected Turnip there. Fix = repoint that path."
  echo "       -> Only RPCS3's OWN cache (…/rpcs3*/cache/…NPUA80075…) growing with no"
  echo "          mesa/foz files = the spinner is RPCS3's program cache; Mesa driver"
  echo "          cache genuinely isn't being exercised (different harvest target)."
fi
echo
echo "=== DOCTOR COMPLETE — paste this whole output back ==="
