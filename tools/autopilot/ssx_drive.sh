#!/bin/bash
# SSX (NPUB30892): title -> World Tour -> the pre-event cinematic (mainintro.vp6) by paced Pitlink taps,
# a frame saved at each checkpoint, then a WATCH of what the car does there. The instrument is the
# movie file's read position (/proc/<pid>/fdinfo on the car): 2026-10-26 it froze at 1,310,720 with all
# six SPU threads at 100 % and flips still at 30 fps (Hunt_SSX_Mountain_20261026.md). With fmv/ moved
# aside (--bypass) there is no movie: the route lands on the event load -> chopper -> drop instead.
#
# usage: tools/autopilot/ssx_drive.sh OUTDIR [--relaunch] [--bypass|--restore] [--arm] [--watch SECS]
#   --relaunch   exit a running game, launch SSX through the garage, wait for the title
#   --bypass     before the launch: move USRDIR/data/common/fmv to fmv.aside (--restore puts it back)
#   --arm        arm a hangrd capture on the car for this run (size-guarded at 1.5 GB; disarmed after)
#   --watch S    after the last tap: S seconds of flips / movie fd position / dmesg / log, a frame per 20 s
# Needs the held wheel (tools/pitlink/pitlink.py serve) or no other controller. RIG=root@SM8250.local (car8).
cd "$(dirname "$0")/../.." || exit 1
D="$1"; shift; [ -n "$D" ] || { echo "usage: $0 OUTDIR [...]"; exit 2; }
mkdir -p "$D"; T=tools/pitlink/pitlink.py; RIG="${RIG:-root@SM8250.local}"
FMV=/storage/roms/bios/rpcs3/dev_hdd0/game/NPUB30892/USRDIR/data/common/fmv
RELAUNCH=0; BYPASS=0; RESTORE=0; ARM=0; WATCH=0
while [ $# -gt 0 ]; do case "$1" in
  --relaunch) RELAUNCH=1;; --bypass) BYPASS=1;; --restore) RESTORE=1;; --arm) ARM=1;;
  --watch) WATCH="$2"; shift;; *) echo "unknown: $1"; exit 2;; esac; shift; done
rig(){ ssh -o ConnectTimeout=8 -o BatchMode=yes "$RIG" "$@"; }
say(){ echo "[$(date +%T)] $*" | tee -a "$D/log.txt"; }
w(){ local t0; t0=$(date +%s); until [ $(( $(date +%s) - t0 )) -ge "$1" ]; do sleep 1; done; }
p(){ python3 $T press "$1" >/dev/null 2>&1 || say "press $1 FAILED"; }
f(){ python3 $T frame "$D/$1.png" --max-w 640 >/dev/null 2>&1 || say "no frame at $1"; }
flips(){ python3 $T status 2>/dev/null | python3 -c 'import sys,json
try: d=json.load(sys.stdin); print(d["state"], d["flip"], (d["frame"] or {}).get("fps"))
except Exception: print("down")'; }
# the movie fd position on the car: "<file> pos:<bytes>" per open .vp6 (none = no movie open)
MOVIEPOS='pid=$(for d in /proc/[0-9]*; do tr "\0" " " 2>/dev/null < $d/cmdline | grep -q "AppRun.wrapped --no-gu[i]" && echo ${d#/proc/}; done | head -1); [ -n "$pid" ] || { echo "no game"; exit 0; }; for fd in /proc/$pid/fd/*; do t=$(readlink $fd 2>/dev/null); case "$t" in *.vp6) echo "${t##*/} $(grep pos /proc/$pid/fdinfo/${fd##*/} | tr -s "\t" " ")";; esac; done'
# spu/rsx host-thread CPU (top -H on the car: the 100 % SPU signature)
CPUSIG='top -bn1 -H 2>/dev/null | grep -E "SPU\[|rsx::" | awk "{print \$NF, \$9}" | sort | uniq -c | sort -rn | head -4 | tr "\n" ";"'

if [ "$RESTORE" = 1 ]; then rig "[ -d $FMV.aside ] && mv $FMV.aside $FMV; ls -d $FMV" && say "fmv/ restored"; fi
if [ "$RELAUNCH" = 1 ]; then
  say "relaunch: exit"; python3 $T exit >/dev/null 2>&1
  for i in $(seq 1 30); do sleep 2; python3 $T garage running 2>/dev/null | grep -q '"running": null' && break; done
  if [ "$BYPASS" = 1 ]; then rig "[ -d $FMV ] && mv $FMV $FMV.aside; ls -d $FMV.aside" && say "fmv/ moved aside (BYPASS)"; fi
  say "relaunch: garage launch"; python3 $T garage launch game=NPUB30892 >/dev/null 2>&1 || { say "launch FAILED"; exit 1; }
  for i in $(seq 1 40); do sleep 3; s=$(flips); case "$s" in running*) set -- $s; [ "$2" -gt 30 ] && break;; esac; done
  say "flipping: $(flips); waiting 60 s for the title"; w 60
fi
if [ "$ARM" = 1 ]; then
  rig 'mkdir -p /storage/ssx_cap; dmesg | wc -l > /storage/ssx_cap/dmesg_mark; wc -c < /storage/.cache/rpcs3/RPCS3.log > /storage/ssx_cap/log_mark
    nohup sh -c "cat /sys/kernel/debug/dri/0/hangrd > /storage/ssx_cap/hangrd_$(date +%s).rd" >/dev/null 2>&1 &
    nohup sh -c "while :; do for f in /storage/ssx_cap/*.rd; do s=\$(stat -c %s \$f 2>/dev/null || echo 0); [ \$s -gt 1500000000 ] && { for q in \$(pgrep -f \"[h]angrd\"); do kill \$q; done; echo capped \$f \$s >> /storage/ssx_cap/guard.log; }; done; sleep 5; done" >/dev/null 2>&1 &
    echo armed' | tail -1 | grep -q armed && say "hangrd ARMED (guard 1.5 GB)"
fi
mark_log(){ rig 'M=$(cat /storage/ssx_cap/log_mark 2>/dev/null || echo 0); L=/storage/.cache/rpcs3/RPCS3.log; tail -c +$((M+1)) $L | grep -aE "^·[EF] |frozen|ensure|texcache|GTK " | grep -av "DrmIsAvailable\|sys_fs_\|Shader Mode\|too sleepy" | tail -3 | cut -c1-200; M2=$(cat /storage/ssx_cap/dmesg_mark 2>/dev/null || echo 0); dmesg | tail -n +$((M2+1)) | grep -E "a6xx|hangcheck|keepalive" | tail -2 | cut -c1-160'; }

say "route: title -> World Tour movie"; f 0_title
p start; w 6; f 1_autosave
p cross; w 110; f 2_loading_online_dialog           # the online-features dialog sits on the loading screen by now
p cross; w 30; f 3_home                             # SSX HOME, World Tour highlighted
p cross; w 10; f 4_tutorial_1
p cross; w 7;  f 5_tutorial_2
p cross; w 8;  f 6_after_advance                    # the movie (dark field) -- or, bypassed, the event load
say "arrived: $(flips) | movie: $(rig "$MOVIEPOS" 2>/dev/null | tr '\n' ' ')"

if [ "$WATCH" != 0 ]; then
  t0=$(date +%s); n=0
  while [ $(( $(date +%s) - t0 )) -lt "$WATCH" ]; do
    w 20; n=$((n+1)); f "w$(printf %02d $n)"
    say "t+$(( $(date +%s) - t0 ))s flips: $(flips) | movie: $(rig "$MOVIEPOS" 2>/dev/null | tr '\n' ' ') | cpu: $(rig "$CPUSIG" 2>/dev/null)"
    mark_log | sed 's/^/    /' | tee -a "$D/log.txt"
  done
fi
if [ "$ARM" = 1 ]; then
  rig 'for q in $(pgrep -f "[h]angrd"); do kill $q; done; for q in $(pgrep -f "[s]sx_cap/\*.rd"); do kill $q; done; ls -la /storage/ssx_cap/ | grep "\.rd" | awk "{print \$5, \$9}"; cat /storage/ssx_cap/guard.log 2>/dev/null' | tee -a "$D/log.txt"
  say "hangrd disarmed (captures left under /storage/ssx_cap on the car)"
fi
say done
