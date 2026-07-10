#!/bin/bash
# grab_bog_sample.sh — ONE SWEEP: pull the newest bog-profiler sample set off the
# ROCKNIX rig and print its verdict-ready summary. The operator says "grab sample"
# after chording SELECT+DPAD-Down mid-race (bin/bog_profile.sh banks
# bog_<epoch>.{perf.data,stacks.gz,summary.txt,meta} on the rig, newest 6 kept).
#
# Usage: grab_bog_sample.sh [N]          # N = how many newest sets (default 1)
# Env:   COCKPIT_RIG_SSH  rig ssh target (default: $RIG_SSH or root@SM8250.local)
#        COCKPIT_OUT      local dir      (default: ./bog_samples)
#        GRAB_PERF_DATA=1 also pull the raw .perf.data (big; default: skip —
#                         stacks.gz is the symbolized full record)
# Prints: meta + the top of summary.txt (the perf report head = the named
#         hotspots/locks). stacks.gz lands locally for deep analysis
#         (zcat | flamegraph / manual read).
set -u
RIG="${COCKPIT_RIG_SSH:-${RIG_SSH:-root@SM8250.local}}"
OUT="${COCKPIT_OUT:-./bog_samples}"
N="${1:-1}"
RDIR=/storage/games-internal/roms/etk/etk_telemetry/perf_samples

SETS=$(ssh -o ConnectTimeout=8 "$RIG" "ls -1t $RDIR/bog_*.meta 2>/dev/null | head -$N" 2>/dev/null)
[ -z "$SETS" ] && { echo "no bog samples on $RIG ($RDIR) — chord R1+DPAD-Down mid-race first" >&2; exit 2; }

mkdir -p "$OUT"
for m in $SETS; do
  b="${m%.meta}"; name=$(basename "$b")
  d="$OUT/$name"; mkdir -p "$d"
  files="$b.meta $b.summary.txt $b.stacks.gz"
  [ "${GRAB_PERF_DATA:-0}" = "1" ] && files="$files $b.perf.data"
  scp -q -o ConnectTimeout=8 $(for f in $files; do echo "$RIG:$f"; done) "$d/" 2>/dev/null
  echo "=============================================================="
  echo "== $name  ->  $d"
  echo "-- meta --"; cat "$d/$name.meta" 2>/dev/null
  echo "-- summary (perf report head) --"
  sed -n '1,60p' "$d/$name.summary.txt" 2>/dev/null
done
echo "=============================================================="
echo "full stacks: $OUT/<set>/<set>.stacks.gz  (zcat for the complete symbolized record)"
