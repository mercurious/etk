#!/usr/bin/env bash
# ==========================================================
# ETK FORGE — mint the kit's binaries on etk-cloud
# ==========================================================
# ⚠️ THE OPERATOR RUNS THIS SCRIPT. CLAUDE NEVER DOES — including `--dry-run`,
# whose preflight opens ssh to the build node.
# WHY (TRACK_MANUAL §1.1, Law #9, the "mint" threshold): this runs on SOMEONE ELSE'S
# COMPUTER and can TRIGGER AN INVOICE. Money is atoms. The A1 node is
# free-tier until it isn't, and spending a stranger's compute on our behalf is
# a human's decision to make and be accountable for — not a cheap, reversible
# byte operation. Claude's job is to prepare the inputs — stage artifacts, set
# the FORGE_* knobs, reconcile the pins with gtk_stack.json, run the host-side
# gates — and then hand off. Read this file; don't run it.
# (2026-08-10: the "NEVER contacts the rig" line below describes forge's
# contract TOWARD THE RIG. It was misread as "forge is the safe one to run,"
# and forge was run three times before the operator stopped it. It is not a
# safety statement about who may invoke this script.)
#
# The Engineer's counterpart to install.sh: install.sh deploys the kit to the
# rig; forge.sh mints the artifacts install.sh deploys. Build -> gate -> sha ->
# stage to this tree. It NEVER contacts the rig and NEVER publishes — staging
# candidates for testing is the whole contract (operator decision 2026-08-07).
# Design record: dossiers/ForgePlan_20260807.md (+ ForgeScript_Handoff_20260807).
#
# Lanes (SEQUENTIAL — the build node has 4 cores; parallel lanes would thrash):
#   rpcs3     reset -> patch -> build -> hardened package -> marker gates ~25 min
#   turnip    one build per MESA_VER; unstripped .so + ETK-GTK string gate ~5 min ea
#   kernel    build_712.sh (gcc-15 enforced in-recipe); drift diff SURFACED ~15 min
#   chiaki / wlmirror   delegate to the proven stagers in tools/rocknix-bin ~2 min
#   image     LAST — bakes the three artifacts above into the SD card image ~40 min
#
# Usage:
#   ./forge.sh                    # all lanes
#   ./forge.sh rpcs3 turnip       # a subset
#   ./forge.sh --dry-run          # preflight + would-build report, no builds
#   ./forge.sh --status           # reprint the last/live run's lane states
#   ./forge.sh --force            # rebuild even if fingerprints are fresh
#   ./forge.sh --local            # colima fallback (compact; node down / IP moved)
#   ./forge.sh --verbose | -v     # raw output, no TUI (this is also the CI path)
#
# Structural laws (each one paid for during the hand-forged v0.8.4 cut):
#   * conductor, not a second install.sh — lane logic lives in the fork repos
#     and tools/forge/lane_*.sh; this file only sequences, polls, verifies, stages
#   * heavy builds run DETACHED on the node (setsid nohup + rc marker): a dropped
#     ssh cannot kill a 25-minute build, and re-running forge REATTACHES to a
#     live build instead of restarting it
#   * fail loud, never cascade: a lane FAIL marks the lane and moves on; the
#     forge exits non-zero at the end
#   * evidence, not spinners: sha + gate verdicts land in the datalog and in
#     state/forge/status.tsv (machine-readable, for reads without a chat round-trip)
#   * fingerprint-fresh lanes SKIP so a re-run after one failure is cheap
# ==========================================================
set -u

cd "$(dirname "$0")" || exit 1
REPO_ROOT="$PWD"

# ANSI palette (matches env.sh conventions; used in raw mode only)
G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; C='\033[0;36m'; N='\033[0m'

# --- operator config (gitignored). Only FORGE_*/ETK_VERBOSE are consumed here;
# --- never echo this file's contents (it can hold a PADDOCK_TOKEN).
[ -f ./etk.conf ] && . ./etk.conf

# --- FORGE knobs (etk.conf may override; defaults reproduce the v0.8.4 stage) ---
FORGE_HOST="${FORGE_HOST:-etk-cloud}"
FORGE_RPCS3_TREE="${FORGE_RPCS3_TREE:-/home/ubuntu/rpcs3}"
FORGE_RPCS3_BASE="${FORGE_RPCS3_BASE:-a1deb2921}"
FORGE_RPCS3_FORK="${FORGE_RPCS3_FORK:-$HOME/etk-rpcs3-gtk}"
FORGE_RPCS3_PATCH="${FORGE_RPCS3_PATCH:-}"          # empty -> newest patches/*-dev.patch in the fork
FORGE_RPCS3_IMAGE="${FORGE_RPCS3_IMAGE:-etk-rpcs3-jammy-aarch64:llvm22}"
FORGE_RPCS3_MARKER="${FORGE_RPCS3_MARKER:-rpcs3_perf_stat}"
FORGE_RPCS3_ARTIFACT="${FORGE_RPCS3_ARTIFACT:-rpcs3-etk_gtk-edition-0.8.5_v0.0.41-19638-a1deb2921_linux_aarch64.AppImage}"
FORGE_TURNIP_VERS="${FORGE_TURNIP_VERS:-26.1.6 26.2.0-rc3}"
FORGE_TURNIP_GTKVER="${FORGE_TURNIP_GTKVER:-0.7}"
FORGE_KERNEL_DATE="${FORGE_KERNEL_DATE:-20260801}"
FORGE_KERNEL_VER="${FORGE_KERNEL_VER:-0.4.1}"
FORGE_KERNEL_ARTDIR="${FORGE_KERNEL_ARTDIR:-$HOME/rocknix-gtk/artifacts}"
# Recipe selector for lane_kernel: 712 = 7.1.2/20260801 (shipping), 72 =
# 7.2/20260901 rebase (scripts/build_72.sh; staging gated on the migrated
# rig's ground truth). Allowlisted below so a typo dies host-side.
FORGE_KERNEL_BUILD="${FORGE_KERNEL_BUILD:-712}"
FORGE_IMAGE_BASEDATE="${FORGE_IMAGE_BASEDATE:-20260801}"
FORGE_STALL_WARN_S="${FORGE_STALL_WARN_S:-1200}"
ETK_VERBOSE="${ETK_VERBOSE:-0}"

FORGE_STATE="$REPO_ROOT/state/forge"
RUNID="$(date +%Y%m%d-%H%M%S)"
LOGDIR="$FORGE_STATE/logs/$RUNID"
RRUNBASE='$HOME/forge-runs'          # node-side; expanded remotely
RRUNDIR="forge-runs/$RUNID"          # node-side, relative to $HOME

# --- CLI ------------------------------------------------------------------
MODE=cloud DRY=0 FORCE=0 DO_STATUS=0
SEL_LANES=""
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        --force)   FORCE=1 ;;
        --local)   MODE=local ;;
        --cloud)   MODE=cloud ;;
        --status)  DO_STATUS=1 ;;
        --verbose|-v) ETK_VERBOSE=1 ;;
        -h|--help) sed -n '3,38p' "$0"; exit 0 ;;
        rpcs3|turnip|kernel|chiaki|wlmirror|image) SEL_LANES="$SEL_LANES $a" ;;
        *) echo "unknown arg: $a (lanes: rpcs3 turnip kernel chiaki wlmirror image)" >&2; exit 2 ;;
    esac
done
[ -n "$SEL_LANES" ] || SEL_LANES="rpcs3 turnip kernel chiaki wlmirror image"
lane_selected() { case " $SEL_LANES " in *" $1 "*) return 0;; *) return 1;; esac; }

# --- small helpers (Air = macOS bash 3.2 + BSD userland; node = Ubuntu) ---
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
fsize() { wc -c < "$1" | tr -d ' '; }

# ssh with a shared control socket: the poll loop ticks every ~8 s and must not
# pay a full handshake per tick.
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10 -o ControlMaster=auto -o ControlPath=$HOME/.ssh/forge-%r@%h-%p -o ControlPersist=600"
FSSH() {
    if [ "$MODE" = local ]; then bash -c "$1"
    else # shellcheck disable=SC2086
         ssh $SSH_OPTS "$FORGE_HOST" "$1"; fi
}
FPUSH_TAR() {  # stream a tar (stdin) into a node-side dir
    if [ "$MODE" = local ]; then mkdir -p "$HOME/$1" && tar -xf - -C "$HOME/$1"
    else # shellcheck disable=SC2086
         ssh $SSH_OPTS "$FORGE_HOST" "mkdir -p \$HOME/$1 && tar -xf - -C \$HOME/$1"; fi
}
# macOS bsdtar writes xattr headers GNU tar warns about on every push — strip.
FTAR() { COPYFILE_DISABLE=1 tar --no-xattrs -cf - "$@"; }

# --- status.tsv: lane <TAB> state <TAB> pct <TAB> runid <TAB> note ----------
forge_status() {  # <lane> <state> <pct> <note>
    mkdir -p "$FORGE_STATE"
    local f="$FORGE_STATE/status.tsv" t="$FORGE_STATE/status.tmp"
    { [ -f "$f" ] && awk -F'\t' -v l="$1" '$1 != l' "$f"
      printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$RUNID" "$4"
    } > "$t" && mv "$t" "$f"
}

if [ "$DO_STATUS" = 1 ]; then
    if [ -f "$FORGE_STATE/status.tsv" ]; then
        printf '%-9s %-6s %4s  %-15s  %s\n' LANE STATE PCT RUN NOTE
        awk -F'\t' '{printf "%-9s %-6s %4s  %-15s  %s\n", $1,$2,$3,$4,$5}' "$FORGE_STATE/status.tsv"
        echo
        echo "logs: $FORGE_STATE/logs/  (a WORK row after a dead forge = re-run ./forge.sh to reattach)"
    else
        echo "no forge run recorded yet ($FORGE_STATE/status.tsv absent)"
    fi
    exit 0
fi

# --- TUI ------------------------------------------------------------------
RSYNC_CMD="rsync -az --progress"   # tui_rsync's verbose-mode fallback path
TUI_HEADER_TITLE="ETK FORGE"
TUI_TOTAL_STEPS=7
TUI_STEP_LABELS=(
    "PREFLIGHT      "
    "RPCS3 FORGE    "
    "TURNIP FORGE   "
    "KERNEL FORGE   "
    "CHIAKI+WLMIR   "
    "GTK IMAGE      "
    "GATES + STAGE  "
)
# tui.sh's header prints "RIG: $RIG_SSH" — for the forge the counterpart node
# is the build host, and TIER reads FORGE. Local shell vars only; nothing here
# is exported or written back to etk.conf.
RIG_SSH="$FORGE_HOST"
ETK_BUILD_TYPE="MINT"   # tui.sh's TIER field is 4 columns; "FORGE" overflowed it
. ./tools/tui.sh 2>/dev/null || { echo "FATAL: tools/tui.sh missing"; exit 1; }
if tui_should_activate; then tui_init; fi

fail_note() { say "[FAIL] $1"; }

# Steps: 0 PREFLIGHT · 1 RPCS3 · 2 TURNIP · 3 KERNEL · 4 CHIAKI+WLM · 5 IMAGE · 6 GATES
LANE_FAILS=""
mark_fail() { LANE_FAILS="$LANE_FAILS $1"; forge_status "$1" FAIL "${2:-0}" "$3"; fail_note "$1: $3"; }

mkdir -p "$LOGDIR" "$FORGE_STATE/fingerprints"

# ==========================================================
# PREFLIGHT (step 0) — verify everything each selected lane needs BEFORE any
# build: node reachable, containers UP (docker ps -a, not images — handoff
# trap #1), inputs staged (gitignored payloads never travel by git pull —
# trap #2), rpcs3 tree in a KNOWN state, knob names version-only (law #8).
# ==========================================================
tui_step_start 0
forge_status preflight WORK 0 "run $RUNID ($MODE)"

# knob validation first — fail before any network
KNAME="KERNEL.rocknix-gtk-${FORGE_KERNEL_DATE}-${FORGE_KERNEL_VER}"
if ! printf '%s' "$FORGE_KERNEL_VER" | grep -Eq '^[0-9]+(\.[0-9]+)*$' \
   || ! printf '%s' "$FORGE_KERNEL_DATE" | grep -Eq '^[0-9]{8}$'; then
    tui_fail "FORGE_KERNEL_DATE/VER must be 8-digit date + digits-and-dots (law #8): got $KNAME"
fi
case "$FORGE_KERNEL_BUILD" in
    712|72) ;;
    *) tui_fail "FORGE_KERNEL_BUILD must be 712 or 72 (selects scripts/build_<sel>.sh on the node): got '$FORGE_KERNEL_BUILD'" ;;
esac
IMGNAME="ROCKNIX-GTK-SM8250.aarch64-${FORGE_IMAGE_BASEDATE}.img"

# --- WHAT THE IMAGE BAKES COMES FROM THE MANIFEST, NOT FROM THE BUILD KNOBS ---
# The flashable card is a SHIPPED release asset, so its three inputs must be
# the three artifacts the release certifies — config/gtk_stack.json, which
# release_sanity already holds in lockstep with install.sh's CERT pins.
# Previously the image lane derived its driver name from FORGE_TURNIP_VERS,
# i.e. from the forge's BUILD LIST. That list is an experiment knob: on
# 2026-08-10 it read "26.2.0 26.3.0-devel-e40d93a" while the certified driver
# was 26.1.6_gtk_0.7, so a card built that day would have shipped an
# uncertified Vulkan driver to every SD-card installer — the 0.8.4 image-lane
# defect from the opposite direction (that one named a driver that no longer
# existed; this one names one that exists but is not the one we ship).
# Building a driver and SHIPPING a driver are different decisions; only the
# manifest gets to make the second one.
_manifest_asset() {  # <key> — the "asset" of gtk_stack.json's <key> block
    sed -n "/\"$1\": {/,/}/p" "$REPO_ROOT/config/gtk_stack.json" \
        | sed -n 's/.*"asset": "\([^"]*\)".*/\1/p' | head -1
}
_manifest_sha() {  # <key> — the "sha256" of gtk_stack.json's <key> block
    sed -n "/\"$1\": {/,/}/p" "$REPO_ROOT/config/gtk_stack.json" \
        | sed -n 's/.*"sha256": "\([0-9a-f]\{64\}\)".*/\1/p' | head -1
}
CERT_KNAME=$(_manifest_asset kernel)
CERT_ANAME=$(_manifest_asset rpcs3)
CERT_TNAME=$(_manifest_asset turnip)
# The middleware is an input too — see lane_image.sh. Pass what the release
# expects so the lane can refuse a node whose checkout drifted.
CERT_VER=$(grep -m1 '^APP_VERSION' "$REPO_ROOT/bin/etk_pitstop.py" | cut -d'"' -f2)
CERT_HEAD=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "")
# THE CARD SHIPS THE WHOLE CATALOG. install.sh's CERTIFIED_BUILDS is cumulative
# so the operator can downgrade from the DRIVER tab, but the image used to bake
# only the default — a flashed card offered a one-entry chooser with no
# downgrade and no pre-release arm (found on a cold card, 2026-08-10). Pair each
# certified driver with its driver_sha() pin and hand the lot to the lane, which
# verifies every one before baking. Derived from install.sh, never re-listed
# here: two lists of drivers is how they drift.
CERT_TCAT=$(awk '
    /^CERTIFIED_BUILDS="/ { gsub(/^CERTIFIED_BUILDS="|"$/,""); n=split($0,a," "); next }
    /^    etk_turnip_.*\) echo "/ {
        name=$1; sub(/\)$/,"",name)
        match($0, /"[0-9a-f]{64}"/); sha=substr($0,RSTART+1,64); m[name]=sha
    }
    END { for (i=1;i<=n;i++) if (a[i] in m) printf "%s:%s ", a[i], m[a[i]] }
' "$REPO_ROOT/install.sh")
CERT_KSHA=$(_manifest_sha kernel)
CERT_ASHA=$(_manifest_sha rpcs3)
CERT_TSHA=$(_manifest_sha turnip)
if lane_selected image; then
    for _p in "kernel:$CERT_KNAME" "rpcs3:$CERT_ANAME" "turnip:$CERT_TNAME"; do
        [ -n "${_p#*:}" ] || tui_fail "gtk_stack.json has no ${_p%%:*} asset — cannot bake an image"
    done
    # The card must carry the same kernel the manifest ships. If the forge is
    # pointed at a different one, that is a decision the operator has to make
    # explicitly, not a silent divergence discovered after a 40-minute build.
    if [ "$KNAME" != "$CERT_KNAME" ]; then
        tui_fail "image lane: forge would bake $KNAME but the manifest ships $CERT_KNAME — reconcile FORGE_KERNEL_VER with config/gtk_stack.json"
    fi
fi

# resolve the rpcs3 patch from the committed fork clone (canonical source; the
# node's untracked copies are never consulted)
if lane_selected rpcs3; then
    [ -d "$FORGE_RPCS3_FORK" ] || tui_fail "FORGE_RPCS3_FORK missing: $FORGE_RPCS3_FORK"
    if [ -z "$FORGE_RPCS3_PATCH" ]; then
        FORGE_RPCS3_PATCH=$(ls -t "$FORGE_RPCS3_FORK"/patches/*-dev.patch 2>/dev/null | head -1)
    fi
    [ -f "$FORGE_RPCS3_PATCH" ] || tui_fail "no rpcs3 patch found (FORGE_RPCS3_PATCH)"
    [ -f "$FORGE_RPCS3_FORK/scripts/package-appimage.sh" ] || tui_fail "fork packager missing"
    say "rpcs3 patch: $(basename "$FORGE_RPCS3_PATCH")"
fi
tui_step_progress 0 15

# node reachability — the ephemeral-IP trap gets the loud remediation
if [ "$MODE" = cloud ] && ! FSSH true 2>/dev/null; then
    tui_cleanup
    echo -e "${R}FATAL: build host '$FORGE_HOST' unreachable.${N}" >&2
    echo    "  etk-cloud's public IP is EPHEMERAL — it changes on stop/start." >&2
    echo    "  Re-read it from the Oracle console, update 'Host etk-cloud' in" >&2
    echo    "  ~/.ssh/config, or run with --local for the colima fallback." >&2
    exit 2
fi
tui_step_progress 0 30

# one consolidated probe (fast-arming discipline: never a dozen round-trips)
PROBE=$(FSSH "
  echo '@CONTAINERS'; docker ps -a --format '{{.Names}} {{.Status}}' 2>/dev/null
  echo '@IMAGEID';    docker images -q '$FORGE_RPCS3_IMAGE' 2>/dev/null
  echo '@DISK';       df -Pm \$HOME 2>/dev/null | awk 'NR==2{print \$4}'
  echo '@TURNIPTREES'; for v in ${FORGE_TURNIP_VERS}; do docker exec turnip-rocknix git -C /work/mesa-\$v rev-parse HEAD 2>/dev/null || echo unknown; done
  echo '@KERNELTIP';  git -C \$HOME/rocknix-gtk rev-parse --short=9 HEAD 2>/dev/null
  echo '@IMGINPUTS';  for f in \$HOME/etk/os-install/ROCKNIX-SM8250.aarch64-${FORGE_IMAGE_BASEDATE}.img.gz \
                               \$HOME/etk/os-install/build/seed_config \
                               \$HOME/etk/os-install/build/mount-storage.sh \
                               \$HOME/etk/os-install/build/build_gtk_image_v2.sh; do
                        [ -e \"\$f\" ] && echo \"OK \$f\" || echo \"MISSING \$f\"; done
  echo '@RPCS3TREE';  cd '$FORGE_RPCS3_TREE' 2>/dev/null && git diff --stat | tail -1
  echo '@END'
" 2>/dev/null)
probe_section() { printf '%s\n' "$PROBE" | awk -v s="@$1" '$0==s{f=1;next} /^@/{f=0} f'; }

DISK_MB=$(probe_section DISK | head -1); DISK_MB=${DISK_MB:-0}
[ "$DISK_MB" -gt 20000 ] 2>/dev/null || say "WARN: node disk low (${DISK_MB} MB free)"

MISSING_PRE=""
need_container() {
    if ! probe_section CONTAINERS | grep -q "^$1 Up"; then
        MISSING_PRE="$MISSING_PRE $2:container-$1-not-Up"
    fi
}
lane_selected turnip  && need_container turnip-rocknix turnip
lane_selected kernel  && need_container rocknix-gtk-kernel-sid kernel
lane_selected image   && need_container etk-imgtool image
if lane_selected rpcs3 && [ -z "$(probe_section IMAGEID | head -1)" ]; then
    MISSING_PRE="$MISSING_PRE rpcs3:image-$FORGE_RPCS3_IMAGE-missing"
fi
if lane_selected image && probe_section IMGINPUTS | grep -q '^MISSING'; then
    MISSING_PRE="$MISSING_PRE image:$(probe_section IMGINPUTS | awk '/^MISSING/{print $2}' | tr '\n' ',')"
fi
tui_step_progress 0 55

# rpcs3 tree state: empty diff OR exactly the configured patch (its resting
# state after a build) are both CLEAN; anything else fails loud, diff banked.
TREE_STATE="n/a"
lane_selected rpcs3 && TREE_STATE="clean"
if lane_selected rpcs3; then
    TREE_STAT=$(probe_section RPCS3TREE | head -1)
    if [ -n "$TREE_STAT" ]; then
        # push the patch up and ask git if the diff is exactly that patch
        FTAR -C "$(dirname "$FORGE_RPCS3_PATCH")" "$(basename "$FORGE_RPCS3_PATCH")" | FPUSH_TAR "$RRUNDIR"
        if FSSH "cd '$FORGE_RPCS3_TREE' && git apply -R --check \$HOME/$RRUNDIR/$(basename "$FORGE_RPCS3_PATCH") 2>/dev/null"; then
            TREE_STATE="patch-applied (resting state)"
        else
            FSSH "cd '$FORGE_RPCS3_TREE' && git diff > \$HOME/$RRUNDIR/preflight-dirty.diff" 2>/dev/null
            MISSING_PRE="$MISSING_PRE rpcs3:tree-has-UNKNOWN-changes(banked:~/$RRUNDIR/preflight-dirty.diff)"
        fi
    fi
    say "rpcs3 tree: $TREE_STATE"
fi
tui_step_progress 0 75

if [ -n "$MISSING_PRE" ]; then
    forge_status preflight FAIL 75 "$MISSING_PRE"
    tui_fail "preflight failed:$MISSING_PRE"
fi

TURNIP_TIP=$(probe_section TURNIPTREES | tr '\n' ',')
KERNEL_TIP=$(probe_section KERNELTIP | head -1)

# --- fingerprints: decide BUILD vs SKIP per lane --------------------------
fp_compute() {  # <lane> -> fingerprint string on stdout
    case "$1" in
        rpcs3)  printf 'base=%s patch=%s pkgr=%s vmk=%s img=%s' \
                    "$FORGE_RPCS3_BASE" "$(sha256_of "$FORGE_RPCS3_PATCH")" \
                    "$(sha256_of "$FORGE_RPCS3_FORK/scripts/package-appimage.sh")" \
                    "$(sha256_of "$FORGE_RPCS3_FORK/scripts/verify-markers.sh")" \
                    "$(probe_section IMAGEID | head -1)" ;;
        turnip) printf 'vers=%s gtk=%s tip=%s' "$FORGE_TURNIP_VERS" "$FORGE_TURNIP_GTKVER" "$TURNIP_TIP" ;;
        kernel) printf 'tip=%s name=%s kcc=gcc-15' "$KERNEL_TIP" "$KNAME" ;;
        # kit=%s IS LOAD-BEARING. The image bakes the ETK middleware straight
        # out of the build node's checkout, but this fingerprint only ever
        # hashed the three BINARIES, the base date and the recipe. The
        # middleware was invisible to it — so a card built from a stale node
        # was declared "fresh", AND the fix for that staleness (pulling the
        # node) changed nothing the fingerprint could see, so the rebuild that
        # would have corrected it SKIPPED. That is how a 0.8.5 cut etched a
        # 0.8.4 card and then refused to re-bake: the bug hid, and then it hid
        # its own repair. Version AND commit, because one version string spans
        # many commits.
        # HASH WHAT THE IMAGE BAKES, NOT WHAT THE LANES BUILT. These used to be
        # the per-lane fingerprint files — the shas of whatever the rpcs3/turnip/
        # kernel lanes last produced. But since the image lane started taking its
        # three artifact NAMES from config/gtk_stack.json, the lanes and the
        # manifest are different things, and the fingerprint was describing the
        # wrong one: on 2026-08-10 it held turnip 7ed58c2f (26.2.0, the lane's
        # output) while the card actually baked 8a16efa6 (26.1.6, the manifest's
        # pin). Changing the manifest would then not move the fingerprint at all,
        # so the rebuild that the change REQUIRES would skip as fresh — the same
        # self-concealing failure as the kit= term, one layer down.
        image)  printf 'k=%s a=%s t=%s base=%s script=%s kit=%s@%s' \
                    "$CERT_KSHA" "$CERT_ASHA" "$CERT_TSHA" \
                    "$FORGE_IMAGE_BASEDATE" \
                    "$(sha256_of os-install/build/build_gtk_image_v2.sh)" \
                    "$CERT_VER" "$CERT_HEAD"
                # cat= : the catalog is baked onto the card, so adding or
                # dropping a driver MUST force a rebuild. Without this the
                # fix for the one-entry chooser would itself skip as fresh.
                printf ' cat=%s' "$CERT_TCAT" ;;
        *)      printf 'delegated' ;;   # chiaki/wlmirror pin their own refs
    esac
}
lane_fresh() {  # 0 = fingerprint fresh AND staged artifact intact
    [ "$FORCE" = 1 ] && return 1
    local f="$FORGE_STATE/fingerprints/$1"
    [ -f "$f" ] || return 1
    [ "$(fp_compute "$1")" = "$(head -1 "$f")" ] || return 1
    local art; art=$(sed -n '2p' "$f"); local sha; sha=$(sed -n '3p' "$f")
    [ -n "$art" ] && [ -f "$art" ] && [ "$(sha256_of "$art")" = "$sha" ]
}
fp_bank() {  # <lane> <staged-artifact-path>
    { fp_compute "$1"; echo; printf '%s\n%s\n' "$2" "$(sha256_of "$2")"; } \
        > "$FORGE_STATE/fingerprints/$1"
    sha256_of "$2" > "$FORGE_STATE/fingerprints/$1.sha"
}

if [ "$DRY" = 1 ]; then
    tui_step_done 0
    say "DRY RUN -- would do:"
    for L in $SEL_LANES; do
        if lane_fresh "$L"; then say "  $L: SKIP (fingerprint fresh)"
        else say "  $L: BUILD"; fi
    done
    TUI_FINISH_BANNER="━━━ DRY RUN COMPLETE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    TUI_FINISH_LINE1="preflight green on $FORGE_HOST ($MODE mode)"
    TUI_FINISH_LINE2="re-run without --dry-run to forge"
    TUI_FINISH_VERBOSE_HEAD="DRY RUN COMPLETE — preflight green."
    tui_finish
    exit 0
fi

# push the lane bundle (recipes + env) in ONE shot
BUNDLE="$LOGDIR/.bundle"; mkdir -p "$BUNDLE"
for L in rpcs3 turnip kernel image; do
    lane_selected "$L" && cp "tools/forge/lane_$L.sh" "$BUNDLE/"
done
if lane_selected rpcs3; then
    cp "$FORGE_RPCS3_PATCH" "$BUNDLE/"
    cp "$FORGE_RPCS3_FORK/scripts/package-appimage.sh" "$BUNDLE/"
    cp "$FORGE_RPCS3_FORK/scripts/verify-markers.sh" "$BUNDLE/"
fi
( cd "$BUNDLE" && FTAR . ) | FPUSH_TAR "$RRUNDIR"
tui_step_done 0
forge_status preflight DONE 100 "node green; tree: $TREE_STATE"

# ==========================================================
# LANE DRIVER — launch detached, poll, fetch, verify, stage
# ==========================================================
lane_env() {  # <lane> -> env assignments for the node-side recipe
    case "$1" in
        rpcs3)  printf 'TREE=%s BASE=%s PATCH=%s IMG=%s MARKER=%s ANAME=%s' \
                    "$FORGE_RPCS3_TREE" "$FORGE_RPCS3_BASE" "$(basename "$FORGE_RPCS3_PATCH")" \
                    "$FORGE_RPCS3_IMAGE" "$FORGE_RPCS3_MARKER" "$FORGE_RPCS3_ARTIFACT" ;;
        turnip) printf 'VERS="%s" GTKVER=%s' "$FORGE_TURNIP_VERS" "$FORGE_TURNIP_GTKVER" ;;
        kernel) printf 'KNAME=%s FORGE_KERNEL_BUILD=%s' "$KNAME" "$FORGE_KERNEL_BUILD" ;;
        # The three baked names come from the MANIFEST (see the preflight
        # note above), never from the build knobs — an image is a shipped
        # asset and must carry exactly the certified stack.
        image)  printf 'KNAME=%s ANAME=%s TNAME=%s KSHA=%s ASHA=%s TSHA=%s TCAT="%s" EXPECT_VER=%s EXPECT_HEAD=%s BASEDATE=%s OUTIMG=%s' \
                    "$CERT_KNAME" "$CERT_ANAME" "$CERT_TNAME" \
                    "$CERT_KSHA" "$CERT_ASHA" "$CERT_TSHA" "$CERT_TCAT" \
                    "$CERT_VER" "$CERT_HEAD" \
                    "$FORGE_IMAGE_BASEDATE" "$IMGNAME" ;;
    esac
}

lane_launch_or_attach() {  # <lane>  — sets ATTACH_DIR (node-relative rundir polled)
    local L="$1"
    ATTACH_DIR="$RRUNDIR"
    local act
    act=$(FSSH "cat \$HOME/forge-runs/active_$L 2>/dev/null" || true)
    if [ -n "$act" ]; then
        local adir apid
        adir=$(printf '%s' "$act" | awk '{print $1}')
        apid=$(printf '%s' "$act" | awk '{print $2}')
        if FSSH "kill -0 $apid 2>/dev/null"; then
            say "$L: REATTACHED to live build (pid $apid, $(basename "$adir"))"
            ATTACH_DIR="forge-runs/$(basename "$adir")"
            return 0
        fi
        if ! FSSH "test -f $adir/lane_$L.rc"; then
            # trap #6: a crash must not look like "still running"
            say "$L: prior build DIED without an exit marker -- relaunching"
        fi
        FSSH "rm -f \$HOME/forge-runs/active_$L"
    fi
    # The wrapper records ITS OWN pid ($$ after setsid): a backgrounded setsid
    # forks when it is the group leader, so the launcher's $! can die instantly
    # while the build lives — reattach must key on the session leader instead.
    FSSH "cd \$HOME/$RRUNDIR || exit 1
env $(lane_env "$L") RUNDIR=\$HOME/$RRUNDIR setsid nohup bash -c 'echo \$\$ > lane_$L.pid; bash lane_$L.sh; echo \$? > lane_$L.rc' >> lane_$L.log 2>&1 < /dev/null &
sleep 1
printf '%s %s\n' \"\$PWD\" \"\$(cat lane_$L.pid 2>/dev/null)\" > \$HOME/forge-runs/active_$L"
}

lane_poll() {  # <lane> <step_idx> [<extra probe cmd>] -> rc in LANE_RC
    local L="$1" idx="$2" extra="${3:-true}"
    local last_size=0 last_change
    last_change=$(date +%s)
    LANE_RC=""
    while :; do
        local tick rc tailtxt size xtra
        tick=$(FSSH "cd \$HOME/$ATTACH_DIR 2>/dev/null || exit 0
                     cat lane_$L.rc 2>/dev/null; echo '@@T'
                     tail -c 1600 lane_$L.log 2>/dev/null | tr -d '\r'; echo; echo '@@S'
                     wc -c < lane_$L.log 2>/dev/null; echo '@@X'
                     $extra" 2>/dev/null)
        rc=$(printf '%s\n' "$tick" | sed -n '1{/^@@T$/!p;}' | head -1)
        tailtxt=$(printf '%s\n' "$tick" | awk '/^@@T$/{f=1;next} /^@@S$/{f=0} f')
        size=$(printf '%s\n' "$tick" | awk '/^@@S$/{f=1;next} /^@@X$/{f=0} f' | tr -dc '0-9')
        xtra=$(printf '%s\n' "$tick" | awk '/^@@X$/{f=1;next} f' | tail -1 | tr -dc '0-9')
        if [ -n "$rc" ] && [ "$rc" != "@@T" ]; then LANE_RC="$rc"; break; fi
        # progress: ninja [N/M] beats everything; else image "== N." phases;
        # else adaptive log-size crawl (kernel: container build log via $extra)
        local pct="" nm
        nm=$(printf '%s' "$tailtxt" | grep -oE '\[[0-9]+/[0-9]+\]' | tail -1 | tr -d '[]')
        if [ -n "$nm" ]; then
            pct=$(( 5 + ${nm%%/*} * 80 / ${nm##*/} ))
        else
            local ph
            ph=$(printf '%s' "$tailtxt" | grep -oE '== [0-9]\.' | tail -1 | tr -dc '0-9')
            if [ -n "$ph" ]; then pct=$(( ph * 13 ))
            elif [ -n "$xtra" ] && [ "$xtra" -gt 0 ] 2>/dev/null; then
                local exp
                exp=$(cat "$FORGE_STATE/fingerprints/$L.expected" 2>/dev/null || echo 0)
                [ "$exp" -gt 0 ] 2>/dev/null && pct=$(( xtra * 80 / exp )) && [ "$pct" -gt 85 ] && pct=85
            fi
        fi
        [ -n "$pct" ] && tui_step_progress "$idx" "$pct" && forge_status "$L" WORK "$pct" "building on $FORGE_HOST"
        local line
        # ${var:0:N} slices CHARACTERS (locale-aware); cut -c slices BYTES and
        # tears multibyte glyphs mid-sequence — a torn ellipsis/em-dash briefly
        # garbles the datalog row until the next redraw (operator-caught).
        line=$(printf '%s\n' "$tailtxt" | grep -vE '^\s*$' | tail -1)
        line="${line:0:70}"
        [ -n "$line" ] && tui_log "$L: $line"
        # stall watch (informational — the rc marker stays authoritative)
        local now; now=$(date +%s)
        if [ "$size" != "$last_size" ] && [ -n "$size" ]; then last_size="$size"; last_change=$now; fi
        if [ $(( now - last_change )) -gt "$FORGE_STALL_WARN_S" ]; then
            say "WARN: $L log quiet $(( (now-last_change)/60 )) min (LTO links are long-silent; rc marker rules)"
            last_change=$now   # re-arm so the warning repeats, not floods
        fi
        sleep 8
    done
    FSSH "rm -f \$HOME/forge-runs/active_$L" 2>/dev/null
    # bank the log + adaptive size expectation Air-side
    FSSH "cat \$HOME/$ATTACH_DIR/lane_$L.log 2>/dev/null" > "$LOGDIR/lane_$L.log" 2>/dev/null
    [ -n "${xtra:-}" ] && [ "${xtra:-0}" -gt 0 ] 2>/dev/null && echo "$xtra" > "$FORGE_STATE/fingerprints/$L.expected"
}

stage_artifact() {  # <lane> <local-target-path> <node-sha> — .prev backup + sha sidecar
    local L="$1" tgt="$2" nsha="$3"
    if [ ! -s "$tgt.forge-tmp" ]; then
        mark_fail "$L" 95 "fetch produced no file ($tgt.forge-tmp)"; return 1
    fi
    local got; got=$(sha256_of "$tgt.forge-tmp")
    if [ "$got" != "$nsha" ]; then
        mark_fail "$L" 95 "transfer sha mismatch ($got != node $nsha)"; return 1
    fi
    if [ -f "$tgt" ] && [ "$(sha256_of "$tgt")" != "$got" ]; then
        cp -p "$tgt" "$tgt.prev"
        say "$L: prior $(basename "$tgt") backed up as .prev"
    fi
    mv "$tgt.forge-tmp" "$tgt"
    ( cd "$(dirname "$tgt")" && { command -v sha256sum >/dev/null 2>&1 \
        && sha256sum "$(basename "$tgt")" || shasum -a 256 "$(basename "$tgt")"; } \
        > "$(basename "$tgt").sha256" )
    say "$L: staged $(basename "$tgt") sha=${got:0:12}.."
    return 0
}

# ==========================================================
# LANE 1: RPCS3 (step 1)
# ==========================================================
if lane_selected rpcs3; then
    tui_step_start 1
    if lane_fresh rpcs3; then
        say "rpcs3: SKIP (fingerprint fresh)"; forge_status rpcs3 SKIP 100 "fresh"; tui_step_done 1
    else
        forge_status rpcs3 WORK 0 "launching"
        lane_launch_or_attach rpcs3
        lane_poll rpcs3 1
        if [ "$LANE_RC" = "0" ]; then
            tui_step_progress 1 90
            NSHA=$(FSSH "sha256sum \$HOME/etk/emulators/$FORGE_RPCS3_ARTIFACT 2>/dev/null | cut -d' ' -f1")
            mkdir -p emulators
            say "rpcs3: fetching AppImage (~80 MB)"
            tui_rsync 1 90 98 "rpcs3 fetch" "$FORGE_HOST:etk/emulators/$FORGE_RPCS3_ARTIFACT" "emulators/$FORGE_RPCS3_ARTIFACT.forge-tmp"
            if stage_artifact rpcs3 "emulators/$FORGE_RPCS3_ARTIFACT" "$NSHA"; then
                fp_bank rpcs3 "emulators/$FORGE_RPCS3_ARTIFACT"
                forge_status rpcs3 DONE 100 "gates green; ${NSHA:0:12}.."
                tui_step_done 1
            fi
        else
            mark_fail rpcs3 50 "lane rc=$LANE_RC — see $LOGDIR/lane_rpcs3.log"
        fi
    fi
fi

# ==========================================================
# LANE 2: TURNIP ×N (step 2)
# ==========================================================
if lane_selected turnip; then
    tui_step_start 2
    if lane_fresh turnip; then
        say "turnip: SKIP (fingerprint fresh)"; forge_status turnip SKIP 100 "fresh"; tui_step_done 2
    else
        forge_status turnip WORK 0 "launching"
        lane_launch_or_attach turnip
        lane_poll turnip 2
        if [ "$LANE_RC" = "0" ]; then
            tui_step_progress 2 90
            TFAIL=0
            for V in $FORGE_TURNIP_VERS; do
                TNAME="etk_turnip_rocknix_${V}_gtk_${FORGE_TURNIP_GTKVER}.so"
                NSHA=$(FSSH "sha256sum \$HOME/etk/drivers/$TNAME 2>/dev/null | cut -d' ' -f1")
                FSSH "cat \$HOME/etk/drivers/$TNAME" > "drivers/$TNAME.forge-tmp" 2>/dev/null
                stage_artifact turnip "drivers/$TNAME" "$NSHA" || TFAIL=1
            done
            if [ "$TFAIL" = 0 ]; then
                fp_bank turnip "drivers/etk_turnip_rocknix_$(echo "$FORGE_TURNIP_VERS" | awk '{print $1}')_gtk_${FORGE_TURNIP_GTKVER}.so"
                forge_status turnip DONE 100 "unstripped + ETK-GTK string gates green"
                tui_step_done 2
            fi
        else
            mark_fail turnip 50 "lane rc=$LANE_RC — see $LOGDIR/lane_turnip.log"
        fi
    fi
fi

# ==========================================================
# LANE 3: KERNEL (step 3) — the one artifact whose failure mode is a silent
# black screen; the forge can BUILD it, only the operator's cold boot PASSES it.
# ==========================================================
if lane_selected kernel; then
    tui_step_start 3
    if lane_fresh kernel; then
        say "kernel: SKIP (fingerprint fresh)"; forge_status kernel SKIP 100 "fresh"; tui_step_done 3
    else
        forge_status kernel WORK 0 "launching"
        lane_launch_or_attach kernel
        lane_poll kernel 3 "docker exec rocknix-gtk-kernel-sid sh -c 'wc -c < /kernel/build712.log' 2>/dev/null"
        if [ "$LANE_RC" = "0" ]; then
            tui_step_progress 3 90
            NSHA=$(FSSH "sha256sum \$HOME/rocknix-gtk/artifacts/$KNAME 2>/dev/null | cut -d' ' -f1")
            mkdir -p "$FORGE_KERNEL_ARTDIR"
            FSSH "cat \$HOME/rocknix-gtk/artifacts/$KNAME" > "$FORGE_KERNEL_ARTDIR/$KNAME.forge-tmp" 2>/dev/null
            if stage_artifact kernel "$FORGE_KERNEL_ARTDIR/$KNAME" "$NSHA"; then
                fp_bank kernel "$FORGE_KERNEL_ARTDIR/$KNAME"
                DRIFT=$(grep -c '^[<>]' "$LOGDIR/lane_kernel.log" 2>/dev/null || echo '?')
                forge_status kernel DONE 100 "config drift: $DRIFT lines (surfaced in log) -- COLD-BOOT GATED"
                say "kernel: REMINDER -- candidate is unvalidated until the operator's cold boot"
                tui_step_done 3
            fi
        else
            mark_fail kernel 50 "lane rc=$LANE_RC — see $LOGDIR/lane_kernel.log"
        fi
    fi
fi

# ==========================================================
# LANE 4: CHIAKI + WL-MIRROR (step 4) — Air-driven stagers, already proven;
# forge adds capture + the no-churn guard (byte-identical rebuild must not
# rewrite buildinfo — that churn makes the provenance record LESS accurate).
# ==========================================================
run_stager() {  # <name> <script> [args]
    local nm="$1"; shift
    local pre post bin="tools/rocknix-bin/$nm"
    pre=$([ -f "$bin" ] && sha256_of "$bin" || echo none)
    say "$nm: $*"
    if "$@" > "$LOGDIR/lane_$nm.log" 2>&1; then
        post=$(sha256_of "$bin")
        if [ "$pre" = "$post" ]; then
            git checkout -q -- "tools/rocknix-bin/$nm.buildinfo" 2>/dev/null
            say "$nm: byte-identical rebuild -- buildinfo churn reverted"
        fi
        _last=$(tail -1 "$LOGDIR/lane_$nm.log")
        forge_status "$nm" DONE 100 "${_last:0:60}"
        return 0
    fi
    mark_fail "$nm" 50 "stager failed — see $LOGDIR/lane_$nm.log"
    return 1
}
if lane_selected chiaki || lane_selected wlmirror; then
    tui_step_start 4
    STG_ARGS=""; [ "$MODE" = local ] && STG_ARGS="--local"
    OK4=1
    if lane_selected chiaki; then
        # shellcheck disable=SC2086
        run_stager chiaki tools/rocknix-bin/build_chiaki.sh $STG_ARGS || OK4=0
        tui_step_progress 4 50
    fi
    if lane_selected wlmirror; then
        # shellcheck disable=SC2086
        run_stager wl-mirror tools/rocknix-bin/build_wl_mirror.sh $STG_ARGS || OK4=0
    fi
    [ "$OK4" = 1 ] && tui_step_done 4
fi

# ==========================================================
# LANE 5: GTK IMAGE (step 5, LAST) — bakes the three artifacts above. If a
# producer lane FAILED this run, the image would bake a known-bad input: skip.
# ==========================================================
if lane_selected image; then
    tui_step_start 5
    BLOCKED=""
    for P in rpcs3 turnip kernel; do
        case " $LANE_FAILS " in *" $P "*) BLOCKED="$BLOCKED $P";; esac
    done
    if [ -n "$BLOCKED" ]; then
        mark_fail image 0 "producer lane(s) failed:$BLOCKED — refusing to bake known-bad inputs"
    elif lane_fresh image; then
        say "image: SKIP (fingerprint fresh)"; forge_status image SKIP 100 "fresh"; tui_step_done 5
    else
        forge_status image WORK 0 "launching"
        lane_launch_or_attach image
        lane_poll image 5
        if [ "$LANE_RC" = "0" ]; then
            tui_step_progress 5 88
            NSHA=$(FSSH "cut -d' ' -f1 \$HOME/etk/os-install/$IMGNAME.gz.sha256 2>/dev/null")
            say "image: fetching $IMGNAME.gz (~1.6 GB -- a few minutes)"
            tui_rsync 5 88 98 "image fetch" "$FORGE_HOST:etk/os-install/$IMGNAME.gz" "os-install/$IMGNAME.gz.forge-tmp"
            if stage_artifact image "os-install/$IMGNAME.gz" "$NSHA"; then
                fp_bank image "os-install/$IMGNAME.gz"
                forge_status image DONE 100 "labels+kernel re-verified from artifact"
                tui_step_done 5
            fi
        else
            mark_fail image 50 "lane rc=$LANE_RC — see $LOGDIR/lane_image.log"
        fi
    fi
fi

# ==========================================================
# GATES + STAGE (step 6) — release_sanity + the evidence summary
# ==========================================================
tui_step_start 6
if bash tools/release_sanity.sh > "$LOGDIR/release_sanity.log" 2>&1; then
    say "release_sanity: PASS"
    SANITY=PASS
else
    SANITY=FAIL
    say "[FAIL] release_sanity -- see $LOGDIR/release_sanity.log"
fi
tui_step_progress 6 60

# cross-check: the image must have baked THIS kernel (both lanes this run)
if lane_selected image && lane_selected kernel \
   && [ -f "$LOGDIR/lane_image.log" ] && [ -f "$LOGDIR/lane_kernel.log" ]; then
    KREL=$(grep -m1 'kernel.release:' "$LOGDIR/lane_kernel.log" | awk '{print $2}')
    if [ -n "$KREL" ] && grep -q "Linux version $KREL" "$LOGDIR/lane_image.log"; then
        say "cross-check: image bakes kernel $KREL ✓"
    elif [ -n "$KREL" ]; then
        say "WARN: image's baked kernel string does not match $KREL -- check lane logs"
    fi
fi

{
    echo "ETK FORGE summary — run $RUNID ($MODE mode, host $FORGE_HOST)"
    echo "lanes: $SEL_LANES"
    echo "release_sanity: ${SANITY:-skipped}"
    echo
    printf '%-9s %-6s %s\n' LANE STATE NOTE
    awk -F'\t' '{printf "%-9s %-6s %s\n", $1,$2,$5}' "$FORGE_STATE/status.tsv"
    echo
    echo "logs: $LOGDIR/"
    echo "staged for testing only — deployment stays install.sh; the rig crowns."
} > "$LOGDIR/summary.txt"
tui_step_done 6

if [ -z "$LANE_FAILS" ] && [ "${SANITY:-PASS}" = "PASS" ]; then
    TUI_FINISH_BANNER="━━━ FORGE COMPLETE — CANDIDATES STAGED ━━━━━━━━━━"
    TUI_FINISH_LINE1="all lanes green • summary: $LOGDIR/summary.txt"
    TUI_FINISH_LINE2="stage-only: validate via install.sh + the operator's cold boot"
    TUI_FINISH_VERBOSE_HEAD="FORGE COMPLETE — candidates staged."
    TUI_FINISH_VERBOSE_BODY="$(cat "$LOGDIR/summary.txt")"
    tui_finish
    exit 0
else
    TUI_FINISH_BANNER="━━━ FORGE FINISHED WITH FAILURES ━━━━━━━━━━━━━━━"
    TUI_FINISH_LINE1="\033[0;31mfailed:${LANE_FAILS:- (sanity)}\033[0m • summary: $LOGDIR/summary.txt"
    TUI_FINISH_LINE2="per-lane logs in $LOGDIR/ — fingerprint-fresh lanes SKIP on re-run"
    TUI_FINISH_VERBOSE_HEAD="FORGE FINISHED WITH FAILURES:${LANE_FAILS:- (sanity)}"
    TUI_FINISH_VERBOSE_BODY="$(cat "$LOGDIR/summary.txt")"
    tui_finish
    exit 1
fi
