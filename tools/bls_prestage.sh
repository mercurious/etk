#!/bin/bash
# ==========================================================
# BLS PRE-STAGE — the disposable OpenABL harness for a Flip 2 (host-side, OPERATOR-run)
# ==========================================================
# ROCKNIX 20261101 moves the SM8250 from ROCKNIX-ABL (one boot.img in /flash/KERNEL)
# to OpenABL v1.2: a raw PE /flash/KERNEL, the device trees in /flash/dtbs/, and
# Boot Loader Spec entries in /flash/loader/entries/. The stock entry
# (rocknix.conf) names NO devicetree, so on a unit whose "Device model" was never
# set in the Vol-Down menu the first OpenABL boot runs on the firmware's tree:
# a dark panel (plan fact 4). OpenABL honours ONE line we can own,
# /flash/loader/loader.conf `default <entry>`, and an entry's own `devicetree`
# line beats the (unset) model. So an entry PRE-STAGED on a 20261001 rig, before
# the update, is the headless first-boot path: rocknix-abl ignores loader/, the
# updater copies its own files beside ours and removes nothing (fact 5), and
# OpenABL's first boot lands on our entry. Plan: etk-dossiers/BuildPlan_0110_OpenABL_20261030.md
# §3 P3 steps 0-2, §4.
#
# This tool writes ONLY these paths under /flash, and nothing else on the rig:
#   loader/loader.conf                 one line: default etk-stock.conf (or etk-gtk.conf)
#   loader/entries/etk-stock.conf      the STOCK kernel (/KERNEL) + the stock `options`
#                                      line verbatim + `devicetree /dtbs/<model>.dtb`
#                                      (+ sort-key etk-stock: sorts before rocknix.conf
#                                      even if loader.conf is ever lost)
#   loader/entries/etk-gtk.conf  ]     only with --gtk KERNEL.gtk --dtbs <dir>:
#   KERNEL.gtk                   ]     the GTK kernel (PE Image, sha-verified on the
#   dtbs/etk/<model>.dtb         ]     rig) on the kit's spliced tree
# It NEVER touches KERNEL, KERNEL.md5, loader/entries/rocknix.conf, dtbs/*.dtb,
# openabl/ or the abl_a/abl_b slots. /flash is ro: remounted rw for the write,
# ro again on every exit path.
#
#   tools/bls_prestage.sh --plan   [opts] [target]   read the rig, print what WOULD be written; writes nothing
#   tools/bls_prestage.sh          [opts] [target]   write the set above
#   tools/bls_prestage.sh --remove [opts] [target]   delete exactly that set (loader.conf only
#                                                    when it is ours), rmdir loader/ if we made it
#   opts: --car carN            refuse unless the unit reached IS that car (scripts/etk_car.sh)
#         --gtk <KERNEL.gtk>    stage this GTK kernel too (raw PE Image; a boot.img is refused)
#         --dtbs <dir>          the kit DTBs for dtbs/etk/ (must hold <model>.dtb)
#         --default stock|gtk   which entry loader.conf names (default: stock = the stock
#                               kernel on our DTB line, plan §4 "test" mode)
#         --extra "<tokens>"    appended to the stock options for etk-gtk.conf
#                               (default: msm.context_keepalive=1 panic=30)
#         --dtb <file.dtb>      override the model -> DTB file map
#   target: ssh target; default $RIG_SSH (env, then etk.conf), else root@SM8250.local
#
# Works on a 20261001 rig (no /flash/loader yet: mkdir -p; the entry refers to
# /dtbs/<model>.dtb, which the update brings) and on a 20261101 one (the stock
# options line is read from its rocknix.conf, never retyped).
#
# Surface after the update's first cold boot (manual §0 row "pre-staging the
# OpenABL entry"): the panel lights with no model set; Vol-Down menu lists
# ROCKNIX / etk-stock (/ etk-gtk); /proc/cmdline == the staged options line;
# /proc/device-tree/model names the Flip 2. Falsified by a dark first boot that
# the menu's Device model fixes (the entry was not picked), or by a cmdline that
# is not the staged line.
#
# BYTES-TO-ATOMS (manual §1.1): writing /flash is a deploy — the OPERATOR runs
# this (car8 lift access excepted). --plan reads only.
# Remote shell is BusyBox POSIX (manual §Q): no long options, no bashisms.
# Harness: tools/test_bls_prestage.sh (fake ssh sandbox; --against HEAD~ fails).
# ==========================================================
set -u
ETK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8"
# Test-only overrides (the harness points these at a sandbox):
R_FLASH="${BLS_FLASH:-/flash}"; R_PROC="${BLS_PROC:-/proc}"

STOCK_OPTIONS_20261101='boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet rootwait console=tty0 video=efifb:off gpt'
EXTRA_DEFAULT='msm.context_keepalive=1 panic=30'

die()  { echo "BLS_PRESTAGE_FAIL: $*" >&2; exit 1; }
usage() { sed -n '2,52p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

MODE=write; CAR=""; GTK=""; DTBS=""; DEFAULT=stock; EXTRA="$EXTRA_DEFAULT"; DTB_OVERRIDE=""; TARGET=""
while [ $# -gt 0 ]; do
    case "$1" in
        --plan)    MODE=plan; shift ;;
        --remove)  MODE=remove; shift ;;
        --car)     CAR="${2:?--car needs carN}"; shift 2 ;;
        --gtk)     GTK="${2:?--gtk needs a KERNEL.gtk file}"; shift 2 ;;
        --dtbs)    DTBS="${2:?--dtbs needs a directory}"; shift 2 ;;
        --default) DEFAULT="${2:?--default needs stock|gtk}"; shift 2 ;;
        --extra)   EXTRA="${2:?--extra needs a string}"; shift 2 ;;
        --dtb)     DTB_OVERRIDE="${2:?--dtb needs a file name}"; shift 2 ;;
        -h|--help) usage ;;
        -*) die "unknown option $1" ;;
        *) [ -z "$TARGET" ] || die "too many arguments"; TARGET="$1"; shift ;;
    esac
done
case "$DEFAULT" in stock|gtk) ;; *) die "--default takes stock or gtk" ;; esac
if [ -z "$TARGET" ]; then
    TARGET="${RIG_SSH:-}"
    [ -n "$TARGET" ] || [ ! -f "$ETK_ROOT/etk.conf" ] || TARGET=$(sed -n 's/^RIG_SSH="\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "$ETK_ROOT/etk.conf" | head -n1)
    [ -n "$TARGET" ] || TARGET=root@SM8250.local
fi
if [ -z "$CAR" ] && [ -f "$ETK_ROOT/etk.conf" ]; then
    CAR=$(sed -n 's/^CAR="\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "$ETK_ROOT/etk.conf" | head -n1)
fi

# ---- the GTK payload (host-side checks; nothing reaches the rig until the write) -------
if [ -n "$GTK" ] || [ -n "$DTBS" ]; then
    [ -n "$GTK" ] && [ -n "$DTBS" ] || die "--gtk and --dtbs go together (the GTK kernel boots on the kit's tree, never the stock one)"
    [ -f "$GTK" ] || die "no such file: $GTK"
    [ -d "$DTBS" ] || die "no such directory: $DTBS"
    MAGIC=$(head -c 8 "$GTK" | tr -d '\000')
    case "$MAGIC" in
        ANDROID!) die "$GTK is an Android boot.img (20261001 artifact) -- OpenABL loads a raw PE Image; a boot.img is 'not an EFI image' to it" ;;
        MZ*) ;;
        *) die "$GTK does not start with the PE magic MZ (got '$(printf '%s' "$MAGIC" | head -c 4)'); refusing to stage a kernel OpenABL cannot load" ;;
    esac
fi
[ "$DEFAULT" = gtk ] && [ -z "$GTK" ] && die "--default gtk without --gtk: the default would name an entry that is not staged"

car_gate() {   # refuse unless the unit at $TARGET is $CAR (report-only without --car)
    [ -f "$ETK_ROOT/scripts/etk_car.sh" ] || { [ -z "$CAR" ] || die "--car given but scripts/etk_car.sh is missing"; return 0; }
    . "$ETK_ROOT/scripts/etk_car.sh"
    local msg; msg=$(etk_car_verify "$TARGET" "$CAR"); local rc=$?
    echo "$msg"
    [ $rc -eq 0 ] || die "the unit at $TARGET is not ${CAR}"
}
rssh() { ssh $SSH_OPTS "$TARGET" "$@"; }
remote_env() { printf "FLASH='%s' PROC='%s'" "$R_FLASH" "$R_PROC"; }
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }   # single-quote for the remote sh
field() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n1; }

# ---- probe: everything the render needs, read-only ---------------------------------------
REMOTE_PROBE='
echo "model=$(tr -d "\000" < "$PROC/device-tree/model" 2>/dev/null)"
echo "kernel_magic=$(head -c 8 "$FLASH/KERNEL" 2>/dev/null | tr -d "\000" | head -c 8)"
echo "loader_dir=$([ -d "$FLASH/loader" ] && echo present || echo absent)"
echo "entries_dir=$([ -d "$FLASH/loader/entries" ] && echo present || echo absent)"
echo "stock_entry=$([ -f "$FLASH/loader/entries/rocknix.conf" ] && echo present || echo absent)"
echo "stock_options=$(sed -n "s/^options[[:space:]]*//p" "$FLASH/loader/entries/rocknix.conf" 2>/dev/null | head -n1)"
echo "stock_version=$(sed -n "s/^version[[:space:]]*//p" "$FLASH/loader/entries/rocknix.conf" 2>/dev/null | head -n1)"
echo "dtbs_dir=$([ -d "$FLASH/dtbs" ] && echo present || echo absent)"
echo "loader_conf=$(cat "$FLASH/loader/loader.conf" 2>/dev/null | tr "\n" "|")"
echo "have_stock_conf=$([ -f "$FLASH/loader/entries/etk-stock.conf" ] && echo yes || echo no)"
echo "have_gtk_conf=$([ -f "$FLASH/loader/entries/etk-gtk.conf" ] && echo yes || echo no)"
echo "have_kernel_gtk=$([ -f "$FLASH/KERNEL.gtk" ] && echo yes || echo no)"
echo "have_dtbs_etk=$([ -d "$FLASH/dtbs/etk" ] && echo yes || echo no)"
echo "flash_ro=$(awk "\$2==\"$FLASH\"{print \$4; exit}" "$PROC/mounts" 2>/dev/null | grep -q "^ro" && echo yes || echo no)"
echo BLS_PROBE_OK'

car_gate
P=$(rssh "$(remote_env); $REMOTE_PROBE" 2>/dev/null) || die "could not read the unit at $TARGET"
printf '%s\n' "$P" | grep -q '^BLS_PROBE_OK$' || die "probe did not complete on $TARGET"
MODEL=$(field "$P" model); KMAGIC=$(field "$P" kernel_magic)
STOCK_OPTIONS=$(field "$P" stock_options); STOCK_VERSION=$(field "$P" stock_version)
LOADER_CONF=$(field "$P" loader_conf)

case "$KMAGIC" in
    ANDROID!) LAYOUT=20261001 ;;
    MZ*)      LAYOUT=20261101 ;;
    *)        LAYOUT="unknown(magic='$KMAGIC')" ;;
esac
[ "$(field "$P" stock_entry)" = present ] && LAYOUT="$LAYOUT+bls-entries"

# model -> the OS's own DTB file name (plan fact 2: confirmed from the 20261101 image's dtbs/)
if [ -n "$DTB_OVERRIDE" ]; then DTB="$DTB_OVERRIDE"
else
    case "$MODEL" in
        "Retroid Pocket Flip2 Visionox") DTB=sm8250-retroidpocket-flip2-visionox.dtb ;;
        "Retroid Pocket Flip2")          DTB=sm8250-retroidpocket-flip2.dtb ;;
        *) die "unknown model '$MODEL' -- not a Flip 2 this tool knows; pass --dtb <file> if you are sure" ;;
    esac
fi
if [ -n "$STOCK_OPTIONS" ]; then OPT_SRC="rig's rocknix.conf (verbatim)"
else STOCK_OPTIONS="$STOCK_OPTIONS_20261101"; OPT_SRC="20261101 text (no rocknix.conf on the rig)"; fi
[ -n "$STOCK_VERSION" ] || STOCK_VERSION=etk-prestage

# ---- the owned set, rendered on the host ---------------------------------------------------
STOCK_CONF=$(printf 'title      ROCKNIX (stock kernel, ETK DTB line)\nversion    %s\nlinux      /KERNEL\ndevicetree /dtbs/%s\noptions    %s\nsort-key   etk-stock\n' "$STOCK_VERSION" "$DTB" "$STOCK_OPTIONS")
GTK_CONF=""
[ -n "$GTK" ] && GTK_CONF=$(printf 'title      ROCKNIX-GTK\nversion    %s\nlinux      /KERNEL.gtk\ndevicetree /dtbs/etk/%s\noptions    %s %s\n' "$(basename "$GTK")" "$DTB" "$STOCK_OPTIONS" "$EXTRA")
case "$DEFAULT" in stock) DEFAULT_LINE='default etk-stock.conf' ;; gtk) DEFAULT_LINE='default etk-gtk.conf' ;; esac
OWNED_LC() {   # is the rig's loader.conf absent or ours (exactly one default line naming one of our entries)?
    case "$1" in ""|"default etk-stock.conf|"|"default etk-gtk.conf|") return 0 ;; *) return 1 ;; esac
}

echo "unit     : $TARGET  model '$MODEL'  layout $LAYOUT  /flash ro=$(field "$P" flash_ro)"
echo "stock DTB: /dtbs/$DTB  ($([ "$(field "$P" dtbs_dir)" = present ] && echo "dtbs/ present" || echo "dtbs/ absent -- the 20261101 update brings it"))"
echo "options  : $OPT_SRC"
echo "already  : etk-stock.conf=$(field "$P" have_stock_conf) etk-gtk.conf=$(field "$P" have_gtk_conf) KERNEL.gtk=$(field "$P" have_kernel_gtk) dtbs/etk=$(field "$P" have_dtbs_etk) loader.conf='$(printf '%s' "$LOADER_CONF" | tr '|' ' ' | sed 's/ $//')'"

# ---- --remove: exactly the owned set ----------------------------------------------------
if [ "$MODE" = remove ]; then
    OWNED_LC "$LOADER_CONF" || die "loader.conf on the rig is not ours ('$(printf '%s' "$LOADER_CONF" | tr '|' ' ')'); nothing removed -- someone else owns that line"
    REMOTE_REMOVE='
set -u
RW=0; done_ro() { [ "$RW" = 1 ] && { sync; mount -o remount,ro "$FLASH" 2>/dev/null; RW=0; }; }
trap done_ro EXIT
mount -o remount,rw "$FLASH" 2>/dev/null && RW=1
for f in loader/loader.conf loader/entries/etk-stock.conf loader/entries/etk-gtk.conf KERNEL.gtk; do
  if [ -f "$FLASH/$f" ]; then rm -f "$FLASH/$f" && echo "removed $f"; fi
done
if [ -d "$FLASH/dtbs/etk" ]; then rm -rf "$FLASH/dtbs/etk" && echo "removed dtbs/etk/"; fi
rmdir "$FLASH/dtbs" 2>/dev/null && echo "removed dtbs/ (empty: we made it -- on 20261101 the stock DTBs keep it)"
rmdir "$FLASH/loader/entries" 2>/dev/null && echo "removed loader/entries/ (empty: we made it)"
rmdir "$FLASH/loader" 2>/dev/null && echo "removed loader/ (empty: we made it)"
for f in loader/loader.conf loader/entries/etk-stock.conf loader/entries/etk-gtk.conf KERNEL.gtk dtbs/etk; do
  [ -e "$FLASH/$f" ] && { echo "STILL PRESENT: $f"; exit 1; }
done
sync; echo BLS_REMOVE_OK'
    OUT=$(rssh "$(remote_env); $REMOTE_REMOVE" 2>&1) || { printf '%s\n' "$OUT"; die "remove did not complete on $TARGET"; }
    printf '%s\n' "$OUT" | grep -v '^BLS_REMOVE_OK$' | sed 's/^/  /'
    printf '%s\n' "$OUT" | grep -q '^BLS_REMOVE_OK$' || die "remove did not confirm"
    echo "BLS_PRESTAGE_REMOVED unit=$TARGET -- KERNEL, rocknix.conf, dtbs/*.dtb untouched; the rig boots as ROCKNIX ships it"
    exit 0
fi

# ---- plan / write -----------------------------------------------------------------------
OWNED_LC "$LOADER_CONF" || die "loader.conf on the rig is not ours ('$(printf '%s' "$LOADER_CONF" | tr '|' ' ')'); refusing to take over that line"
if [ -n "$GTK" ]; then
    [ -f "$DTBS/$DTB" ] || die "$DTBS has no $DTB for model '$MODEL' -- the GTK entry would point at nothing"
    GTK_SHA=$(sha256sum "$GTK" | cut -d' ' -f1)
fi

echo "would write under $R_FLASH:" | sed "s/^would/$([ "$MODE" = plan ] && echo would || echo will)/"
echo "  loader/loader.conf"
printf '%s\n' "$DEFAULT_LINE" | sed 's/^/    | /'
echo "  loader/entries/etk-stock.conf"
printf '%s\n' "$STOCK_CONF" | sed 's/^/    | /'
if [ -n "$GTK" ]; then
    echo "  loader/entries/etk-gtk.conf"
    printf '%s\n' "$GTK_CONF" | sed 's/^/    | /'
    echo "  KERNEL.gtk  <- $GTK  ($(wc -c < "$GTK") B, sha256 $GTK_SHA)"
    echo "  dtbs/etk/   <- $DTBS/*.dtb  ($(ls "$DTBS"/*.dtb 2>/dev/null | wc -l | tr -d ' ') files; the entry names $DTB)"
fi
echo "never touched: KERNEL, KERNEL.md5, loader/entries/rocknix.conf, dtbs/*.dtb, openabl/, abl_a, abl_b"
if [ "$MODE" = plan ]; then
    echo "BLS_PRESTAGE_PLAN unit=$TARGET model='$MODEL' layout=$LAYOUT default=$DEFAULT gtk=$([ -n "$GTK" ] && echo yes || echo no) -- nothing written"
    exit 0
fi

# Stage dir on the host -> one tar stream -> extracted into $FLASH/etk-prestage.tmp on the
# rig, sha-checked there, then each path moved into place (a rename on the same FAT).
TD=$(mktemp -d); trap 'rm -rf "$TD"' EXIT
mkdir -p "$TD/stage/loader/entries"
printf '%s\n' "$DEFAULT_LINE" > "$TD/stage/loader/loader.conf"
printf '%s\n' "$STOCK_CONF" > "$TD/stage/loader/entries/etk-stock.conf"
if [ -n "$GTK" ]; then
    printf '%s\n' "$GTK_CONF" > "$TD/stage/loader/entries/etk-gtk.conf"
    cp "$GTK" "$TD/stage/KERNEL.gtk"
    mkdir -p "$TD/stage/dtbs/etk"; cp "$DTBS"/*.dtb "$TD/stage/dtbs/etk/"
fi
(cd "$TD/stage" && find . -type f ! -name SHA256SUMS | sed 's|^\./||' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS)
HAVE_GTK=$([ -n "$GTK" ] && echo 1 || echo 0)
REMOTE_WRITE='
set -u
TMP="$FLASH/etk-prestage.tmp"
RW=0; cleanup() { rm -rf "$TMP" 2>/dev/null; [ "$RW" = 1 ] && { sync; mount -o remount,ro "$FLASH" 2>/dev/null; RW=0; }; }
trap cleanup EXIT
mount -o remount,rw "$FLASH" 2>/dev/null && RW=1
rm -rf "$TMP"; mkdir -p "$TMP" || { echo "cannot create $TMP (is /flash writable?)"; exit 1; }
tar -xf - -C "$TMP" || { echo "tar extract failed"; exit 1; }
( cd "$TMP" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) || { echo "sha256 mismatch after transfer"; exit 1; }
mkdir -p "$FLASH/loader/entries" || { echo "mkdir loader/entries failed"; exit 1; }
mv -f "$TMP/loader/loader.conf" "$FLASH/loader/loader.conf" || exit 1
mv -f "$TMP/loader/entries/etk-stock.conf" "$FLASH/loader/entries/etk-stock.conf" || exit 1
if [ "$HAVE_GTK" = 1 ]; then
  mkdir -p "$FLASH/dtbs" || exit 1
  rm -rf "$FLASH/dtbs/etk"; mv "$TMP/dtbs/etk" "$FLASH/dtbs/etk" || exit 1
  mv -f "$TMP/KERNEL.gtk" "$FLASH/KERNEL.gtk" || exit 1
  mv -f "$TMP/loader/entries/etk-gtk.conf" "$FLASH/loader/entries/etk-gtk.conf" || exit 1
  echo "kernel_gtk_sha=$(sha256sum "$FLASH/KERNEL.gtk" | cut -d" " -f1)"
fi
sync
echo "loader_conf=$(cat "$FLASH/loader/loader.conf" | tr "\n" "|")"
echo "stock_conf_sha=$(sha256sum "$FLASH/loader/entries/etk-stock.conf" | cut -d" " -f1)"
echo "kernel_sha=$(sha256sum "$FLASH/KERNEL" 2>/dev/null | cut -d" " -f1)"
echo "stock_entry_sha=$(sha256sum "$FLASH/loader/entries/rocknix.conf" 2>/dev/null | cut -d" " -f1)"
echo BLS_WRITE_OK'
BEFORE=$(rssh "echo kernel_sha=\$(sha256sum $(sq "$R_FLASH/KERNEL") 2>/dev/null | cut -d' ' -f1); echo stock_entry_sha=\$(sha256sum $(sq "$R_FLASH/loader/entries/rocknix.conf") 2>/dev/null | cut -d' ' -f1)" 2>/dev/null)
OUT=$(tar -C "$TD/stage" -cf - . | rssh "$(remote_env) HAVE_GTK='$HAVE_GTK'; $REMOTE_WRITE" 2>&1) || { printf '%s\n' "$OUT" | sed 's/^/  /'; die "write did not complete on $TARGET (/flash remounted ro, temp removed)"; }
printf '%s\n' "$OUT" | grep -q '^BLS_WRITE_OK$' || { printf '%s\n' "$OUT" | sed 's/^/  /'; die "write did not confirm"; }

# read-back on the host side: the text is ours byte for byte, the kernel/stock entry untouched
WANT_STOCK_SHA=$(printf '%s\n' "$STOCK_CONF" | sha256sum | cut -d' ' -f1)
[ "$(field "$OUT" stock_conf_sha)" = "$WANT_STOCK_SHA" ] || die "etk-stock.conf read-back differs from the rendered text"
[ "$(field "$OUT" loader_conf)" = "$DEFAULT_LINE|" ] || die "loader.conf read-back is '$(field "$OUT" loader_conf)', wanted '$DEFAULT_LINE'"
if [ -n "$GTK" ]; then [ "$(field "$OUT" kernel_gtk_sha)" = "$GTK_SHA" ] || die "KERNEL.gtk read-back sha mismatch"; fi
for k in kernel_sha stock_entry_sha; do
    [ "$(field "$BEFORE" $k)" = "$(field "$OUT" $k)" ] || die "$k changed under us (before '$(field "$BEFORE" $k)' after '$(field "$OUT" $k)') -- KERNEL/rocknix.conf must never move; investigate before booting"
done
echo "BLS_PRESTAGE_OK unit=$TARGET model='$MODEL' layout=$LAYOUT default=$DEFAULT_LINE gtk=$([ -n "$GTK" ] && echo "$GTK_SHA" || echo none) stock_entry_sha=$WANT_STOCK_SHA kernel_untouched=yes"
echo "next: the operator runs the ROCKNIX update (or cold boots a 20261101 rig). Surface: panel lit with NO model set; Vol-Down menu shows etk-stock$([ -n "$GTK" ] && echo ' + etk-gtk'); /proc/cmdline == the options line above. --remove undoes exactly this."
