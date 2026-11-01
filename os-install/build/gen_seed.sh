#!/bin/bash
# ==========================================================================
# gen_seed.sh — render the card image's hostless seed FROM THE KIT (0.11.0)
# --------------------------------------------------------------------------
# The GTK card image never runs install.sh: /flash/mount-storage.sh copies
# $ETK_ROOT/.seed_config into /storage/.config on boot 2 and systemd starts
# whatever is in it. Until 0.11.0 that seed was a STATIC SNAPSHOT of one rig's
# .config (os-install/build/seed_config, gitignored, 2026-07/10), so every unit
# install.sh grew after the snapshot — the internal-mic UCM bind, the USB-net
# heal, USB/thermal wake off, the mDNS heal, the battery guard, the UFS pin,
# Pitlink USB, the SD rebind — was absent on a card until the user ran the
# one-liner (v0.10.0 card walk, car12 2026-10-23: mic dead, USB link
# carrier-less, `etk-sd-rebind.service not-found` on every boot).
#
# This script renders the seed at image-build time from the SAME SOURCES
# install.sh deploys, so the card and a host install cannot drift apart:
#   * every unit / boot script install.sh writes with `cat << 'MARK' > PATH`
#     under /storage/.config (the heredoc bodies, extracted by their target
#     path — the same trick tools/test_kernel_abl.sh and tools/test_usbnet_heal.sh
#     use to run install.sh's rig bodies in a sandbox);
#   * the files install.sh ships from config/ (etk-gtk-version.service — left
#     with its @PLACEHOLDERS@ for the image lane's boot-identity render; the
#     etk-ucm unit, bind script and the Flip 2 HiFi-RP.conf overlay; the two
#     WirePlumber S16 pins the mic depends on; MangoHud.conf);
#   * the wants-links install.sh enables, derived from each unit's own
#     [Install] WantedBy= (absolute /storage/.config/system.d/<unit> targets,
#     the form the real seed used and the lane's prune resolves), plus the
#     basic.target.wants link STEP 6.4 makes for the boot-identity unit;
#   * the battery guard's conf with install.sh's defaults.
# Deliberately NOT seeded: the Pitlink switch (095-etk-pitlink — the unit is
# enabled but ConditionPathExists keeps it off until TOOLS > Pitlink), RPCS3
# env flags (096, operator-only), OS links the old snapshot carried from its
# rig's settings (sshd/simple-http-server: inert — ROCKNIX gates sshd on
# ssh.enabled in system.cfg, 2026-10-23), and anything install.sh writes
# outside /storage/.config (the kit tree itself rides in $ETK_ROOT).
# The mako style, the shipped Turnip dial and the boot-identity render +
# dangling-link prune stay in build_gtk_image_v2.sh (they always were there;
# tools/test_boot_identity.sh pins them by marker).
#
# Usage: gen_seed.sh --repo <etk checkout> --out <dir>   [--quiet]
#   writes <dir>/{system.d,custom_scripts,profile.d,etk-ucm,wireplumber,...}
#   and prints one "seed: ..." line per class; exit 1 with "SEED_FAIL ..." on
#   any miss (a unit install.sh writes that this script cannot place is a
#   bug here, never a card that quietly ships without it).
# Harness: tools/test_seed_gen.sh (unit set == install.sh's; links resolve;
# no placeholder survives once the lane's render ran; --against discriminates).
# ==========================================================================
set -u
REPO=""; OUT=""; QUIET=0
while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO="${2:?}"; shift 2 ;;
        --out)  OUT="${2:?}"; shift 2 ;;
        --quiet) QUIET=1; shift ;;
        *) echo "usage: $0 --repo <etk checkout> --out <seed dir> [--quiet]" >&2; exit 2 ;;
    esac
done
[ -n "$REPO" ] && [ -n "$OUT" ] || { echo "usage: $0 --repo <etk checkout> --out <seed dir> [--quiet]" >&2; exit 2; }
INST="$REPO/install.sh"
[ -f "$INST" ] || { echo "SEED_FAIL no install.sh at $INST"; exit 1; }
die() { echo "SEED_FAIL $*" >&2; exit 1; }
say() { [ "$QUIET" = 1 ] || echo "   seed: $*"; }
ETK_ROOT_RIG="/storage/games-internal/roms/etk"
CFG_RIG="/storage/.config"

rm -rf "$OUT"; mkdir -p "$OUT/system.d" "$OUT/custom_scripts" "$OUT/profile.d"
# a path on the rig -> its place in the seed
place() { case "$1" in "$CFG_RIG"/*) printf '%s/%s' "$OUT" "${1#"$CFG_RIG"/}" ;; *) return 1 ;; esac; }

# --- 1. heredoc bodies: every `cat << 'MARK' > /storage/.config/...` in install.sh ----
# Keyed by TARGET PATH, not by marker (seven units share the marker SVC). A target
# given as "$VAR" is resolved from that variable's one literal assignment (the
# Sentry: BOOT_SENTRY="/storage/.config/custom_scripts/01-etk-sentry.sh"). An
# UNQUOTED delimiter (cat << VPUFWUNIT) means install.sh expands variables on the
# rig; the only one in use is $ETK_ROOT, expanded here to the kit's path, and any
# other surviving $VAR is fatal (a new unquoted heredoc must be taught here).
N_UNITS=0; N_SCRIPTS=0; N_OTHER=0; WRITTEN=""
while IFS=$'\t' read -r lineno quoted mark target; do
    [ -n "$target" ] || continue
    case "$target" in
        '"$'*'"') var=${target#'"$'}; var=${var%'"'}
                  target=$(sed -n "s/^[[:space:]]*$var=\"\([^\"]*\)\".*/\1/p" "$INST" | head -1)
                  [ -n "$target" ] || die "cannot resolve heredoc target \$$var (install.sh:$lineno)" ;;
    esac
    dst=$(place "$target") || continue
    mkdir -p "$(dirname "$dst")"
    # the body: lines after this `cat <<` line up to the first line equal to the marker
    awk -v s="$lineno" -v m="$mark" 'NR>s { if ($0 == m) exit; print }' "$INST" > "$dst"
    [ -s "$dst" ] || die "empty heredoc body for $target (install.sh:$lineno, marker $mark)"
    if [ "$quoted" = "u" ]; then
        sed -i "s#\\\$ETK_ROOT#$ETK_ROOT_RIG#g" "$dst"
        left=$(grep -o '\$[A-Za-z_][A-Za-z0-9_]*' "$dst" | sort -u | tr '\n' ' ')
        [ -z "$left" ] || die "unquoted heredoc for $target still carries $left (teach gen_seed.sh the expansion)"
    fi
    case "$target" in
        */system.d/*)       N_UNITS=$((N_UNITS+1)) ;;
        *.sh)               chmod 0755 "$dst"; N_SCRIPTS=$((N_SCRIPTS+1)) ;;
        *)                  N_OTHER=$((N_OTHER+1)) ;;
    esac
    WRITTEN="$WRITTEN $target"
done < <(awk '
    # cat << '\''MARK'\'' > TARGET   |   cat << MARK > TARGET
    /^[[:space:]]*cat[[:space:]]*<<[[:space:]]*/ {
        line=$0; sub(/^[[:space:]]*cat[[:space:]]*<<[[:space:]]*/, "", line)
        q="u"; if (substr(line,1,1)=="\x27") { q="q"; line=substr(line,2); i=index(line,"\x27"); m=substr(line,1,i-1); line=substr(line,i+1) }
        else { i=index(line," "); if (i==0) next; m=substr(line,1,i-1); line=substr(line,i) }
        if (line !~ /^[[:space:]]*>[[:space:]]*/) next
        sub(/^[[:space:]]*>[[:space:]]*/, "", line); sub(/[[:space:]].*$/, "", line)
        printf "%d\t%s\t%s\t%s\n", NR, q, m, line
    }' "$INST")
[ "$N_UNITS" -gt 0 ] || die "no units extracted from install.sh"
say "install.sh heredocs: $N_UNITS units, $N_SCRIPTS boot scripts, $N_OTHER other files"

# --- 2. files install.sh ships from config/ ----------------------------------------
# <repo file>  <rig path>   (STEP numbers are install.sh's)
cp_cfg() {  # $1 repo-relative source  $2 rig path  $3 mode
    [ -f "$REPO/$1" ] || die "config source missing: $1"
    dst=$(place "$2") || die "not a .config path: $2"
    mkdir -p "$(dirname "$dst")"; tr -d '\r' < "$REPO/$1" > "$dst"; chmod "$3" "$dst"
    WRITTEN="$WRITTEN $2"
}
cp_cfg config/etk-gtk-version.service          "$CFG_RIG/system.d/etk-gtk-version.service" 0644   # STEP 6.4 (lane renders @GTKVER@...)
cp_cfg config/etk-ucm.service                  "$CFG_RIG/system.d/etk-ucm.service"         0644   # STEP 6.76
cp_cfg config/etk-ucm-bind.sh                  "$CFG_RIG/etk-ucm-bind.sh"                  0755   # STEP 6.76
cp_cfg config/ucm/sm8250-HiFi-RP.flip2.conf    "$CFG_RIG/etk-ucm/HiFi-RP.conf"             0644   # STEP 6.76
cp_cfg config/wireplumber-dp-s16.conf          "$CFG_RIG/wireplumber/wireplumber.conf.d/50-etk-dp-audio-s16.conf" 0644   # STEP 6.75
cp_cfg config/wireplumber-capture-s16.conf     "$CFG_RIG/wireplumber/wireplumber.conf.d/51-etk-capture-s16.conf" 0644   # STEP 6.75 (the mic needs S16)
cp_cfg config/MangoHud.conf                    "$CFG_RIG/MangoHud/MangoHud.conf"           0644   # STEP 3
say "config/ files: boot identity (placeholders for the lane), mic UCM overlay + bind + unit, 2 WirePlumber S16 pins, MangoHud"

# --- 3. the battery guard's conf, install.sh's defaults (STEP 6.79) -----------------
BG_FLOOR=$(sed -n 's/.*ETK_BATTERY_FLOOR:-\([0-9]*\)}.*/\1/p' "$INST" | head -1)
BG_CHECK=$(sed -n 's/.*ETK_BATTERY_CHECK_MIN:-\([0-9]*\)}.*/\1/p' "$INST" | head -1)
[ -n "$BG_FLOOR" ] && [ -n "$BG_CHECK" ] || die "cannot read the battery guard defaults from install.sh"
printf 'ETK_BATTERY_FLOOR=%s\nETK_BATTERY_CHECK_MIN=%s\n' "$BG_FLOOR" "$BG_CHECK" > "$OUT/etk_battery_guard.conf"
say "battery guard conf: floor ${BG_FLOOR}%, asleep checks every ${BG_CHECK} min"

# --- 4. wants-links: what install.sh enables, where each unit says it belongs --------
# `systemctl enable` on a unit with [Install] WantedBy=a b creates a.wants/<unit> and
# b.wants/<unit>. Only units install.sh ENABLES get links (etk-mdns-heal.service is
# pulled by its timer, never enabled itself). The boot-identity unit is linked by hand
# in STEP 6.4 (basic.target.wants) and is enabled nowhere else; the enable list is
# read from install.sh so a unit added without an enable stays unlinked here too.
ENABLED=$(grep -oE 'systemctl enable (/storage/\.config/system\.d/)?(\$u|[A-Za-z0-9_.@-]+)\.(service|timer)' "$INST" \
          | sed -E 's#.*/##; s#^systemctl enable ##' | sort -u)
# the battery guard's `for u in a b c; do systemctl enable .../$u.service` loop
for_u=$(sed -n 's/^for u in \(etk-[a-z -]*\); do$/\1/p' "$INST" | head -1)
ENABLED=$(printf '%s\n' "$ENABLED" $(for u in $for_u; do echo "$u.service"; done) | grep -v '^\$u' | sort -u)
N_LINKS=0
for unit in $ENABLED; do
    [ -f "$OUT/system.d/$unit" ] || die "install.sh enables $unit but the seed has no such unit"
    wb=$(sed -n 's/^WantedBy=//p' "$OUT/system.d/$unit" | head -1)
    [ -n "$wb" ] || die "$unit has no WantedBy= (nothing to link)"
    for tgt in $wb; do
        mkdir -p "$OUT/system.d/$tgt.wants"
        ln -sfn "$CFG_RIG/system.d/$unit" "$OUT/system.d/$tgt.wants/$unit"
        N_LINKS=$((N_LINKS+1))
    done
done
mkdir -p "$OUT/system.d/basic.target.wants"
ln -sfn "$CFG_RIG/system.d/etk-gtk-version.service" "$OUT/system.d/basic.target.wants/etk-gtk-version.service"
N_LINKS=$((N_LINKS+1))
[ -L "$OUT/system.d/multi-user.target.wants/etk.service" ] || die "etk.service (the Sentry) is not wanted -- this card would activate nothing"
say "wants-links: $N_LINKS ($(ls -d "$OUT"/system.d/*.wants | xargs -n1 basename | sed 's/.wants$//' | tr '\n' ' '))"

# --- 5. every ExecStart under /storage/.config must name a file the seed carries ------
# (a unit whose script lives in $ETK_ROOT is fine: the kit tree rides beside the seed)
for u in "$OUT"/system.d/*.service; do
    for p in $(grep -oE "$CFG_RIG/[A-Za-z0-9_./@-]+" "$u" | sort -u); do
        case "$p" in "$CFG_RIG"/profile.d/095-etk-pitlink) continue ;; esac   # the switch: absent by design
        [ -e "$(place "$p")" ] || die "$(basename "$u") references $p, which the seed does not carry"
    done
done
NF=$(find "$OUT" -type f | wc -l | tr -d ' '); NL=$(find "$OUT" -type l | wc -l | tr -d ' ')
say "rendered: $NF files + $NL symlinks -> $OUT"
exit 0
