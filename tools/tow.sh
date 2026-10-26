#!/usr/bin/env bash
# ==========================================================
# tools/tow.sh — TOW: tow a game from one car to another
# ==========================================================
# A USB-C cable between two Flip 2s is a TOW LINE: one car takes the USB host
# and power-source role and enumerates the other's USB-net gadget (CDC-NCM,
# high-speed). Measured 2026-10-25, car12 -> car8: ~32-35 MB/s card to card
# over the line, 18.4 MB/s car to car over WiFi (same AP: every byte crosses
# the air twice). ssh over WiFi is the control line; the cable carries data.
# No cable -> the data rides WiFi (or force it with --wifi).
#
#   tools/tow.sh --from flip2-12g --to etk-rig BCUS98114        # tow GT5
#   tools/tow.sh --from flip2-12g --to etk-rig --plan BCUS98114 # scout only
#   tools/tow.sh --from A --to B NPEA00050 /storage/roms/psp/X.iso ...
#
# An ITEM is a PS3 title ID or a path under /storage/roms. A title ID tows
# everything the game is: ps3/ files with "(ID)" in the name, a .psn stub
# holding the ID, the files its .m3u names, dev_hdd0/game/ID* (update +
# install data), home/*/savedata/ID*, home/*/exdata/*-ID_*.rap (the PSN licence:
# without it RPCS3 says "Failed to decrypt content"), custom_configs/config_ID.yml. The
# donor keeps its copy: TOW only reads the donor (its scratch is /tmp, RAM).
#
# Integrity: every stream is md5'd in flight on BOTH ends (one read per
# side); a disc image lands as <name>.part and is renamed only when the two
# hashes match; tar items must match the donor's file count. A stream that
# carried nothing can never read as a pass (2026-10-25: a route out a dead
# `gadget` iface "sent" 1 GiB at 17970 MB/s and delivered 0 bytes).
# The recipient's scratch lives ON THE GAMES CARD (/storage/roms/.tow): on
# a UFS-booted car /storage is the 6 GB system partition, and the first
# trial filled it with a 3 GB stream copy.
# Collisions: refuses if anything already exists on the recipient (never
# overwrites the operator's files) unless --replace.
#
# Surface: the TUI (pit wall console, tools/tui.sh), then the recipient's
# EmulationStation list (ES API /reloadgames + /systems/ps3/games) must show
# the game. Log: state/tow/<stamp>/tow.log.
#
# WHO RUNS IT: it writes the recipient's library. Into car8 the Engineer
# runs it (lift access, manual §1.1); into any other car, the operator.
# The tow line's host car powers the other (~2.5 A out of its battery).
set -u
cd "$(dirname "$0")/.." || exit 1

ROOT=/storage/roms
BIG=1073741824          # files >= 1 GiB stream raw (and land as .part)
PORT0=47930
LINE_ADDR=169.254.170.250   # the host car's address on its cdc_ncm iface
RATE_USB=32; RATE_WIFI=18   # MB/s, measured 2026-10-25 (plan ETA only)

# ---------------------------------------------------------------- rig bodies
# POSIX + BusyBox only (manual §Q BusyBox laws).

# scout: items on $@ -> "F size rel" (raw stream) | "T size nfiles rel" (tar)
# | "E message". Paths are relative to $ROOT.
read -r -d '' SCOUT_SH <<'EOF'
ROOT=${TOW_ROOT:-/storage/roms}; BIG=${TOW_BIG:-1073741824}
cd "$ROOT" || { echo "E no $ROOT"; exit 1; }
emit() {
  p=$1
  [ -e "$p" ] || return 0
  if [ -f "$p" ]; then
    s=$(stat -c %s "$p")
    if [ "$s" -ge "$BIG" ]; then echo "F $s $p"; else echo "T $s 1 $p"; fi
  elif [ -d "$p" ]; then
    if [ -n "$(find "$p" -type f -size +8388607k | head -1)" ]; then
      echo "E $p holds a file over 8 GiB (tar cannot carry it): tow that file by path"; return 0
    fi
    echo "T $(du -sb "$p" | cut -f1) $(find "$p" -type f | wc -l) $p"
  fi
}
for it in "$@"; do
  case "$it" in
    "$ROOT"/*) rel=${it#"$ROOT"/}; [ -e "$rel" ] && emit "$rel" || echo "E $it: not on the donor" ;;
    [A-Z][A-Z][A-Z][A-Z][0-9][0-9][0-9][0-9][0-9])
      found=0
      for f in ps3/*; do
        case "$f" in *"($it)"*) emit "$f"; found=1 ;; esac
        case "$f" in *.psn) [ "$(tr -d ' \r\n' < "$f")" = "$it" ] && { emit "$f"; found=1; } ;; esac
      done
      for m in ps3/*"($it)"*.m3u; do
        [ -f "$m" ] || continue
        while IFS= read -r l || [ -n "$l" ]; do
          l=$(printf '%s' "$l" | tr -d '\r'); [ -n "$l" ] && emit "ps3/$l"
        done < "$m"
      done
      for d in bios/rpcs3/dev_hdd0/game/"$it"* bios/rpcs3/dev_hdd0/home/*/savedata/"$it"* \
               bios/rpcs3/dev_hdd0/home/*/exdata/*-"$it"_*.rap bios/rpcs3/custom_configs/config_"$it".yml; do
        [ -e "$d" ] && emit "$d"
      done
      [ "$found" = 1 ] || echo "E $it: no game file under $ROOT/ps3 (no \"($it)\" name, no .psn stub)" ;;
    *) echo "E $it: not a title ID or a path under $ROOT" ;;
  esac
done
EOF

# probe: one car's view of the line, its library and its battery.
read -r -d '' PROBE_SH <<'EOF'
echo "CAR $(cat /storage/.etk/car 2>/dev/null || hostname)"
echo "ROLE $(cat /sys/class/usb_role/*/role 2>/dev/null | head -1)"
for i in /sys/class/net/*; do
  case "$(readlink -f "$i/device/driver" 2>/dev/null)" in *cdc_ncm) echo "NCM ${i##*/}" ;; esac
done
if grep -qx configured /sys/class/udc/*/state 2>/dev/null; then
  a=$(ip -4 addr show gadget 2>/dev/null | awk '/inet /{sub("/.*","",$2); print $2; exit}')
  [ -n "$a" ] && echo "GADGET $a"
fi
a=$(ip -4 addr show wlan0 2>/dev/null | awk '/inet /{sub("/.*","",$2); print $2; exit}')
[ -n "$a" ] && echo "WLAN $a"
echo "BAT $(cat /sys/class/power_supply/battery/capacity 2>/dev/null) $(cat /sys/class/power_supply/battery/status 2>/dev/null)"
echo "FREE $(df -k /storage/roms | awk 'END{print $4*1024}')"
EOF

# rx: $1 = run dir (on the card), $2 = first port. Listeners for the tar
# stream (port0) and one per big.list line (port0+n), all up front.
read -r -d '' RX_SH <<'EOF'
W=$1; P=$2; ROOT=${TOW_ROOT:-/storage/roms}; cd "$W" || exit 1
if [ -s tar.list ]; then
  mkfifo t.md5 t.in
  md5sum < t.md5 > rx.0.md5 &
  nc -l -p "$P" > t.in & echo $! > nc.0.pid
  ( tee t.md5 < t.in | tar xf - -C "$ROOT"; echo $? > rx.0.rc ) &
fi
n=1
while IFS= read -r rel; do
  mkfifo "b$n.md5" "b$n.in"; mkdir -p "$(dirname "$ROOT/$rel")"
  md5sum < "b$n.md5" > "rx.$n.md5" &
  nc -l -p $((P + n)) > "b$n.in" & echo $! > "nc.$n.pid"
  ( tee "b$n.md5" < "b$n.in" > "$ROOT/$rel.part"; echo $? > "rx.$n.rc" ) &
  n=$((n + 1))
done < big.list
wait
echo done > rx.state
EOF

# tx: $1 = run dir (/tmp), $2 = recipient address, $3 = first port.
read -r -d '' TX_SH <<'EOF'
W=$1; A=$2; P=$3; ROOT=${TOW_ROOT:-/storage/roms}; cd "$ROOT" || exit 1
set -o pipefail 2>/dev/null
if [ -s "$W/tar.list" ]; then
  mkfifo "$W/t.md5"
  md5sum < "$W/t.md5" > "$W/tx.0.md5" &
  tar cf - -T "$W/tar.list" | tee "$W/t.md5" | nc "$A" "$P"
  echo "0 $?" >> "$W/tx.log"; wait
fi
n=1
while IFS= read -r rel; do
  mkfifo "$W/b$n.md5"
  md5sum < "$W/b$n.md5" > "$W/tx.$n.md5" &
  cat "$rel" | tee "$W/b$n.md5" | nc "$A" $((P + n))
  echo "$n $?" >> "$W/tx.log"; wait
  n=$((n + 1))
done < "$W/big.list"
echo done >> "$W/tx.log"
EOF

# progress on the recipient: "n bytes" per stream (bytes = nc's writes so
# far, or "end" once that listener has exited).
read -r -d '' PROG_SH <<'EOF'
cd "$1" 2>/dev/null || exit 0
for f in nc.*.pid; do
  [ -f "$f" ] || continue
  n=${f#nc.}; n=${n%.pid}; p=$(cat "$f")
  if [ ! -d "/proc/$p" ]; then echo "$n end"
  elif grep -q '^nc' "/proc/$p/cmdline"; then echo "$n $(awk '/^wchar/{print $2}' "/proc/$p/io")"
  else echo "$n 0"; fi      # forked, not yet exec'd into nc
done
[ -f rx.state ] && echo "rx done"
EOF

# ---------------------------------------------------------------- helpers
q() { local s=${1//\'/\'\\\'\'}; printf "'%s'" "$s"; }     # POSIX single-quote
human() { awk -v b="$1" 'BEGIN{ if (b >= 1073741824) printf "%.1f GB", b/1073741824;
          else if (b >= 1048576) printf "%.0f MB", b/1048576; else printf "%.0f KB", b/1024 }'; }

# tow_verdict TX_MD5 RX_MD5 RC  -> prints PASS or FAIL:<why>. An empty or
# missing hash is a FAIL: a stream that carried nothing never passes.
tow_verdict() {
    local tx=${1%% *} rx=${2%% *} rc=$3
    [ -n "$tx" ] || { echo "FAIL:no donor hash"; return; }
    [ -n "$rx" ] || { echo "FAIL:no recipient hash"; return; }
    [ "$tx" = "$rx" ] || { echo "FAIL:md5 mismatch"; return; }
    [ "$tx" != d41d8cd98f00b204e9800998ecf8427e ] || { echo "FAIL:empty stream"; return; }
    [ "$rc" = 0 ] || { echo "FAIL:recipient exit $rc"; return; }
    echo PASS
}

[ "${TOW_LIB:-0}" = 1 ] && return 0 2>/dev/null   # test_tow.sh sources the helpers

usage() { sed -n '9,13p' "$0" | sed 's/^# //'; exit 2; }
FROM=""; TO=""; PLAN=0; WIFI=0; REPLACE=0; ITEMS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --from) FROM=${2:?}; shift 2 ;;
        --to) TO=${2:?}; shift 2 ;;
        --plan) PLAN=1; shift ;;
        --wifi) WIFI=1; shift ;;
        --replace) REPLACE=1; shift ;;
        --verbose) ETK_VERBOSE=1; shift ;;
        -h|--help) usage ;;
        -*) usage ;;
        *) ITEMS+=("$1"); shift ;;
    esac
done
[ -n "$FROM" ] && [ -n "$TO" ] && [ ${#ITEMS[@]} -gt 0 ] || usage

STAMP=$(date +%Y%m%d-%H%M%S)
LOGDIR=state/tow/$STAMP; mkdir -p "$LOGDIR"; LOG=$LOGDIR/tow.log
CTL=$(mktemp -d)   # ssh control sockets + list staging; removed at every exit below
SSHO=(-o BatchMode=yes -o ConnectTimeout=8 -o ControlMaster=auto -o "ControlPath=$CTL/%r@%h:%p" -o ControlPersist=120)
on() { local h=$1; shift; ssh "${SSHO[@]}" "$h" "$@"; }
log() { printf '%s %s\n' "$(date +%T)" "$*" >> "$LOG"; }

ETK_VERBOSE=${ETK_VERBOSE:-0}
source ./tools/tui.sh
TUI_HEADER_TITLE="ETK TOW"; TUI_HEADER_KEY="LINK"; TUI_HEADER_VERSION="tools/tow.sh"
ETK_BUILD_TYPE="...."; RIG_SSH="$FROM -> $TO"
TUI_TOTAL_STEPS=6
TUI_STEP_LABELS=("SCOUT          " "TOW LINE       " "GAME DATA      " "DISC IMAGES    " "VERIFY         " "ES LIST        ")
TUI_FINISH_VERBOSE_HEAD="TOW COMPLETE."
TEARDOWN=""; RXDIR=""; TXDIR=""; STARTED=0
teardown() {
    [ -n "$TEARDOWN" ] && { on "$LINE_HOST" "$TEARDOWN" >/dev/null 2>&1; TEARDOWN=""; }
}
stop_ends() {   # a failed or aborted tow: stop both ends, drop our own .part files
    on "$FROM" "kill -TERM -\$(cat $TXDIR/pgid) 2>/dev/null" >/dev/null 2>&1
    on "$TO" "kill -TERM -\$(cat $RXDIR/pgid) 2>/dev/null; sleep 1;
        while IFS= read -r r; do rm -f \"$ROOT/\$r.part\"; done < $RXDIR/big.list" >/dev/null 2>&1
}
fail() {
    log "FAIL $*"
    [ "$STARTED" = 1 ] && { STARTED=0; stop_ends; }
    teardown; rm -rf "$CTL"
    tui_fail "$*  (log: $LOG)"
}
abort() { fail "aborted (both ends stopped, partial images removed)"; }
if tui_should_activate; then tui_init; fi

# ---------------------------------------------------------------- 1 SCOUT
tui_step_start 0
log "tow $FROM -> $TO: ${ITEMS[*]}"
args=""; for it in "${ITEMS[@]}"; do args+=" $(q "$it")"; done
SCOUT=$( { printf 'set --%s\n' "$args"; printf '%s\n' "$SCOUT_SH"; } | on "$FROM" sh -s 2>&1 | sort -u ) \
    || fail "cannot reach $FROM"
log "scout:"$'\n'"$SCOUT"
errs=$(grep '^E ' <<<"$SCOUT" | cut -c3-)
[ -z "$errs" ] || fail "$(head -1 <<<"$errs")"
TARL=(); TARN=(); BIGL=(); BIGS=(); TAR_BYTES=0; BIG_BYTES=0
while read -r kind size rest; do
    case "$kind" in
        T) n=${rest%% *}; TARL+=("${rest#* }"); TARN+=("$n"); TAR_BYTES=$((TAR_BYTES + size)) ;;
        F) BIGL+=("$rest"); BIGS+=("$size"); BIG_BYTES=$((BIG_BYTES + size)) ;;
    esac
done <<<"$SCOUT"
[ $(( ${#TARL[@]} + ${#BIGL[@]} )) -gt 0 ] || fail "nothing to tow"
TOTAL=$((TAR_BYTES + BIG_BYTES))
DPROBE=$(printf '%s\n' "$PROBE_SH" | on "$FROM" sh -s) || fail "cannot probe $FROM"
RPROBE=$(printf '%s\n' "$PROBE_SH" | on "$TO" sh -s) || fail "cannot reach $TO"
pv() { awk -v k="$2" '$1==k{ $1=""; sub(/^ /,""); print; exit }' <<<"$1"; }
DCAR=$(pv "$DPROBE" CAR); RCAR=$(pv "$RPROBE" CAR)
[ "$DCAR" != "$RCAR" ] || fail "--from and --to are the same car ($DCAR)"
RIG_SSH="$DCAR -> $RCAR"
FREE=$(pv "$RPROBE" FREE)
[ "${FREE:-0}" -gt $((TOTAL + 1073741824)) ] || fail "$RCAR has $(human "${FREE:-0}") free, the tow needs $(human "$TOTAL") + 1 GB"
ALL=("${TARL[@]}" "${BIGL[@]}")
chk=""; for r in "${ALL[@]}"; do chk+="[ -e $(q "$ROOT/$r") ] && echo $(q "$r");"$'\n'; done
COLL=$(on "$TO" "$chk")
if [ -n "$COLL" ] && [ "$REPLACE" = 0 ] && [ "$PLAN" = 0 ]; then
    log "collisions:"$'\n'"$COLL"
    fail "$RCAR already has $(wc -l <<<"$COLL") of these (first: $(head -1 <<<"$COLL")); --replace to overwrite"
fi
say "$DCAR -> $RCAR: ${#TARL[@]} data item(s) $(human "$TAR_BYTES") + ${#BIGL[@]} image(s) $(human "$BIG_BYTES")"
tui_step_done 0

# ---------------------------------------------------------------- 2 TOW LINE
tui_step_start 1
LINK=WIFI; LINE_HOST=""; DATA_ADDR=$(pv "$RPROBE" WLAN)
DNCM=$(pv "$DPROBE" NCM); RNCM=$(pv "$RPROBE" NCM)
DGAD=$(pv "$DPROBE" GADGET); RGAD=$(pv "$RPROBE" GADGET)
if [ "$WIFI" = 0 ]; then
    if [ -n "$DNCM" ] && [ -n "$RGAD" ]; then       # donor is the line's host
        LINK=USB; LINE_HOST=$FROM; IF=$DNCM; PEER=$RGAD; DATA_ADDR=$RGAD
    elif [ -n "$RNCM" ] && [ -n "$DGAD" ]; then     # recipient is the line's host
        LINK=USB; LINE_HOST=$TO; IF=$RNCM; PEER=$DGAD; DATA_ADDR=$LINE_ADDR
    fi
fi
if [ "$LINK" = USB ]; then
    HCAR=$([ "$LINE_HOST" = "$FROM" ] && echo "$DCAR" || echo "$RCAR")
    HBAT=$(pv "$([ "$LINE_HOST" = "$FROM" ] && echo "$DPROBE" || echo "$RPROBE")" BAT)
    if [ "$PLAN" = 0 ]; then
        on "$LINE_HOST" "ip link set $IF up; ip addr add $LINE_ADDR/32 dev $IF 2>/dev/null;
            ip route replace $PEER/32 dev $IF src $LINE_ADDR" || fail "cannot raise the line on $HCAR"
        TEARDOWN="ip route del $PEER/32 dev $IF; ip addr del $LINE_ADDR/32 dev $IF"
        # the dead-gadget trap: prove the route, not just the ping
        route=$(on "$LINE_HOST" "ip route get $PEER")
        grep -q "dev $IF" <<<"$route" || fail "route to $PEER leaves by '$route', not the line"
        on "$FROM" "ping -c2 -W2 $DATA_ADDR >/dev/null" || fail "no answer from $RCAR over the line"
    fi
    say "tow line: USB, $HCAR is host and powers the other car ($HCAR battery ${HBAT})"
else
    [ -n "$DATA_ADDR" ] || fail "no cable link and $RCAR has no WiFi address"
    say "tow line: no cable link -- data over WiFi to $DATA_ADDR"
fi
ETK_BUILD_TYPE=$LINK; [ "${TUI_ACTIVE:-0}" = 1 ] && tui_draw_header
log "link $LINK data_addr=$DATA_ADDR host=${LINE_HOST:-none}"
tui_step_done 1

if [ "$PLAN" = 1 ]; then
    tui_cleanup
    rate=$([ "$LINK" = USB ] && echo $RATE_USB || echo $RATE_WIFI)
    echo; echo "TOW PLAN  $DCAR -> $RCAR  over $LINK  (~$(( TOTAL / 1048576 / rate / 60 + 1 )) min at the ~$rate MB/s $LINK rate;"
    echo "          a worn recipient card writes slower: car8's 256 GB SanDisk ran 13-15 MB/s)"
    while read -r kind size rest; do
        case "$kind" in
            T) printf '  data   %9s  %6s files  %s\n' "$(human "$size")" "${rest%% *}" "${rest#* }" ;;
            F) printf '  image  %9s               %s  (lands as .part until verified)\n' "$(human "$size")" "$rest" ;;
        esac
    done <<<"$SCOUT"
    echo "  $RCAR free: $(human "$FREE")    collisions: $([ -n "$COLL" ] && wc -l <<<"$COLL" || echo none)"
    echo "  log: $LOG"
    rm -rf "$CTL"; exit 0
fi

# ---------------------------------------------------------------- 3+4 TOW
RXDIR=$ROOT/.tow/$STAMP; TXDIR=/tmp/tow-$STAMP
lists() { printf '%s\n' "${TARL[@]}" | sed '/^$/d' > "$CTL/tar.list"; printf '%s\n' "${BIGL[@]}" | sed '/^$/d' > "$CTL/big.list"; }
lists
for h in "$TO:$RXDIR" "$FROM:$TXDIR"; do
    on "${h%%:*}" "mkdir -p ${h#*:}" || fail "cannot make ${h#*:} on ${h%%:*}"
    for f in tar.list big.list; do on "${h%%:*}" "cat > ${h#*:}/$f" < "$CTL/$f"; done
done
printf '%s\n' "$RX_SH" | on "$TO" "cat > $RXDIR/rx.sh"
printf '%s\n' "$TX_SH" | on "$FROM" "cat > $TXDIR/tx.sh"
on "$TO" "{ cd $RXDIR && setsid sh -c 'echo \$\$ > pgid; exec sh rx.sh $RXDIR $PORT0' > rx.err 2>&1 < /dev/null; } > /dev/null 2>&1 < /dev/null &"
want=$(( ${#BIGL[@]} + (${#TARL[@]} > 0 ? 1 : 0) ))
for _ in $(seq 1 20); do
    up=$(on "$TO" "netstat -ltn 2>/dev/null" | awk -v a=$PORT0 -v b=$((PORT0 + ${#BIGL[@]})) \
        '{n=split($4,x,":"); p=x[n]+0; if (p>=a && p<=b) c++} END{print c+0}')
    [ "$up" -ge "$want" ] && break; sleep 0.5
done
[ "$up" -ge "$want" ] || fail "$RCAR listeners not up ($up/$want)"
trap 'abort' INT TERM; STARTED=1
on "$FROM" "{ cd $TXDIR && setsid sh -c 'echo \$\$ > pgid; exec sh tx.sh $TXDIR $DATA_ADDR $PORT0' > tx.err 2>&1 < /dev/null; } > /dev/null 2>&1 < /dev/null &"
log "streams started: tar=${#TARL[@]} items, images=${#BIGL[@]}, port0=$PORT0"
[ ${#TARL[@]} -gt 0 ] && tui_step_start 2 || { tui_step_start 2; tui_step_done 2; }
t0=$(date +%s); last=-1; lastmove=$t0; cur=""
while :; do
    sleep 2
    P=$(printf '%s\n' "$PROG_SH" | on "$TO" sh -s -- "$RXDIR") || continue
    got=0; dt=0; db=0; active=""
    while read -r n b; do
        [ "$n" = rx ] && continue
        if [ "$b" = end ]; then b=$([ "$n" = 0 ] && echo "$TAR_BYTES" || echo "${BIGS[$((n - 1))]}"); else active=$n; fi
        [ "$n" = 0 ] && dt=$b || db=$((db + b))
        got=$((got + b))
    done <<<"$P"
    now=$(date +%s)
    [ "$got" != "$last" ] && { last=$got; lastmove=$now; }
    [ $((now - lastmove)) -lt 60 ] || fail "stalled: nothing arrived for 60 s at $(human "$got")"
    rate=$(( got / 1048576 / (now - t0 > 0 ? now - t0 : 1) ))
    eta=$(( rate > 0 ? (TOTAL - got) / 1048576 / rate : 0 ))
    if [ ${#TARL[@]} -gt 0 ] && [ "$TAR_BYTES" -gt 0 ]; then tui_step_progress 2 $(( dt * 100 / TAR_BYTES )); fi
    if [ "$active" != "" ] && [ "$active" != 0 ] && [ "$cur" != img ]; then
        [ ${#TARL[@]} -gt 0 ] && tui_step_done 2; tui_step_start 3; cur=img
    fi
    [ "$BIG_BYTES" -gt 0 ] && [ "$cur" = img ] && tui_step_progress 3 $(( db * 100 / BIG_BYTES ))
    say "$(human "$got") / $(human "$TOTAL")  ·  $rate MB/s  ·  ETA $((eta / 60))m$(printf %02d $((eta % 60)))s"
    grep -q '^rx done' <<<"$P" && break
done
trap - INT TERM; STARTED=0
[ ${#TARL[@]} -gt 0 ] && tui_step_done 2
tui_step_start 3; tui_step_done 3
el=$(( $(date +%s) - t0 )); log "streams done: $(human "$TOTAL") in ${el}s"

# ---------------------------------------------------------------- 5 VERIFY
tui_step_start 4
TXLOG=$(on "$FROM" "cat $TXDIR/tx.log; for f in $TXDIR/tx.*.md5; do echo \"\$(basename \$f) \$(cat \$f)\"; done")
RXOUT=$(on "$TO" "cd $RXDIR; for f in rx.*.md5; do echo \"\$f \$(cat \$f)\"; done; for f in rx.*.rc; do echo \"\$f \$(cat \$f)\"; done")
log "tx:"$'\n'"$TXLOG"; log "rx:"$'\n'"$RXOUT"
hash() { awk -v k="$2" '$1==k{print $2}' <<<"$1"; }
BAD=0
if [ ${#TARL[@]} -gt 0 ]; then
    v=$(tow_verdict "$(hash "$TXLOG" tx.0.md5)" "$(hash "$RXOUT" rx.0.md5)" "$(hash "$RXOUT" rx.0.rc)")
    cnt=""; for i in "${!TARL[@]}"; do cnt+="find $(q "$ROOT/${TARL[$i]}") -type f | wc -l;"; done
    got=($(on "$TO" "$cnt"))
    for i in "${!TARL[@]}"; do
        [ "${got[$i]:-0}" = "${TARN[$i]}" ] || { v="FAIL:${TARL[$i]} has ${got[$i]:-0}/${TARN[$i]} files"; break; }
    done
    say "game data: $v"; log "verdict tar: $v"; [ "$v" = PASS ] || BAD=1
fi
for i in "${!BIGL[@]}"; do
    n=$((i + 1)); r=${BIGL[$i]}
    v=$(tow_verdict "$(hash "$TXLOG" tx.$n.md5)" "$(hash "$RXOUT" rx.$n.md5)" "$(hash "$RXOUT" rx.$n.rc)")
    if [ "$v" = PASS ]; then
        on "$TO" "mv $(q "$ROOT/$r.part") $(q "$ROOT/$r")" || v="FAIL:rename"
    else
        on "$TO" "rm -f $(q "$ROOT/$r.part")"
    fi
    say "${r##*/}: $v"; log "verdict $r: $v"; [ "$v" = PASS ] || BAD=1
    tui_step_progress 4 $(( n * 100 / ${#BIGL[@]} ))
done
on "$FROM" "rm -rf $TXDIR"
[ "$BAD" = 0 ] && on "$TO" "rm -rf $RXDIR; rmdir $ROOT/.tow 2>/dev/null"
teardown
[ "$BAD" = 0 ] || fail "verify failed -- see $LOG (recipient scratch kept: $RCAR:$RXDIR)"
tui_step_done 4

# ---------------------------------------------------------------- 6 ES LIST
tui_step_start 5
ES="not checked"
shown=(); for r in "${ALL[@]}"; do case "$r" in ps3/*) shown+=("$ROOT/$r") ;; esac; done
if [ ${#shown[@]} -gt 0 ]; then
    running=$(on "$TO" "curl -s -m 5 http://127.0.0.1:1234/runningGame")
    if [ -n "$running" ] && ! grep -q "NO GAME RUNNING" <<<"$running"; then
        ES="a game is running on $RCAR: list not reloaded (it shows the game at the next ES start)"
    elif on "$TO" "curl -s -m 20 http://127.0.0.1:1234/reloadgames >/dev/null"; then
        ES="NOT in the list"       # the reload is async: poll up to ~30 s (a fixed 3 s missed it, 2026-10-26)
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            sleep 3
            list=$(on "$TO" "curl -s -m 10 http://127.0.0.1:1234/systems/ps3/games")
            for s in "${shown[@]}"; do grep -qF "\"path\": \"$s\"" <<<"$list" && { ES="in the list (${s##*/})"; break 2; }; done
        done
    else
        ES="ES not answering on $RCAR (list unconfirmed)"
    fi
fi
log "es: $ES"; say "ES ps3: $ES"
tui_step_done 5

TUI_FINISH_BANNER="━━━ TOWED ━━━  $DCAR -> $RCAR  ·  $(human "$TOTAL") in $((el / 60))m$(printf %02d $((el % 60)))s over $LINK"
TUI_FINISH_LINE1="every stream md5-matched on both ends; images renamed from .part"
TUI_FINISH_LINE2="ES on $RCAR: $ES"
TUI_FINISH_LINE3="log: $LOG"
TUI_FINISH_VERBOSE_BODY="    $TUI_FINISH_BANNER\n    $TUI_FINISH_LINE2\n    $TUI_FINISH_LINE3"
tui_finish
rm -rf "$CTL"
case "$ES" in "NOT in the list") exit 3 ;; esac
exit 0
