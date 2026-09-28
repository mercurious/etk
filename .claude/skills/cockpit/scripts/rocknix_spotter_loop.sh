#!/bin/bash
# ETK Cockpit — ROCKNIX live Spotter (T1), runs ON the rig. Args: DURATION_SEC INTERVAL_SEC [STALE_TICKS]
# Telemetry over standard Linux sysfs (same SM8250). Lean on thermal + frametime, NOT GPU%
# (drm/msm has no direct busy node) — infer CPU-bound vs GPU-bound from GPU-freq + CPU-freq.
#
# CRASH-WATCH (auto-break + capture — ARM AT IDLE *BEFORE* LAUNCH, then walk away):
#   1. a6xx GPU FAULT — the dominant GT5P hang. dmesg `a6xx_irq … gpu fault`. It is DMESG-ONLY:
#      leaves NO core and NO RPCS3.log fatal, so the old core/log-only watch MISSED it entirely
#      (the whole reason a live multi-crash stint got hand-watched on 2026-06-18). This is now the
#      PRIMARY detector; the script marks dmesg at arm and breaks on any fault newer than the mark.
#   2. SILENT freeze / process death — gated on /dev/shm/etk_shm/live_stat.txt FRESHNESS, NOT pgrep.
#      The mango bridge rewrites live_stat ~1 Hz while in-game; `pgrep -x AppRun.wrapped` is
#      UNRELIABLE on this build (observed seen=0 for an entire live run), so DO NOT gate on it —
#      a frozen/stopped live_stat (unchanged for STALE_TICKS polls after the real DDU appeared) is
#      the trustworthy "no longer rendering" signal and catches the silent class the a6xx watch can't.
#   3. (kept) new /storage/cores dump OR RPCS3.log fatal — the SPU/segfault silent class (gdb-able).
#   4. EXIT-HANG — RPCS3 writes its whole graceful shutdown ("gui_application: Deleting old game
#      window" is the last line of every clean exit, on every core, once per process — never at the
#      .pkg EBOOT->EMAIN respawn) and then the process never leaves. Cores 0.9.0.x: gone 0.5-4.6 s
#      after that line (N=30 archived exits). Core 0.9.1: 5 of 6 exits hung 9 s .. 74 min, R3 the
#      only way out (2026-09-27; GT5P, GT5P disc, GT HD). SILENT would misfile it as a freeze and
#      the log has nothing more to say, so time it from that line; past EXITHANG_SECS with the
#      process still alive, capture every thread (state/wchan/cpu, kernel stack, gdb bt — the
#      AppImage mount dies with the process, so symbols resolve only now), watch up to
#      EXITHANG_WAIT for a self-exit (a timeout has a number), then toast the operator to R3.
#      A clean exit prints its exit latency instead — the number the 0.9.0.x baseline is in.
# On any trigger: capture a6xx status + grim frame + proc state + ledger tail, print, break.
# The caller (run in background) is notified on exit. Classification: ADRENO when a fault is in
# dmesg, SILENT when live_stat froze with no fault, EXIT-HANG when it froze after the shutdown log.
#
# RESCUE_BREAK=0 keeps watching through GPU hangs the keepalive absorbs (each one is printed with
# its wall clock) instead of breaking on the first — for hunting what happens LATER in a session
# on a title that rescues routinely mid-race (GT5P: 1-9 per session). hangrd defaults OFF then:
# an armed cat appends a crashstate per re-hang (5.26 GB on one storm) and a later catch wants
# the stacks, not the redump. Set HANGRD_CAPTURE=1 to keep it.
DUR="${1:-1800}"; INT="${2:-5}"; STALE_TICKS="${3:-4}"
RESCUE_BREAK="${RESCUE_BREAK:-1}"
[ "$RESCUE_BREAK" = 0 ] && HANGRD_CAPTURE="${HANGRD_CAPTURE:-0}"
EXITHANG_SECS="${EXITHANG_SECS:-10}"; EXITHANG_WAIT="${EXITHANG_WAIT:-90}"; EXITHANG_GDB="${EXITHANG_GDB:-1}"
EXITHANG_RESAMPLE="${EXITHANG_RESAMPLE:-10}"   # seconds between the two thread tables
EXIT_LINE='gui_application: Deleting old game window'
NOTIFY="${ETK_ROOT:-/storage/games-internal/roms/etk}/bin/etk_notify.sh"
LOG=$(ls -t /storage/*/.cache/rpcs3/RPCS3.log /storage/.cache/rpcs3/RPCS3.log 2>/dev/null | head -1)
# Cores land where the kernel's core_pattern says: ETK routes them to the game card
# ($ETK_ROOT/cores, 02-etk-coredump.sh). A fixed /storage/cores here went blind when they moved.
# Detection keys on the NEWEST file changing (the per-crash prune keeps the count flat at 2).
cp=$(cat /proc/sys/kernel/core_pattern 2>/dev/null)
case "$cp" in /*) CORES=${cp%/*};; *) CORES=/storage/cores;; esac
base_core=$(ls -t "$CORES" 2>/dev/null | head -1)
R3F=/dev/shm/etk_shm/r3_pressed.txt   # recovery.sh stamps the epoch of every L1+R3
SHM=/dev/shm/etk_shm/live_stat.txt
MARK=$(awk '{print int($1)}' /proc/uptime)
zt(){ awk '{printf "%.0f",$1/1000}' /sys/class/thermal/thermal_zone"$1"/temp 2>/dev/null; }  # m°C->°C
# in-game = a real live DDU. Exclude pre-game (WAIT/IDLE/LOADING) and the startup banner (SHDRS),
# so stale-counting only begins once steady telemetry is flowing (no false trip on the banner hold).
ingame(){ case "$1" in ""|*WAIT*|*IDLE*|*LOADING*|*SHDRS*) return 1;; *) return 0;; esac; }
# emulator-alive probe (gates the SILENT class): a real silent freeze has the emulator process
# STILL ALIVE with a frozen live_stat; a graceful exit/abort has it GONE (but live_stat lingers at
# its last value). cmdline-verified /proc walk — NOT `pgrep -f` (self-matches this wrapper shell,
# TRACK_MANUAL §Q, AppRun.wrapped) and NOT `pgrep -x AppRun.wrapped` (observed seen=0 for a whole live
# run on this build). cmdline is NUL-separated; read it per /proc PROC-discovery law. Skip self ($$).
emu_pid(){
  for c in /proc/[0-9]*/cmdline; do
    [ "${c%/cmdline}" = "/proc/$$" ] && continue
    exe=$(tr '\0' '\n' < "$c" 2>/dev/null | head -1)   # argv[0] = the executable, not an arg-mention
    case "$exe" in *AppRun.wrapped|*EBOOT.BIN|*EMAIN.SELF) p=${c%/cmdline}; echo "${p#/proc/}"; return 0;; esac
  done
  return 1
}
emu_alive(){ emu_pid >/dev/null; }
up(){ awk '{print int($1)}' /proc/uptime; }
# tid state wchan cpu-ticks(utime+stime) comm. stat's comm field can hold spaces; everything after
# its LAST ") " is fixed-position (state = 1st, utime/stime = 12th/13th).
thread_table(){
  for t in /proc/"$1"/task/*; do
    s=$(sed 's/^.*) //' "$t/stat" 2>/dev/null)
    printf '%s\t%s\t%s\t%s\t%s\n' "${t##*/}" "$(echo "$s" | cut -d' ' -f1)" "$(cat "$t/wchan" 2>/dev/null)" \
      "$(echo "$s" | awk '{print $12+$13}')" "$(cat "$t/comm" 2>/dev/null)"
  done
}
# EXIT-HANG capture. Non-intrusive reads first (thread table + kernel stacks), then gdb — attach-stop
# is harmless on a process that is already stuck. A second table EXITHANG_RESAMPLE s later tells STUCK (cpu flat)
# from SLOW (cpu moving) and names any spinner.
exithang_capture(){
  pid=$(emu_pid) || { echo "[spotter] EXIT-HANG: the process left before the capture started"; return; }
  d="$FDIR/exithang_$(date +%Y%m%d_%H%M%S)"; mkdir -p "$d"
  thread_table "$pid" > "$d/threads_a.tsv"
  for t in /proc/"$pid"/task/*; do echo "### ${t##*/} $(cat "$t/comm" 2>/dev/null)"; cat "$t/stack" 2>/dev/null; done > "$d/kstacks.txt"
  tr '\0' '\n' < /proc/"$pid"/environ 2>/dev/null > "$d/environ.txt"
  if grep -q vfs_coredump "$d/kstacks.txt"; then
    echo "[spotter]   the kernel is writing a core dump (vfs_coredump) — gdb cannot attach; the core is the evidence"
  elif [ "$EXITHANG_GDB" = 1 ] && command -v gdb >/dev/null 2>&1; then
    timeout 90 gdb -p "$pid" -batch -ex 'set pagination off' -ex 'thread apply all bt' > "$d/gdb_bt.txt" 2>&1
  fi
  sleep "$EXITHANG_RESAMPLE"
  kill -0 "$pid" 2>/dev/null && thread_table "$pid" > "$d/threads_b.tsv"
  tail -c 30000 "$LOG" > "$d/rpcs3_log_tail.txt" 2>/dev/null
  dmesg | tail -80 > "$d/dmesg_tail.txt" 2>/dev/null
  echo "[spotter] EXIT-HANG capture -> $d"
  echo "[spotter]   $(wc -l < "$d/threads_a.tsv") threads, states: $(awk -F'\t' '{n[$2]++} END{for(s in n) printf "%s:%d ", s, n[s]}' "$d/threads_a.tsv")"
  awk -F'\t' '$2!="S"' "$d/threads_a.tsv" | head -12 | sed 's/^/[spotter]   not-sleeping: /'
  [ -f "$d/threads_b.tsv" ] && awk -F'\t' 'NR==FNR{a[$1]=$4; next} ($1 in a) && $4>a[$1] {printf "%d\t%s\t%s\t%s\n", $4-a[$1], $1, $5, $3}' \
    "$d/threads_a.tsv" "$d/threads_b.tsv" | sort -rn | head -8 | sed "s/^/[spotter]   cpu ticks in ${EXITHANG_RESAMPLE}s: /"
  # the main thread (tid == pid) is the one that has to return from main() for the process to leave
  echo "[spotter]   main thread: $(awk -F'\t' -v p="$pid" '$1==p' "$d/threads_a.tsv")"
  awk -v p="### $pid " 'index($0,p)==1{f=1;next} /^###/{f=0} f' "$d/kstacks.txt" | head -8 | sed 's/^/[spotter]     k: /'
  [ -s "$d/gdb_bt.txt" ] && awk '/^Thread 1 /{f=1} f&&/^$/{exit} f' "$d/gdb_bt.txt" | head -24 | sed 's/^/[spotter]     g: /'
  echo "$d" > "$FDIR/.last_exithang"
}
# --- hangrd forensic capture (gpu_watch fold-in): cat the debugfs node so the kernel emits the
#     freedreno REDUMP (.rd) of the HANGING submit the instant the GPU wedges. Arm AT IDLE, here,
#     before launch. Decode off-rig with cffdump; the .faultinfo sidecar (written on catch) carries
#     the dmesg ib1/fence/status so the decode lands on the FAULTING draw, not a 1300-draw haystack.
HANGRD_CAPTURE="${HANGRD_CAPTURE:-1}"; HANGRD_NODE=/sys/kernel/debug/dri/0/hangrd
FDIR="${FORENSIC_DIR:-/storage/games-internal/roms/etk/etk_telemetry/crash_forensics}"
RDFILE=""; HANGRD_PID=""
if [ "$HANGRD_CAPTURE" = 1 ] && [ -e "$HANGRD_NODE" ]; then
  mkdir -p "$FDIR"
  RDFILE="$FDIR/hangrd_$(date +%Y%m%d_%H%M%S).rd"
  cat "$HANGRD_NODE" > "$RDFILE" 2>/dev/null &
  HANGRD_PID=$!
  echo "$RDFILE" > "$FDIR/.armed_rd"
  echo "[spotter] hangrd ARMED -> $RDFILE (pid $HANGRD_PID); /storage free $(df -m /storage 2>/dev/null | awk 'NR==2{print $4}')MB"
elif [ "$HANGRD_CAPTURE" = 1 ]; then
  echo "[spotter] WARN: hangrd node absent ($HANGRD_NODE) — no .rd capture this run"
fi
echo "[spotter] armed @ uptime ${MARK}s — a6xx-fault$([ "$RESCUE_BREAK" = 0 ] && echo ' (logged, no break)') + live_stat-stale(${STALE_TICKS}x) + core/log + exit-hang(${EXITHANG_SECS}s). dur=${DUR}s int=${INT}s. LAUNCH NOW."
endt=$(( MARK + DUR )); seen=0; prev=""; stale=0; crash=""; graceful=""; exit_at=""; hangs=0
lprev=$(wc -c < "$LOG" 2>/dev/null || echo 0)   # log-fatal delta baseline: don't rescan pre-arm history
# Arm-time live_stat snapshot: a PREVIOUS session's frozen/lingering DDU string looks in-game and
# would seed seen=1 immediately, then fire a phantom SILENT four unchanged ticks later (bit three
# arms in a row on the SSX campaign, 2026-08-11 — armed over the corpse of the prior wedge each
# time). Nothing counts until the string has MOVED past this snapshot: fresh telemetry from a real
# launch always changes it within a tick or two.
ARMVAL=$(cat "$SHM" 2>/dev/null)
while [ "$(up)" -lt "$endt" ]; do
  now=$(up)
  pid=$(pgrep -f rpcs3 | head -1)   # RSS display only (best-effort; do NOT use for liveness)
  rss=$(awk '/VmRSS/{printf "%d",$2/1024}' /proc/"$pid"/status 2>/dev/null)
  gpuf=$(awk '{printf "%d",$1/1000000}' /sys/class/devfreq/3d00000.gpu/cur_freq 2>/dev/null)   # MHz (305-800)
  cpf=$(awk '{printf "%d",$1/1000}' /sys/devices/system/cpu/cpufreq/policy7/scaling_cur_freq 2>/dev/null) # MHz prime
  free=$(awk '/MemAvailable/{printf "%d",$2/1024}' /proc/meminfo)
  etk=$(cat "$SHM" 2>/dev/null)
  # 1. a6xx GPU fault newer than arm-mark
  fl=$(dmesg | grep -E "a6xx_irq.*gpu fault ring" | tail -1)
  fts=$(echo "$fl" | sed -n 's/^\[ *\([0-9]*\).*/\1/p')
  [ "$RESCUE_BREAK" = 1 ] && [ -n "$fts" ] && [ "$fts" -gt "$MARK" ] && crash="ADRENO status=$(echo "$fl" | sed -n 's/.*status \([0-9A-Fa-f]*\).*/\1/p')"
  # 1b. NO-FAULT forward-progress stall (SSX NPUB30892 class, 2026-08-11): hangcheck lockup with
  #     NO a6xx fault line — keepalive absorbs it (the game may keep limping, live_stat stays
  #     fresh, so the SILENT gate never trips either), but hangrd has already fired on the first
  #     recover, so break and finalize the capture. The faultinfo sidecar will be SPARSE for this
  #     class (hangcheck lines carry completed/submitted fence only, no ib1/ib2) — decode is
  #     structural; the .rd itself still holds the parked submit.
  if [ -z "$crash" ] && [ "$RESCUE_BREAK" = 1 ]; then
    hl=$(dmesg | grep -E "hangcheck detected gpu lockup" | tail -1)
    hts=$(echo "$hl" | sed -n 's/^\[ *\([0-9]*\).*/\1/p')
    if [ -n "$hts" ] && [ "$hts" -gt "$MARK" ]; then crash="ADRENO-NOFAULT (hangcheck, keepalive-absorbed)"; fl="$hl"; fi
  fi
  # 1c. RESCUE_BREAK=0: log each new GPU hang (fault or no-fault lockup) with its wall clock, keep going.
  if [ "$RESCUE_BREAK" = 0 ]; then
    hl=$(dmesg | grep -E "a6xx_irq.*gpu fault ring|hangcheck detected gpu lockup")
    n=$(echo "$hl" | awk -v m="$MARK" 'match($0, /^\[ *[0-9]+/) { t = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", t); if (t + 0 > m) c++ } END { print c + 0 }')
    if [ "$n" -gt "$hangs" ]; then
      hangs=$n
      echo "[spotter] GPU hang #$hangs since arm @ $(date +%H:%M:%S)${exit_at:+ (after the shutdown log)}: $(echo "$hl" | tail -1 | cut -c1-150)"
    fi
  fi
  # RPCS3.log delta (feeds 3 and 4) — scanned BEFORE the SILENT gate so an exit that has already
  # logged its shutdown is never misfiled as a freeze.
  # RPCS3.log fatal — scan the BYTE DELTA since last tick, not a fixed tail window. RPCS3's Log
  # Writer flushes in chunks, so a fatal can land already buried under the ~17-line syscall-stats
  # block within one flush; tail -8 missed a live rsx::thread fatal exactly this way (SSX,
  # 2026-08-11). A size DROP means the log was truncated by a relaunch — rescan from 0.
  lsz=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  # A size DROP = the log was rotated/replaced by a relaunch. Re-baseline WITHOUT scanning the
  # new content this tick: scanning from 0 right after rotation re-fired on the PREVIOUS
  # session's fatal during R3/relaunch churn (live miss, SSX 2026-08-11). New-session fatals
  # land as fresh deltas on later ticks and are still caught.
  [ "$lsz" -lt "${lprev:-0}" ] && lprev="$lsz"
  if [ "$lsz" -gt "${lprev:-0}" ]; then
    # Pattern precision: bare "fatal error" matches the startup CONFIG DUMP line
    # "Show fatal error hints: false" and false-fired on a healthy launch (2026-08-11).
    # Match the actual crash shapes only.
    tail -c +"$((lprev+1))" "$LOG" 2>/dev/null | grep -qiE "Segfault|SIGSEGV|Thread terminated due to fatal error" \
      && crash="${crash:+$crash; }RPCS3 FATAL"
    if [ -z "$exit_at" ] && tail -c +"$((lprev+1))" "$LOG" 2>/dev/null | grep -qF "$EXIT_LINE"; then
      exit_at=$now; exit_epoch=$(date +%s); echo "[spotter] RPCS3 finished its shutdown log @ $(date +%H:%M:%S) — timing the process exit"
    fi
    lprev=$lsz
  fi
  # 2. live_stat freshness (silent freeze / process death)
  if ingame "$etk" && { [ "$seen" = 1 ] || [ "$etk" != "$ARMVAL" ]; }; then
    seen=1; [ "$etk" = "$prev" ] && stale=$((stale+1)) || stale=0
  else [ "$seen" = 1 ] && stale=$((stale+1)); fi
  prev="$etk"
  # SILENT gate: a stale live_stat is only a freeze if the emulator is STILL ALIVE. If it's GONE,
  # this was a graceful exit/abort (live_stat just lingers) — NOT a crash, so no stub, no capture.
  # (Firing SILENT on every graceful exit is what produced the 28B header-only hangrd stub.)
  if [ -z "$crash" ] && [ -z "$exit_at" ] && [ "$seen" = 1 ] && [ "$stale" -ge "$STALE_TICKS" ]; then
    if emu_alive; then crash="SILENT (live_stat stale ${stale}x)"
    else graceful="emulator exited (live_stat stale ${stale}x, process gone)"; fi
  fi
  # 3. core dump (SPU/segfault class; the RPCS3.log fatal half is scanned above)
  nc=$(ls -t "$CORES" 2>/dev/null | head -1)
  if [ -n "$nc" ] && [ "$nc" != "$base_core" ]; then
    # After the shutdown log, a new core IS the exit hang: 0.9.1 aborts in a static destructor
    # (vkDestroyBuffer on the dead device) and the "hang" is the kernel writing a multi-GB core
    # to the card (2026-09-27, AppRun.wrapped.<pid>.6.core, kstack in vfs_coredump).
    if [ -n "$exit_at" ]; then crash="EXIT-CRASH (signal $(echo "$nc" | awk -F. '{print $(NF-1)}') after the shutdown log — the stall is the kernel writing $CORES/$nc)"
    else crash="${crash:+$crash; }NEW CORE $nc"; fi
  fi
  # 4. EXIT-HANG — the shutdown log is written; is the process gone?
  if [ -n "$exit_at" ] && [ -z "$crash" ]; then
    if ! emu_alive; then graceful="process left within $((now - exit_at))s of RPCS3's last shutdown line (tick ${INT}s)"
    elif [ $((now - exit_at)) -ge "$EXITHANG_SECS" ]; then crash="EXIT-HANG (process alive $((now - exit_at))s after RPCS3 finished its shutdown)"; fi
  fi
  printf '%s | GPU %s°C %sMHz | CPUp %s°C %sMHz | free %sMB rpcs3 %sMB | bat %s°C | etk[%s]%s\n' \
    "$(date +%H:%M:%S)" "$(zt 15)" "${gpuf:-?}" "$(zt 10)" "${cpf:-?}" "$free" "${rss:-?}" "$(zt 25)" "$etk" "${crash:+  *** $crash ***}"
  [ -n "$crash" ] && break
  [ -n "$graceful" ] && { echo "[spotter] graceful exit detected — $graceful"; break; }
  sleep "$INT"
done
if [ -n "$crash" ]; then
  echo "[spotter] >>> CRASH: $crash"
  dmesg | grep -E "a6xx_irq.*gpu fault ring|hangcheck recover|rb 0: fence" | tail -3
  case "$crash" in EXIT-HANG*|EXIT-CRASH*)
    # The operator is looking at a stuck screen with L1+R3 under a thumb: tell them to hold it.
    "$NOTIFY" "EXIT HANG — HOLD R3" "Capturing the stuck emulator. Wait for the next message." >/dev/null 2>&1 || true
    exithang_capture
    while [ $(( $(up) - exit_at )) -lt "$EXITHANG_WAIT" ] && emu_alive; do sleep 2; done
    el=$(( $(up) - exit_at )); r3=$(cat "$R3F" 2>/dev/null)
    if emu_alive; then
      echo "[spotter] >>> STILL HUNG ${el}s after the shutdown log — capture banked; R3 now."
      "$NOTIFY" "EXIT HANG CAPTURED" "Stuck ${el}s after exit. Press L1+R3 now." >/dev/null 2>&1 || true
    elif [ -n "$r3" ] && [ "$r3" -ge "$exit_epoch" ] 2>/dev/null; then
      echo "[spotter] >>> L1+R3 ended it $((r3 - exit_epoch))s after the shutdown log (the process was gone by +${el}s)."
    else
      echo "[spotter] >>> the process LEFT ON ITS OWN ~${el}s after the shutdown log — a bounded wait somewhere; that number is evidence."
      "$NOTIFY" "EXIT HANG CLEARED" "The emulator closed by itself after ${el}s. No R3 needed." >/dev/null 2>&1 || true
    fi ;;
  esac
  # --- finalize hangrd .rd + ib1 sidecar (the decode key) ---
  if [ -n "$RDFILE" ]; then
    # on an a6xx hang the cat unblocks + flushes the redump. Wait until the .rd is SIZE-STABLE
    # (~10s of no growth), NOT a fixed 6s: a large working set (long play session) streams for
    # >6s, and a fixed kill TRUNCATES it mid-stream — dropping BOTH cmdstream IBs → undecodable,
    # unrepairable (validated 2026-06-19: 6s-kill=211MB truncated vs size-stable=587MB complete).
    # Exits early if the cat finishes naturally (node EOF). For SILENT/CORE (cat still blocking at
    # ~0B) the size holds stable → loop exits in ~10s and the stub is dropped below.
    _prev=-1; _stable=0
    while [ "$_stable" -lt 5 ]; do
      kill -0 "$HANGRD_PID" 2>/dev/null || break
      _sz=$(wc -c < "$RDFILE" 2>/dev/null || echo 0)
      if [ "$_sz" = "$_prev" ]; then _stable=$((_stable + 1)); else _stable=0; _prev=$_sz; fi
      sleep 2
    done
    kill "$HANGRD_PID" 2>/dev/null   # size-stable (or already done); safe to stop
    rdsz=$(wc -c < "$RDFILE" 2>/dev/null || echo 0)
    { echo "$fl"
      echo "$fl" | sed -n 's/.*fence \([0-9a-fx]*\) status \([0-9A-Fa-f]*\) rb \([0-9a-f/]*\) ib1 \([0-9A-Fa-f]*\)\/\([0-9a-f]*\) ib2 \([0-9A-Fa-f]*\)\/\([0-9a-f]*\).*/fence=\1 status=\2 rb=\3 ib1=\4 ib1_size=\5 ib2=\6 ib2_size=\7/p'
    } > "$RDFILE.faultinfo"
    echo "[spotter] hangrd .rd: $RDFILE (${rdsz}B) + $(basename "$RDFILE").faultinfo"
    if [ "${rdsz:-0}" -lt 1024 ]; then
      echo "[spotter] WARN: .rd header-only (${rdsz}B) — hangrd didn't capture a cmdstream (dossier 6a); decode would be empty"
    else
      echo "[spotter]   $(cat "$RDFILE.faultinfo" | tail -1)"
      # VALIDATE + self-repair. The hangrd node can emit an INCOMPLETE redump even when the file
      # is size-stable and the reader has closed: a truncated trailing RD_BUFFER_CONTENTS and/or a
      # missing RD_CMDSTREAM_ADDR (the cmdstream-entry pointer, written at the redump TAIL). cffdump
      # then decodes 0 draws. Seen 2026-06-18 (Save 235823) + 2026-06-19 (prefer_gmem 114642 — that
      # one truncated BEFORE any R3, so it is the node's own dump, not an R3 race). Fix in place:
      # drop the incomplete tail, synthesize RD_CMDSTREAM_ADDR from the faultinfo ib1 sized to the
      # FULL containing RD_GPUADDR buffer (dmesg ib1_size is the *remaining* count → truncates).
      # Canonical/host tool: scripts/turnip/rd_repair.py (this is the self-contained rig twin).
      if command -v python3 >/dev/null 2>&1; then
        python3 - "$RDFILE" <<'RDREPAIR'
import sys, struct, os, re
rd = sys.argv[1]
size = os.path.getsize(rd); secs = []; trunc = None
with open(rd, "rb") as f:
    while True:
        off = f.tell(); h = f.read(8)
        if len(h) < 8: break
        t, sz = struct.unpack("<II", h); end = off + 8 + sz
        if end > size: trunc = (t, size - (off + 8)); break
        secs.append((t, sz, off, end)); f.seek(sz, 1)
has_cs = any(t == 6 for t, _, _, _ in secs)
if not trunc and has_cs:
    print("[spotter]   redump VALID (%d sections, has RD_CMDSTREAM_ADDR)" % len(secs)); sys.exit(0)
why = ("truncated;" if trunc else "") + ("" if has_cs else "no-cmdstream-addr")
fi = rd + ".faultinfo"; ib1 = None
if os.path.exists(fi):
    m = re.search(r"ib1=([0-9A-Fa-f]+)", open(fi).read()); ib1 = int(m.group(1), 16) if m else None
if ib1 is None:
    print("[spotter]   WARN redump DEFECTIVE (%s) + no ib1 — cannot self-repair; repair host-side" % why); sys.exit(0)
sd = None
with open(rd, "rb") as f:
    for t, sz, off, end in secs:
        if t == 3 and sz >= 8:
            f.seek(off + 8); d = f.read(sz)
            lo = struct.unpack("<I", d[0:4])[0]; ln = struct.unpack("<I", d[4:8])[0]
            hi = struct.unpack("<I", d[8:12])[0] if sz >= 12 else 0
            a = lo | (hi << 32)
            if a <= ib1 < a + ln: sd = (ln - (ib1 - a)) // 4; break
if sd is None:
    print("[spotter]   WARN redump DEFECTIVE (%s); ib1 0x%X unmapped — repair host-side" % (why, ib1)); sys.exit(0)
lg = secs[-1][3] if secs else 0
sect = struct.pack("<II", 6, 12) + struct.pack("<III", ib1 & 0xFFFFFFFF, sd, (ib1 >> 32) & 0xFFFFFFFF)
with open(rd, "rb") as f: body = f.read(lg)
with open(rd + ".t", "wb") as o: o.write(body); o.write(sect)
os.replace(rd + ".t", rd)
print("[spotter]   redump REPAIRED in place (%s) synth RD_CMDSTREAM_ADDR 0x%X sizedwords 0x%X" % (why, ib1, sd))
RDREPAIR
      fi
    fi
  fi
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run/0-runtime-dir}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
  SHOT="/tmp/spotter_crash_$(date +%H%M%S).png"
  command -v grim >/dev/null && timeout 3 grim "$SHOT" 2>/dev/null && echo "[spotter] frame $SHOT ($(wc -c < "$SHOT" 2>/dev/null)B)"
  echo "[spotter] state: AppRun=$(pgrep -x AppRun.wrapped | tr '\n' ' ') input_d=$(pgrep -f input_d.py >/dev/null && echo ALIVE || echo dead) active_id=$(cat /dev/shm/etk_shm/active_id.txt 2>/dev/null)"
  echo "[spotter] tune=$(cat /storage/games-internal/roms/etk/etk_telemetry/active_tune.txt 2>/dev/null)"
  echo "[spotter] ledger: $(tail -1 /storage/games-internal/roms/etk/etk_telemetry/sessions.tsv 2>/dev/null | cut -f1,2,5,10,11,15)"
else
  # disarm hangrd: kill the blocking cat and drop the header-only stub
  if [ -n "$HANGRD_PID" ]; then
    kill "$HANGRD_PID" 2>/dev/null
    [ -n "$RDFILE" ] && [ "$(wc -c < "$RDFILE" 2>/dev/null || echo 0)" -lt 1024 ] && rm -f "$RDFILE"
    echo "[spotter] hangrd disarmed (no crash); empty .rd removed."
  fi
  if [ -n "$graceful" ]; then
    echo "[spotter] >>> GRACEFUL EXIT: $graceful — no crash, no stub, no forensic capture."
  else
    echo "[spotter] window ended — NO crash caught (possible CLEAN run; check ledger)."
  fi
fi
