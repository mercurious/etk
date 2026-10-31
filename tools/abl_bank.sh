#!/bin/bash
# ==========================================================
# ABL BANK — read-only copy of a Flip 2's abl_a / abl_b bootloader slots (host-side)
# ==========================================================
# The ROCKNIX 20261101 update flashes OpenABL over BOTH abl_a and abl_b
# (`updateabl`: sha the slots against the shipped ELF, `dd` to both, NO backup;
# plan fact 5/8). The only way back to the ABL a unit runs today is a copy taken
# before the update. This tool is that copy: it `dd`s each slot by PARTLABEL
# over ssh into state/abl/<car>/, hashes it on BOTH ends (the rig's sha256sum of
# the device vs the host's sha256sum of the file), and writes a README naming
# the source device, the unit and the date. Compare: the 2026-10-07 car12 bank in
# state/flip2-12gb-abl-backup/ (taken from Android with Retroid's backup_abl.sh).
#
#   tools/abl_bank.sh [--car carN] [--dir <dir>] [target]
#     --car carN   refuse unless the unit reached IS that car (scripts/etk_car.sh); also names
#                  the bank dir (default: CAR from etk.conf, else the unit's own /storage/.etk/car)
#     --dir <dir>  bank here instead of state/abl/<car>/ (a dir that already holds an
#                  abl_a.img is refused: a bank is never overwritten)
#     target       ssh target; default $RIG_SSH (env, then etk.conf), else root@SM8250.local
#
# THIS TOOL WRITES NOTHING ON THE RIG. Every remote command is a read (readlink,
# cat of sysfs, dd if=, sha256sum); the tool refuses to run if its own remote body
# ever gains a write (self-check below). Restoring a slot is NOT this tool's job
# — that is `dd of=` into a bootloader partition, a brick-class write for the
# operator's hand, from Android (Retroid's restore_backup_abl.sh) or EDL.
#
# Remote shell is BusyBox POSIX (manual §Q). Harness: tools/test_bls_prestage.sh
# (abl_bank section: fake sysfs + device files; --against HEAD~ fails).
# ==========================================================
set -u
ETK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8"
# Test-only overrides (the harness points these at a sandbox):
R_SYSBLK="${ABL_BANK_SYSBLK:-/sys/class/block}"; R_DEV="${ABL_BANK_DEV:-/dev}"

die()  { echo "ABL_BANK_FAIL: $*" >&2; exit 1; }
usage() { sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

CAR=""; DIR=""; TARGET=""
while [ $# -gt 0 ]; do
    case "$1" in
        --car) CAR="${2:?--car needs carN}"; shift 2 ;;
        --dir) DIR="${2:?--dir needs a directory}"; shift 2 ;;
        -h|--help) usage ;;
        -*) die "unknown option $1" ;;
        *) [ -z "$TARGET" ] || die "too many arguments"; TARGET="$1"; shift ;;
    esac
done
if [ -z "$TARGET" ]; then
    TARGET="${RIG_SSH:-}"
    [ -n "$TARGET" ] || [ ! -f "$ETK_ROOT/etk.conf" ] || TARGET=$(sed -n 's/^RIG_SSH="\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "$ETK_ROOT/etk.conf" | head -n1)
    [ -n "$TARGET" ] || TARGET=root@SM8250.local
fi
[ -n "$CAR" ] || [ ! -f "$ETK_ROOT/etk.conf" ] || CAR=$(sed -n 's/^CAR="\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "$ETK_ROOT/etk.conf" | head -n1)

rssh() { ssh $SSH_OPTS "$TARGET" "$@"; }
field() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n1; }

# ---- the remote bodies: READS ONLY ---------------------------------------------------------
# resolve <partlabel> -> device node + size, via the by-partlabel symlink, else sysfs PARTNAME
REMOTE_RESOLVE='
name="$1"
dev=$(readlink -f "$DEV/disk/by-partlabel/$name" 2>/dev/null)
if [ -z "$dev" ] || ! { [ -b "$dev" ] || { [ "$DEV" != /dev ] && [ -f "$dev" ]; }; }; then
  dev=""
  for u in "$SYSBLK"/*/uevent; do
    if grep -q "^PARTNAME=$name\$" "$u" 2>/dev/null; then dev="$DEV/$(sed -n "s/^DEVNAME=//p" "$u" | head -n1)"; break; fi
  done
fi
is_dev() { [ -b "$1" ] || { [ "$DEV" != /dev ] && [ -f "$1" ]; }; }   # the -f arm only under the harness override
[ -n "$dev" ] && is_dev "$dev" || { echo "dev="; exit 0; }
base=${dev##*/}
echo "dev=$dev"
echo "sectors=$(cat "$SYSBLK/$base/size" 2>/dev/null)"
echo "parent=$(readlink -f "$SYSBLK/$base/.." 2>/dev/null | sed "s|.*/||")"
echo "sha=$(sha256sum "$dev" 2>/dev/null | cut -d" " -f1)"'
REMOTE_IDENT='
echo "model=$(tr -d "\000" < /proc/device-tree/model 2>/dev/null)"
echo "car=$(cat /storage/.etk/car 2>/dev/null | head -n1)"
echo "os=$(sed -n "s/^OS_VERSION=//p" /etc/os-release 2>/dev/null | head -n1)"
echo "build=$(sed -n "s/^BUILD_ID=//p" /etc/os-release 2>/dev/null | head -n1)"
echo "kernel_magic=$(head -c 8 /flash/KERNEL 2>/dev/null | tr -d "\000" | head -c 8)"
echo "host=$(hostname 2>/dev/null)"'
# self-check: no write can hide in the remote bodies (dd of=, redirects into files, mount, rm, mv, cp, tee)
for body in "$REMOTE_RESOLVE" "$REMOTE_IDENT"; do
    printf '%s\n' "$body" | sed 's|[0-9]*>/dev/null||g' | grep -qE '(^|[^a-z])(of=|mount|rm |rm$|mv |cp |tee |dd |>)' && die "self-check: a remote body contains a write -- refusing to run"
done

# ---- car gate (refuse the wrong unit; report-only without --car) ----------------------------
if [ -f "$ETK_ROOT/scripts/etk_car.sh" ]; then
    . "$ETK_ROOT/scripts/etk_car.sh"
    msg=$(etk_car_verify "$TARGET" "$CAR"); rc=$?
    echo "$msg"; [ $rc -eq 0 ] || die "the unit at $TARGET is not ${CAR:-reachable}"
fi
ID=$(rssh "$REMOTE_IDENT" 2>/dev/null) || die "could not read the unit at $TARGET"
[ -n "$CAR" ] || CAR=$(field "$ID" car)
[ -n "$CAR" ] || CAR="unassigned-$(field "$ID" host | sed 's/[^A-Za-z0-9._-]//g')"
[ -n "$DIR" ] || DIR="$ETK_ROOT/state/abl/$CAR"
[ ! -e "$DIR/abl_a.img" ] || die "$DIR already holds an abl_a.img -- a bank is never overwritten; pass --dir $DIR/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DIR" || die "cannot create $DIR"
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "unit : $TARGET  model '$(field "$ID" model)'  ROCKNIX $(field "$ID" os) ($(field "$ID" build))  /flash/KERNEL magic '$(field "$ID" kernel_magic)'"
echo "bank : $DIR"
: > "$DIR/SHA256SUMS"
{
    echo "ABL BANK -- read-only copy of the bootloader slots, taken by tools/abl_bank.sh"
    echo "date      : $STAMP"
    echo "unit      : $TARGET ($CAR), model '$(field "$ID" model)', host $(field "$ID" host)"
    echo "rocknix   : $(field "$ID" os) build $(field "$ID" build); /flash/KERNEL magic '$(field "$ID" kernel_magic)' ($(case "$(field "$ID" kernel_magic)" in ANDROID!) echo "boot.img: rocknix-abl era, 20261001";; MZ*) echo "PE Image: OpenABL era, 20261101+";; *) echo unknown;; esac))"
    echo "how       : ssh \$TARGET dd if=<device> bs=1M (device resolved by PARTLABEL on the rig); sha256 on the rig (device) == sha256 here (file)"
    echo "restore   : NOT from this tool. dd of= into abl_a/abl_b is a brick-class write: Retroid's restore_backup_abl.sh from Android, or EDL (9008)."
} > "$DIR/README"
for slot in abl_a abl_b; do
    R=$(rssh "set -- $slot; SYSBLK='$R_SYSBLK' DEV='$R_DEV'; $REMOTE_RESOLVE" 2>/dev/null) || die "could not resolve $slot on $TARGET"
    DEV=$(field "$R" dev); [ -n "$DEV" ] || die "no partition labelled $slot on $TARGET (by-partlabel and sysfs PARTNAME both empty)"
    SECTORS=$(field "$R" sectors); RSHA=$(field "$R" sha)
    [ -n "$RSHA" ] || die "the rig could not hash $DEV (not root, or the device is unreadable)"
    rssh "dd if='$DEV' bs=1048576 2>/dev/null" > "$DIR/$slot.img" || die "dd of $slot failed"
    HSHA=$(sha256sum "$DIR/$slot.img" | cut -d' ' -f1)
    [ "$HSHA" = "$RSHA" ] || { rm -f "$DIR/$slot.img"; die "$slot: host sha $HSHA != rig sha $RSHA (a torn read; nothing kept)"; }
    SIZE=$(wc -c < "$DIR/$slot.img")
    [ -z "$SECTORS" ] || [ "$SIZE" = "$((SECTORS * 512))" ] || echo "  note: $slot read $SIZE B but sysfs says $((SECTORS * 512)) B (4Kn sector math) -- the sha agreement above is the verdict"
    printf '%s  %s.img\n' "$HSHA" "$slot" >> "$DIR/SHA256SUMS"
    echo "$slot     : $DEV (on $(field "$R" parent)), $SIZE B, sha256 $HSHA  -> $DIR/$slot.img" | tee -a "$DIR/README"
done
A=$(sed -n 's/  abl_a.img$//p' "$DIR/SHA256SUMS"); B=$(sed -n 's/  abl_b.img$//p' "$DIR/SHA256SUMS")
if [ "$A" = "$B" ]; then echo "slots     : abl_a == abl_b (the normal state: ROCKNIX flashes both)" | tee -a "$DIR/README"
else echo "slots     : abl_a != abl_b -- the two slots DIFFER; keep both, name it in the dossier" | tee -a "$DIR/README"; fi
chmod a-w "$DIR/abl_a.img" "$DIR/abl_b.img" "$DIR/SHA256SUMS"
echo "ABL_BANK_OK car=$CAR dir=$DIR a=$A b=$B rig_writes=none"
echo "check     : (cd $DIR && sha256sum -c SHA256SUMS)"
