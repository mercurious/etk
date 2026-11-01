#!/bin/bash
# ==========================================================
# BLS TRIAL — the two OpenABL fallback trials on a Flip 2 (host-side, OPERATOR-run)
# ==========================================================
# Plan: etk-dossiers/BuildPlan_0110_OpenABL_20261030.md §3 P3 step 2. Both trials need a
# rig that already carries the pre-staged set (tools/bls_prestage.sh --gtk) on 20261101.
#
#   tools/bls_trial.sh status            read only: entry booted, default, entries, trial
#   tools/bls_trial.sh fallthrough       default etk-gtk + KERNEL.gtk moved aside, so the
#                                        entry fails to LOAD and Start must try the next one
#   tools/bls_trial.sh count [N]         default etk-gtk + etk-gtk.conf -> etk-gtk+N.conf
#                                        (N 1-9, default 1): OpenABL renames it before each
#                                        boot; at +0-N the entry is bad and is skipped
#   tools/bls_trial.sh restore           undo either trial; the default returns to stock
#
#   --car <name>   refuse unless the unit is this car (default: etk.conf CAR=)
#   [target]       ssh target (default: RIG_SSH, then etk.conf RIG_SSH=)
#
# THE COUNT NEEDS NO POWER CUT: nothing on the rig blesses a boot (no systemd-bless-boot), so
# every ordinary boot, good or bad, spends one try. N plain power-ons run it out, and boot
# N+1 is the proof, so one try proves the mechanism. The kit must bless a good boot before it can ship a counted entry.
#
# It writes ONLY: KERNEL.gtk <-> KERNEL.gtk.aside, etk-gtk.conf <-> etk-gtk+*.conf, and the
# one loader.conf line. KERNEL, rocknix.conf and etk-stock.conf are hashed before and after,
# and it refuses to run a second trial on top of a first. A foreign loader.conf is refused.
#
# BYTES-TO-ATOMS: every verb but `status` writes /flash on a rig: the OPERATOR runs it.
# Remote shell is BusyBox POSIX (manual §Q). Harness: tools/test_bls_trial.sh.
# ==========================================================
set -u
ETK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8"
# Test-only overrides (the harness points these at a sandbox):
R_FLASH="${BLS_FLASH:-/flash}"; R_PROC="${BLS_PROC:-/proc}"

die()   { echo "BLS_TRIAL_FAIL: $*" >&2; exit 1; }
usage() { sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

VERB=""; N=1; CAR=""; TARGET=""
while [ $# -gt 0 ]; do
    case "$1" in
        --car)   CAR="${2:?--car needs a name}"; shift 2 ;;
        -h|--help) usage ;;
        -*)      die "unknown option: $1" ;;
        status|fallthrough|restore) [ -z "$VERB" ] || die "one verb at a time"; VERB="$1"; shift ;;
        count)   [ -z "$VERB" ] || die "one verb at a time"; VERB=count; shift
                 case "${1:-}" in [0-9]*) N="$1"; shift ;; esac ;;
        *)       [ -z "$TARGET" ] || die "too many arguments"; TARGET="$1"; shift ;;
    esac
done
[ -n "$VERB" ] || usage
case "$N" in [1-9]) ;; *) die "count takes 1-9 tries, got '$N'" ;; esac

if [ -z "$TARGET" ]; then
    TARGET="${RIG_SSH:-}"
    [ -n "$TARGET" ] || [ ! -f "$ETK_ROOT/etk.conf" ] || TARGET=$(sed -n 's/^RIG_SSH="\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "$ETK_ROOT/etk.conf" | head -n1)
    [ -n "$TARGET" ] || die "no target: pass one, or set RIG_SSH in etk.conf"
fi
if [ -z "$CAR" ] && [ -f "$ETK_ROOT/etk.conf" ]; then
    CAR=$(sed -n 's/^CAR="\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "$ETK_ROOT/etk.conf" | head -n1)
fi

rssh() { ssh $SSH_OPTS "$TARGET" "$@"; }
remote_env() { printf "FLASH='%s' PROC='%s'" "$R_FLASH" "$R_PROC"; }

# Report-only without a car; with one, the unit must prove it is that car.
if [ -n "$CAR" ] && [ -f "$ETK_ROOT/scripts/etk_car.sh" ]; then
    . "$ETK_ROOT/scripts/etk_car.sh"
    msg=$(etk_car_verify "$TARGET" "$CAR"); rc=$?
    echo "$msg"
    [ $rc -eq 0 ] || die "the unit at $TARGET is not ${CAR}"
fi

# ---- the read: everything status prints, and the read-back after a write ---------------
REMOTE_READ='
E="$FLASH/loader/entries"
if grep -q "msm.context_keepalive=1" "$PROC/cmdline" 2>/dev/null; then
  echo "booted   : etk-gtk (keepalive on the cmdline)"
else
  echo "booted   : stock kernel (etk-stock or rocknix.conf: one options line, cannot tell which)"
fi
echo "default  : $(cat "$FLASH/loader/loader.conf" 2>/dev/null | tr "\n" " ")"
TRIAL=none
[ -f "$FLASH/KERNEL.gtk.aside" ] && TRIAL=fallthrough
for f in "$E"/*.conf; do
  [ -f "$f" ] || continue
  n=${f##*/}; note=""
  case "$n" in
    *+0-*.conf)  note="  <- out of tries: bad, skipped"; TRIAL=count ;;
    *+*.conf)    note="  <- counting"; TRIAL=count ;;
  esac
  echo "entry    : $n$note"
done
if [ -f "$FLASH/KERNEL.gtk" ]; then echo "kernel   : KERNEL.gtk present"; else echo "kernel   : KERNEL.gtk ABSENT"; fi
echo "trial    : $TRIAL"'

if [ "$VERB" = status ]; then
    rssh "$(remote_env); $REMOTE_READ" || die "could not read the unit at $TARGET"
    exit 0
fi

# ---- the write ---------------------------------------------------------------------------
REMOTE_WRITE='
E="$FLASH/loader/entries"; LC="$FLASH/loader/loader.conf"
refuse() { echo "refused: $*"; exit 3; }
hashes() { sha256sum "$FLASH/KERNEL" "$E/rocknix.conf" "$E/etk-stock.conf" 2>/dev/null | cut -d" " -f1 | tr "\n" " "; }

[ "$(head -c 2 "$FLASH/KERNEL" 2>/dev/null)" = MZ ] || refuse "KERNEL is not a PE image: this rig is not on 20261101"
[ -f "$E/rocknix.conf" ] || refuse "no rocknix.conf: this rig is not on OpenABL"
[ -f "$E/etk-stock.conf" ] || refuse "no etk-stock.conf: run tools/bls_prestage.sh first (it is the fallback these trials land on)"
case "$(cat "$LC" 2>/dev/null)" in
  "default etk-stock.conf"|"default etk-gtk.conf") ;;
  *) refuse "loader.conf is not ours; refusing to take over that line" ;;
esac
COUNTED=""
for f in "$E"/etk-gtk+*.conf; do [ -f "$f" ] && COUNTED="$COUNTED $f"; done
ASIDE=no; [ -f "$FLASH/KERNEL.gtk.aside" ] && ASIDE=yes
case "$VERB" in
  fallthrough|count)
    [ -z "$COUNTED" ] && [ "$ASIDE" = no ] || refuse "a trial is already in place: run restore first"
    [ -f "$FLASH/KERNEL.gtk" ] || refuse "no KERNEL.gtk on the rig"
    [ -f "$E/etk-gtk.conf" ] || refuse "no etk-gtk.conf on the rig"
    ;;
  restore)
    set -- $COUNTED
    [ $# -le 1 ] || refuse "more than one counted etk-gtk entry:$COUNTED"
    [ -z "$COUNTED" ] || [ ! -f "$E/etk-gtk.conf" ] || refuse "both etk-gtk.conf and$COUNTED exist"
    [ "$ASIDE" = no ] || [ ! -f "$FLASH/KERNEL.gtk" ] || refuse "both KERNEL.gtk and KERNEL.gtk.aside exist"
    ;;
esac

BEFORE=$(hashes)
RW=0; done_rw() { [ "$RW" = 1 ] && { sync; mount -o remount,ro "$FLASH" 2>/dev/null; RW=0; }; }
trap done_rw EXIT
mount -o remount,rw "$FLASH" || { echo "cannot remount $FLASH rw"; exit 1; }
RW=1
set_default() { printf "default %s\n" "$1" > "$LC.new" && mv -f "$LC.new" "$LC"; }
case "$VERB" in
  fallthrough) mv "$FLASH/KERNEL.gtk" "$FLASH/KERNEL.gtk.aside" || exit 1
               set_default etk-gtk.conf || exit 1 ;;
  count)       mv "$E/etk-gtk.conf" "$E/etk-gtk+$N.conf" || exit 1
               set_default etk-gtk.conf || exit 1 ;;
  restore)     [ -z "$COUNTED" ] || mv $COUNTED "$E/etk-gtk.conf" || exit 1
               [ "$ASIDE" = no ] || mv "$FLASH/KERNEL.gtk.aside" "$FLASH/KERNEL.gtk" || exit 1
               set_default etk-stock.conf || exit 1 ;;
esac
done_rw
[ "$(hashes)" = "$BEFORE" ] || { echo "KERNEL, rocknix.conf or etk-stock.conf CHANGED"; exit 1; }
echo BLS_WRITE_OK'

OUT=$(rssh "$(remote_env) VERB='$VERB' N='$N'; $REMOTE_WRITE" 2>&1); RC=$?
printf '%s\n' "$OUT" | sed 's/^/  /'
[ $RC -eq 0 ] || die "$VERB did not complete"
printf '%s\n' "$OUT" | grep -q '^BLS_WRITE_OK$' || die "$VERB did not confirm"
rssh "$(remote_env); $REMOTE_READ" || die "could not read the unit back"

case "$VERB" in
    fallthrough) cat <<EOF

BLS_TRIAL_OK fallthrough. Power off, then a plain power-on (no Vol-Down).
PASS: it reaches ES on the stock kernel: the failed entry fell through.
FAIL: an error or a dark panel. Vol-Down, pick the stock entry, then tools/bls_trial.sh restore.
EOF
        ;;
    count) cat <<EOF

BLS_TRIAL_OK count $N. Each plain power-on spends a try: $N boot GTK, boot $((N + 1)) boots stock.
Check with tools/bls_trial.sh status after each. tools/bls_trial.sh restore ends it.
EOF
        ;;
    restore) cat <<EOF

BLS_TRIAL_OK restore. Default is etk-stock; the next plain power-on boots the stock kernel.
EOF
        ;;
esac
