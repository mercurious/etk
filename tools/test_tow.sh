#!/usr/bin/env bash
# ==========================================================
# tools/test_tow.sh — TOW (tools/tow.sh): scout, verdict, streams, header
# ==========================================================
# Proves:
#   1. the verdict never passes a stream that carried nothing: missing hash,
#      mismatch, the empty-stream md5, a recipient error all FAIL (the 2026-10-25
#      trial "sent" 1 GiB at 17970 MB/s out a dead gadget iface and delivered 0);
#   2. SCOUT resolves a title ID to exactly what the game is (ps3 "(ID)" files,
#      .psn stub by content, .m3u targets, dev_hdd0 game + savedata, custom
#      config), ignores a decoy ID, routes >= 1 GiB files to raw streams, and
#      refuses a dir holding a file tar cannot carry (> 8 GiB);
#   3. tools/tui.sh's header still reads TIER for install/uninstall and LINK
#      for TOW, at the same visible width;
#   4. (--rig HOST) the rig bodies under BusyBox: SCOUT on a /tmp fixture, then a
#      loopback tow (RX + TX over 127.0.0.1) -> PASS with files in place, and the
#      broken streams -> FAIL: no listener, a truncated image, an empty stream.
#      Writes only /tmp/towtest on the rig.
#
# DISCRIMINATION:
#   tools/test_tow.sh                      # working tree — must PASS
#   tools/test_tow.sh --against 37cd0b169  # before TOW existed — must FAIL
#   tools/test_tow.sh --rig etk-rig        # + the BusyBox leg
set -u
cd "$(dirname "$0")/.." || exit 1
REV=""; RIG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --against) REV="${2:?--against needs a revision}"; shift 2 ;;
        --rig) RIG="${2:?--rig needs an ssh host}"; shift 2 ;;
        *) echo "usage: $0 [--against <rev>] [--rig <host>]"; exit 2 ;;
    esac
done
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
if [ -n "$REV" ]; then
    mkdir -p "$T/src/tools"
    git show "$REV:tools/tow.sh" > "$T/src/tools/tow.sh" 2>/dev/null || { echo "FAIL: no tools/tow.sh at $REV"; exit 1; }
    git show "$REV:tools/tui.sh" > "$T/src/tools/tui.sh"
    SRC=$T/src
else
    SRC=$PWD
fi
PASS=0; FAILS=0
ok() { PASS=$((PASS + 1)); echo "  ok   $1"; }
no() { FAILS=$((FAILS + 1)); echo "  FAIL $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1 -- got [$2] want [$3]"; fi; }

TOW_LIB=1 source "$SRC/tools/tow.sh" || { echo "FAIL: cannot source tow.sh"; exit 1; }

echo "1. verdict"
E=d41d8cd98f00b204e9800998ecf8427e
H=0123456789abcdef0123456789abcdef
check "match + rc 0 passes"            "$(tow_verdict "$H  -" "$H  -" 0)" PASS
check "no recipient hash fails"        "$(tow_verdict "$H  -" "" 0)"     "FAIL:no recipient hash"
check "no donor hash fails"            "$(tow_verdict "" "$H  -" 0)"     "FAIL:no donor hash"
check "mismatch fails"                 "$(tow_verdict "$H  -" "${H%?}0  -" 0)" "FAIL:md5 mismatch"
check "empty stream fails (both ends)" "$(tow_verdict "$E  -" "$E  -" 0)" "FAIL:empty stream"
check "tar error fails"                "$(tow_verdict "$H  -" "$H  -" 2)" "FAIL:recipient exit 2"

fixture() {   # $1 = root.  Sparse files by dd seek: BusyBox has no truncate
    local R=$1
    sparse() { dd if=/dev/null of="$2" bs=1 seek="$1" 2>/dev/null; }
    mkdir -p "$R/ps3" "$R/bios/rpcs3/dev_hdd0/game/BCUS98114/USRDIR" "$R/bios/rpcs3/dev_hdd0/game/BCUS98114DLC" \
             "$R/bios/rpcs3/dev_hdd0/home/00000001/savedata/BCUS98114-GAME" "$R/bios/rpcs3/custom_configs" \
             "$R/bios/rpcs3/dev_hdd0/game/NPEA00050" "$R/bios/rpcs3/dev_hdd0/game/BCUS98158" "$R/bigdir" \
             "$R/bios/rpcs3/dev_hdd0/home/00000001/exdata"
    sparse 1100M "$R/ps3/Gran Turismo 5 (BCUS98114).iso"
    printf 'Gran Turismo 5 (BCUS98114).iso\r\n' > "$R/ps3/Gran Turismo 5 (BCUS98114).m3u"
    printf 'NPEA00050' > "$R/ps3/Gran Turismo 5 Prologue Spec III.psn"
    printf 'BCUS98158' > "$R/ps3/Decoy.psn"
    sparse 1100M "$R/ps3/Decoy (BCUS98158).iso"
    sfo() {   # $1 = path, $2 = TITLE: a minimal real PARAM.SFO (python3 is on the host and every car)
        python3 -c '
import struct, sys
p, t = sys.argv[1], sys.argv[2].encode()
key = b"TITLE\0"; val = t + b"\0" * (128 - len(t))
idx = struct.pack("<HHIII", 0, 0x204, len(t) + 1, 128, 0)
hdr = struct.pack("<4sIIII", b"\0PSF", 0x101, 20 + 16, 20 + 16 + len(key), 1)
open(p, "wb").write(hdr + idx + key + val)' "$1" "$2"
    }
    sfo "$R/bios/rpcs3/dev_hdd0/game/BCUS98114/PARAM.SFO" "GRAN TURISMO 5"
    # a save under ANOTHER serial of the same game (2026-10-26: SSX's saves are BLUS30849…, towed
    # without them): carried when its PARAM.SFO TITLE equals the game's; a different title is not
    mkdir -p "$R/bios/rpcs3/dev_hdd0/home/00000001/savedata/BLUS30849-GT5TWIN" \
             "$R/bios/rpcs3/dev_hdd0/home/00000001/savedata/BLUS99999-OTHER"
    sfo "$R/bios/rpcs3/dev_hdd0/home/00000001/savedata/BLUS30849-GT5TWIN/PARAM.SFO" "GRAN TURISMO 5"
    sfo "$R/bios/rpcs3/dev_hdd0/home/00000001/savedata/BLUS99999-OTHER/PARAM.SFO" "Some Other Game"
    echo b > "$R/bios/rpcs3/dev_hdd0/game/BCUS98114/USRDIR/EBOOT.BIN"
    echo c > "$R/bios/rpcs3/dev_hdd0/game/BCUS98114DLC/x"
    echo d > "$R/bios/rpcs3/dev_hdd0/home/00000001/savedata/BCUS98114-GAME/SYS"
    echo e > "$R/bios/rpcs3/custom_configs/config_BCUS98114.yml"
    echo f > "$R/bios/rpcs3/dev_hdd0/game/NPEA00050/x"
    sparse 9G "$R/bigdir/huge.bin"
    # PSN licences: the title's own is carried, another title's is not (2026-10-26: SSX towed without its .rap)
    printf 'rap-gt5-0123456' > "$R/bios/rpcs3/dev_hdd0/home/00000001/exdata/UP0006-BCUS98114_00-GT5LICENSE000000.rap"
    printf 'rap-other-01234' > "$R/bios/rpcs3/dev_hdd0/home/00000001/exdata/UP0006-BCUS98158_00-OTHERLICENSE0000.rap"
}
# T sizes are du -sb (fs-dependent for dirs): compare kind + count + path; F exact
SCOUT_WANT='F 1153433600 ps3/Gran Turismo 5 (BCUS98114).iso
T * 1 bios/rpcs3/dev_hdd0/home/00000001/exdata/UP0006-BCUS98114_00-GT5LICENSE000000.rap
T * 1 bios/rpcs3/custom_configs/config_BCUS98114.yml
T * 1 bios/rpcs3/dev_hdd0/game/BCUS98114DLC
T * 1 bios/rpcs3/dev_hdd0/game/NPEA00050
T * 1 bios/rpcs3/dev_hdd0/home/00000001/savedata/BCUS98114-GAME
T * 1 bios/rpcs3/dev_hdd0/home/00000001/savedata/BLUS30849-GT5TWIN
T * 1 ps3/Gran Turismo 5 (BCUS98114).m3u
T * 1 ps3/Gran Turismo 5 Prologue Spec III.psn
T * 2 bios/rpcs3/dev_hdd0/game/BCUS98114'
scout_norm() { awk '$1=="T"{$2="*"} {print}' | sort -u; }
echo "2. scout (host shell)"
fixture "$T/root"
got=$( { printf 'set -- BCUS98114 NPEA00050\n'; printf '%s\n' "$SCOUT_SH"; } | TOW_ROOT=$T/root sh -s | scout_norm)
want=$(sort -u <<<"$SCOUT_WANT")
check "GT5 + Spec III resolve exactly" "$got" "$want"
got=$( { printf 'set -- %s ZZZZ00000\n' "$T/root/bigdir"; printf '%s\n' "$SCOUT_SH"; } | TOW_ROOT=$T/root sh -s | cut -c1-40)
case "$got" in *"holds a file over 8 GiB"*) ok "a > 8 GiB file inside a dir is refused" ;; *) no "8 GiB guard -- got [$got]" ;; esac
case "$got" in *"ZZZZ00000: no game file"*) ok "an unknown title ID is an error" ;; *) no "unknown ID -- got [$got]" ;; esac

echo "3. header"
hdr() { ( unset TUI_HEADER_KEY TUI_HEADER_VERSION; eval "$1"; source "$SRC/tools/tui.sh"
          TUI_ROW_HEADER_TOP=1; tui_draw_header ) | sed -E 's/\x1b\[[0-9;]*[A-Za-z]//g'; }
h1=$(hdr 'RIG_SSH=root@etk-rig')
h2=$(hdr 'RIG_SSH="car12 -> car8"; TUI_HEADER_KEY=LINK; TUI_HEADER_VERSION=tools/tow.sh; ETK_BUILD_TYPE=USB')
case "$h1" in *"TIER: FULL"*"v14.1.0"*) ok "install header unchanged" ;; *) no "install header -- [$h1]" ;; esac
case "$h2" in *"LINK: USB"*"tools/tow.sh"*) ok "TOW header reads LINK" ;; *) no "TOW header -- [$h2]" ;; esac
check "same visible width" "$(printf '%s' "$h1" | wc -m)" "$(printf '%s' "$h2" | wc -m)"

if [ -n "$RIG" ]; then
    echo "4. rig leg on $RIG (BusyBox, /tmp/towtest only)"
    R=/tmp/towtest
    rq() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$RIG" "$@"; }
    rq "rm -rf $R; mkdir -p $R/bin" || { no "reach $RIG"; RIG=""; }
fi
if [ -n "$RIG" ]; then
    declare -f fixture | rq "cat > $R/bin/fixture.sh; echo 'fixture \"\$1\"' >> $R/bin/fixture.sh"
    for v in SCOUT_SH RX_SH TX_SH; do printf '%s\n' "${!v}" | rq "cat > $R/bin/$v"; done
    rq "sh $R/bin/fixture.sh $R/src && mkdir -p $R/dst/ps3 $R/dst/bios/rpcs3"
    got=$(rq "cd $R && { echo 'set -- BCUS98114 NPEA00050'; cat bin/SCOUT_SH; } | TOW_ROOT=$R/src sh -s" | scout_norm)
    check "BusyBox scout == host scout" "$got" "$want"

    # a real loopback tow: one data stream (3 items) + one image (40 MB random)
    rq "cd $R && head -c 41943040 /dev/urandom > src/ps3/img.iso && mkdir -p run1 &&
        printf '%s\n' bios/rpcs3/dev_hdd0/game/BCUS98114 bios/rpcs3/custom_configs/config_BCUS98114.yml 'ps3/Gran Turismo 5 (BCUS98114).m3u' > run1/tar.list &&
        echo ps3/img.iso > run1/big.list && cp -r run1 tx1"
    tow_loop() {   # $1 run, $2 port, $3 = the sender command (default: TX_SH)
        rq "cd $R/$1 && (TOW_ROOT=$R/dst setsid sh $R/bin/RX_SH $R/$1 $2 > rx.err 2>&1 < /dev/null &) ; sleep 1"
        rq "${3:-TOW_ROOT=$R/src sh $R/bin/TX_SH $R/tx${1#run} 127.0.0.1 $2} > /dev/null 2>&1"
        rq "i=0; while [ ! -f $R/$1/rx.state ] && [ \$i -lt 20 ]; do sleep 0.5; i=\$((i+1)); done"
    }
    md() { rq "cat $1 2>/dev/null"; }
    tow_loop run1 47990
    check "data stream PASS" "$(tow_verdict "$(md $R/tx1/tx.0.md5)" "$(md $R/run1/rx.0.md5)" "$(md $R/run1/rx.0.rc)")" PASS
    check "image stream PASS" "$(tow_verdict "$(md $R/tx1/tx.1.md5)" "$(md $R/run1/rx.1.md5)" "$(md $R/run1/rx.1.rc)")" PASS
    check "files landed (count)" "$(rq "cd $R/dst && find bios ps3 -type f | wc -l")" "5"
    check "image bytes equal" "$(rq "cmp $R/src/ps3/img.iso $R/dst/ps3/img.iso.part && echo same")" same

    # broken 1: no listener at all -- the sender "finishes", nothing arrives
    rq "mkdir -p $R/tx2 && echo ps3/img.iso > $R/tx2/big.list && : > $R/tx2/tar.list &&
        TOW_ROOT=$R/src sh $R/bin/TX_SH $R/tx2 127.0.0.1 47980 > /dev/null 2>&1"
    case "$(tow_verdict "$(md $R/tx2/tx.1.md5)" "$(md $R/nowhere/rx.1.md5)" "$(md $R/nowhere/rx.1.rc)")" in
        FAIL:*) ok "no listener -> FAIL" ;; *) no "no listener read as PASS" ;; esac
    # broken 2: a truncated image (the sender dies at 10 MB)
    rq "mkdir -p $R/run3 $R/tx3 && echo ps3/img.iso > $R/run3/big.list && : > $R/run3/tar.list"
    tow_loop run3 47970 "cd $R/src && head -c 10485760 ps3/img.iso | nc 127.0.0.1 47971"
    rq "cd $R/src && md5sum < ps3/img.iso > $R/tx3/tx.1.md5"
    case "$(tow_verdict "$(md $R/tx3/tx.1.md5)" "$(md $R/run3/rx.1.md5)" "$(md $R/run3/rx.1.rc)")" in
        "FAIL:md5 mismatch") ok "truncated image -> FAIL" ;; *) no "truncated image not caught" ;; esac
    # broken 3: a connection that carries nothing
    rq "mkdir -p $R/run4 && echo ps3/img.iso > $R/run4/big.list && : > $R/run4/tar.list"
    tow_loop run4 47960 "nc 127.0.0.1 47961 < /dev/null"
    case "$(tow_verdict "$(md $R/run4/rx.1.md5)" "$(md $R/run4/rx.1.md5)" 0)" in
        "FAIL:empty stream") ok "empty stream -> FAIL" ;; *) no "empty stream not caught" ;; esac
    rq "rm -rf $R"
fi

echo
if [ "$FAILS" = 0 ]; then echo "PASS ($PASS checks)"; exit 0; fi
echo "FAIL ($FAILS of $((PASS + FAILS)) checks)"; exit 1
