#!/usr/bin/env bash
# ==========================================================================
# tools/release_sanity.sh — ETK release sanity gates (run at every cut)
# --------------------------------------------------------------------------
# CHECK: artifact filenames are VERSION-ONLY (TRACK_MANUAL §C.4, law #8).
#   Feature-name cruft (`-audiofix0`, `-kgsl-parity0`, `-p0hook`, `-fiforesync`)
#   is a compulsive tendency that keeps slipping through and gets SHIPPED to the
#   end user (live miss: `KERNEL.rocknix-gtk-20260706-audiofix0` in v0.7.0). This
#   gate greps the names that ACTUALLY SHIP so it can't recur — treat it like a
#   `bash -n` / `py_compile` step in the release ritual.
#
# Scope = the shipping config, NOT loose local build junk. Superseded build
# outputs still sitting in ~/rocknix-gtk/artifacts/ (stock0, kgsl-parity0, ...)
# are intentionally NOT flagged — they don't ship; only what the config points
# at does. The checks:
#   - etk.conf              KERNEL_IMAGE   -> strict kernel version-only pattern
#   - os-install build      KERNEL default -> strict kernel version-only pattern
#   - drivers/*.so                         -> feature-word denylist
#   - install.sh            CERT_RPCS3 / CERTIFIED_BUILDS -> feature-word denylist
#
# Local (gitignored) config lives in the main checkout; resolved via
# $ETK_DEPLOY_ROOT -> repo root -> $HOME/etk. A missing input SKIPs (not fails),
# so a fresh clone / CI run checks what it can and still passes.
#
# Exit non-zero on any violation. Extend with more gates below as needed.
# ==========================================================================
set -u

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SELF_DIR/.." && pwd)
DEPLOY_ROOT="${ETK_DEPLOY_ROOT:-$REPO_ROOT}"

# Feature-word roots that have slipped (or could). NOTE what is deliberately
# absent: version tokens (0.2, 26.1.3), arch triplets (linux_aarch64) and
# edition labels (gtk-edition, gtk_0.4) are legitimate and must NOT be flagged.
FEATURE_WORDS="audiofix kgsl parity p0hook fiforesync fifo resync keepalive contextkeepalive querysurvive survive"

FAIL=0
c_ok=$'\033[32m'; c_bad=$'\033[31m'; c_warn=$'\033[33m'; c_off=$'\033[0m'
ok()   { printf "  ${c_ok}PASS${c_off}  %s\n" "$*"; }
bad()  { printf "  ${c_bad}FAIL${c_off}  %s\n" "$*"; FAIL=1; }
skip() { printf "  ${c_warn}SKIP${c_off}  %s\n" "$*"; }

# Resolve a local (possibly-gitignored) path across candidate roots.
find_local() {
    for r in "$DEPLOY_ROOT" "$REPO_ROOT" "$HOME/etk"; do
        [ -e "$r/$1" ] && { printf '%s\n' "$r/$1"; return 0; }
    done
    return 1
}

# Strict: a shipping KERNEL artifact must be KERNEL.rocknix-gtk-<8digits>-<ver>
# with <ver> = digits and dots only (0.2, 1, 1.4.0) — never a feature word. The
# 8-digit date is required: install.sh derives the boot-identity date from it
# (grep -oE '[0-9]{8}'), so a dateless name silently degrades to "dev".
check_kernel_versiononly() {
    ctx="$1"; name=$(basename "$2")
    case "$name" in
        KERNEL.rocknix-gtk-*) ;;
        *) skip "$ctx: '$name' is not a KERNEL.rocknix-gtk artifact"; return;;
    esac
    suffix=$(printf '%s' "$name" | sed -E 's/^KERNEL\.rocknix-gtk-[0-9]{8}-//')
    if [ "$suffix" = "$name" ]; then
        bad "$ctx: '$name' has no -<8-digit date>- segment (install.sh boot-identity date needs it)"
    elif printf '%s' "$suffix" | grep -Eq '^[0-9]+(\.[0-9]+)*$'; then
        ok "$ctx: $name"
    else
        bad "$ctx: '$name' — suffix '$suffix' is not version-only (law #8; use e.g. -0.2)"
    fi
}

# Denylist scan for artifact classes whose names legitimately carry words.
check_no_feature_words() {
    ctx="$1"; name=$(basename "$2")
    for w in $FEATURE_WORDS; do
        case "$name" in
            *"$w"*) bad "$ctx: '$name' contains feature word '$w' (law #8)"; return;;
        esac
    done
    ok "$ctx: $name"
}

echo "== ETK release sanity =="
echo "-- law #8: artifact filenames are version-only --"

# A. etk.conf KERNEL_IMAGE (the local kernel-deploy pointer)
if ETKCONF=$(find_local etk.conf); then
    KI=$(grep -E '^[[:space:]]*KERNEL_IMAGE=' "$ETKCONF" | tail -1 \
         | cut -d= -f2- | sed -e 's/"//g' -e "s/'//g" -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    if [ -z "$KI" ]; then
        skip "etk.conf KERNEL_IMAGE empty (stock kernel; nothing to ship)"
    else
        check_kernel_versiononly "etk.conf KERNEL_IMAGE" "$KI"
        [ -f "$KI" ] || skip "etk.conf KERNEL_IMAGE points at a missing file: $KI"
    fi
    # A dev-override here means this host is NOT installing the certified AUTO
    # lane — legitimate mid-campaign, wrong posture at a cut. Surface it.
    RAI=$(grep -E '^[[:space:]]*RPCS3_APPIMAGE=' "$ETKCONF" | tail -1 \
         | cut -d= -f2- | sed -e 's/"//g' -e "s/'//g" -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    case "$RAI" in
        ""|stock) : ;;
        *) printf "  ${c_warn}NOTE${c_off}: etk.conf RPCS3_APPIMAGE dev-override active (%s) — this host installs THAT build, not the certified AUTO lane; revert to \"\" before cutting\n" "$(basename "$RAI")" ;;
    esac
else
    skip "etk.conf not found (ok in CI / fresh clone)"
fi

# B. os-install image builder KERNEL default
if BUILD=$(find_local os-install/build/build_gtk_image_v2.sh); then
    KD=$(grep -E '^KERNEL=' "$BUILD" | tail -1 | sed -E 's/.*:-//; s/\}.*//')
    check_kernel_versiononly "build_gtk_image_v2.sh KERNEL default" "$KD"
else
    skip "os-install/build/build_gtk_image_v2.sh not found"
fi

# C. in-repo drivers
found_drv=0
for so in "$REPO_ROOT"/drivers/*.so; do
    [ -e "$so" ] || continue
    found_drv=1
    check_no_feature_words "drivers/" "$so"
done
[ "$found_drv" = 1 ] || skip "no drivers/*.so found"

# D. install.sh certified emulator/driver asset names
if [ -f "$REPO_ROOT/install.sh" ]; then
    CR=$(grep -E '^CERT_RPCS3=' "$REPO_ROOT/install.sh" | tail -1 | sed -E 's/^[^=]*=//; s/^"//; s/".*$//')
    [ -n "$CR" ] && check_no_feature_words "install.sh CERT_RPCS3" "$CR"
    CB=$(grep -E '^CERTIFIED_BUILDS=' "$REPO_ROOT/install.sh" | tail -1 | sed -E 's/^[^=]*=//; s/^"//; s/".*$//')
    for cb in $CB; do
        [ -n "$cb" ] && check_no_feature_words "install.sh CERTIFIED_BUILDS" "$cb"
    done
else
    skip "install.sh not found"
fi

# E. gtk_stack.json manifest — the self-update lane's pins must be in
# LOCKSTEP with install.sh's CERT pins (two sources, one truth: a drifted
# manifest ships couch users a different stack than the host lane installs).
# Also enforces the five-asset release contract: the kernel asset named in
# the manifest must ship on the release like the other four.
MANIFEST="$REPO_ROOT/config/gtk_stack.json"
if [ -f "$MANIFEST" ] && [ -f "$REPO_ROOT/install.sh" ]; then
    M_RPCS3=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['rpcs3']['asset'])" 2>/dev/null)
    M_RPCS3_SHA=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['rpcs3']['sha256'])" 2>/dev/null)
    M_TURNIP=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['turnip']['asset'])" 2>/dev/null)
    M_KERNEL=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['kernel']['asset'])" 2>/dev/null)
    I_RPCS3=$(grep -E '^CERT_RPCS3=' "$REPO_ROOT/install.sh" | tail -1 | sed -E 's/^[^=]*=//; s/^"//; s/".*$//')
    I_RPCS3_SHA=$(grep -E '^CERT_RPCS3_SHA=' "$REPO_ROOT/install.sh" | tail -1 | sed -E 's/^[^=]*=//; s/^"//; s/".*$//')
    if [ "$M_RPCS3" = "$I_RPCS3" ] && [ "$M_RPCS3_SHA" = "$I_RPCS3_SHA" ]; then
        ok "gtk_stack.json rpcs3 pin matches install.sh CERT_RPCS3"
    else
        bad "gtk_stack.json rpcs3 pin DRIFTED from install.sh CERT_RPCS3 ($M_RPCS3 vs $I_RPCS3)"
    fi
    if grep -qE "^CERTIFIED_BUILDS=.*$M_TURNIP" "$REPO_ROOT/install.sh" \
       && grep -q "$(python3 -c "import json;print(json.load(open('$MANIFEST'))['turnip']['sha256'])" 2>/dev/null)" "$REPO_ROOT/install.sh"; then
        ok "gtk_stack.json turnip pin matches install.sh CERTIFIED_BUILDS"
    else
        bad "gtk_stack.json turnip pin DRIFTED from install.sh (asset or sha missing there)"
    fi
    check_kernel_versiononly "gtk_stack.json kernel asset" "$M_KERNEL"
    printf "  ${c_warn:-}NOTE${c_off:-}: five-asset contract — '%s' must ship on the release\n" "$M_KERNEL"
else
    skip "config/gtk_stack.json or install.sh not found"
fi

echo
# --------------------------------------------------------------------------
# DOES THE PINNED ARTIFACT ACTUALLY EXIST?
# --------------------------------------------------------------------------
# Every check above validates NAMES and cross-file CONSISTENCY. None of them
# asked whether the thing being pinned is real. That blind spot shipped three
# separate defects, all found the same night (2026-08-06):
#   1. install.sh CERTIFIED_BUILDS pinned etk_turnip_rocknix_26.1.6_gtk_0.6.so,
#      which 404s on releases/latest — every fresh install silently fail-softed
#      to stock Turnip. gtk_stack.json agreed with install.sh, so the existing
#      lockstep check passed them both.
#   2. build_gtk_image_v2.sh defaulted TURNIP_SO to that same deleted gtk_0.6,
#      so the image lane would have died at its own input check.
#   3. …and defaulted KERNEL to -0.3 after the forge moved to etk-cloud.
# Local existence is a HARD failure. The network probe is advisory: a release
# is often cut before its assets are uploaded, and the gate must work offline.
echo "-- pinned artifacts exist --"
_probe_local() {  # $1=label  $2=path (may be a container path we remap)
    _p="$2"
    case "$_p" in
        /etk/*)         _p="$REPO_ROOT/${_p#/etk/}" ;;
        /rocknix-gtk/*) _p="$HOME/rocknix-gtk/${_p#/rocknix-gtk/}" ;;
    esac
    if [ -f "$_p" ]; then ok "$1: $(basename "$_p")"
    else bad "$1: MISSING on disk — $_p"; fi
}
if [ -f "$REPO_ROOT/os-install/build/build_gtk_image_v2.sh" ]; then
    for v in KERNEL APPIMAGE TURNIP_SO; do
        _d=$(sed -n "s/^$v=\"\${$v:-\(.*\)}\"$/\1/p" "$REPO_ROOT/os-install/build/build_gtk_image_v2.sh" | head -1)
        [ -n "$_d" ] && _probe_local "image lane $v default" "$_d"
    done
fi
for cb in $(sed -n 's/^CERTIFIED_BUILDS="\(.*\)"$/\1/p' "$REPO_ROOT/install.sh" | head -1); do
    _probe_local "install.sh CERTIFIED_BUILDS" "/etk/drivers/$cb"
done
_CERT=$(sed -n 's/^CERT_RPCS3="\(.*\)"$/\1/p' "$REPO_ROOT/install.sh" | head -1)
# ZERO-SOURCE GATE (2026-08-27 stock-nuke): install.sh AUTO has exactly two
# sources for the certified core — the local emulators/ copy, then the
# releases/latest asset. ef062b5 pinned an artifact that was never published,
# the 0.9.0.1 mint retired the local copy, and BOTH lanes went dark: the next
# install deleted rpcs3-sa.custom and the rig silently ran stock all evening.
# The old shape of this check soft-SKIPPED both conditions ("expected until
# this cut publishes" / "ok in fresh clone"). On a host whose catalog is
# populated (= a deploy host, not a fresh clone/CI), a cert pin with no local
# copy is survivable only if the release actually serves it — so:
# local miss + non-200 = FAIL; local miss + offline = FAIL (unprovable).
_CERT_NCORES=$(ls "$REPO_ROOT"/emulators/*.AppImage 2>/dev/null | wc -l | tr -d ' ')
_CERT_LOCAL=0; [ -n "$_CERT" ] && [ -f "$REPO_ROOT/emulators/$_CERT" ] && _CERT_LOCAL=1
if [ -n "$_CERT" ] && command -v curl >/dev/null 2>&1; then
    _code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' -L \
        "https://github.com/mercurious/etk/releases/latest/download/$_CERT" 2>/dev/null || echo 000)
    case "$_code" in
        200) ok "CERT_RPCS3 resolves on releases/latest (HTTP 200)" ;;
        000) if [ "$_CERT_LOCAL" = 0 ] && [ "$_CERT_NCORES" -gt 0 ]; then
                 bad "CERT_RPCS3 has NO local copy and network is down — zero provable sources ($_CERT)"
             else
                 skip "CERT_RPCS3 reachability: no network (local copy covers installs)"
             fi ;;
        *)   if [ "$_CERT_LOCAL" = 0 ] && [ "$_CERT_NCORES" -gt 0 ]; then
                 bad "CERT_RPCS3: HTTP $_code on releases/latest AND no local copy — install.sh AUTO has ZERO sources (the 2026-08-27 stock-nuke condition)"
             else
                 skip "CERT_RPCS3 returns HTTP $_code on releases/latest — expected until this cut publishes (local copy covers installs)"
             fi ;;
    esac
fi

echo
# --------------------------------------------------------------------------
# TWO CATALOGS, TWO DIFFERENT RULES (operator, 2026-08-10)
# --------------------------------------------------------------------------
# TURNIP is CUMULATIVE — a user must be able to DOWNGRADE. Each cut adds the
# latest stable plus one pre-release; nothing is removed. This only works
# because every listed driver also ships as a release ASSET: install.sh fetches
# from releases/latest/download, so dropping a driver from the asset set makes
# it unfetchable for every fresh install, not merely unrecommended. The FIRST
# entry is the certified default and must equal the manifest's turnip pin —
# that is what self-update and the flashable card take, so a pre-release
# sitting there would make devel the default for every new user.
#
# RPCS3 CORES are the opposite: capped at TWO, host-side A/B tooling, and never
# published (install.sh STEP 6.552 — "not a distribution channel"). Exactly one
# emulator ships, the certified AppImage.
#
# Gated because the driver pins drifted for two releases: FORGE_TURNIP_VERS had
# already moved to the new pair while CERTIFIED_BUILDS, the manifest, the image
# lane and the PowerShell port all still named the old one, and nothing
# compared them.
echo "-- catalogs: turnip cumulative, cores capped at 2 --"
_CB=$(sed -n 's/^CERTIFIED_BUILDS="\(.*\)"$/\1/p' "$REPO_ROOT/install.sh" | head -1)
_JT=$(sed -n '/"turnip": {/,/}/p' "$REPO_ROOT/config/gtk_stack.json" \
    | sed -n 's/.*"asset": "\([^"]*\)".*/\1/p' | head -1)
_first=$(printf '%s' "$_CB" | awk '{print $1}')
_n=0; _pre=0
for _d in $_CB; do
    _n=$((_n + 1))
    case "$_d" in *-rc[0-9]*|*-devel*|*-beta*|*-alpha*) _pre=$((_pre + 1)) ;; esac
done
[ "$_n" -ge 2 ] && ok "turnip catalog has $_n entries (cumulative — downgrade path intact)" \
                || bad "turnip catalog has $_n entry; the catalog is cumulative, older drivers must stay"
[ "$_pre" -ge 1 ] && ok "catalog carries $_pre pre-release build(s)" \
                  || bad "catalog carries no pre-release — the forward A/B arm is gone"
case "$_first" in
    *-rc[0-9]*|*-devel*|*-beta*|*-alpha*)
        bad "first entry is a PRE-RELEASE ($_first) — the default must be stable" ;;
    *)  ok "certified default is a stable build ($_first)" ;;
esac
[ "$_JT" = "$_first" ] && ok "manifest turnip pin == the certified default" \
                       || bad "manifest pin ($_JT) != CERTIFIED_BUILDS[0] ($_first)"
# Every catalog entry must be staged AND known to driver_sha, or a downgrade
# fetch either 404s or cannot be verified. The sha gate below only ever covered
# the manifest's primary — that hole nearly shipped a re-minted rc3.
for _d in $_CB; do
    _miss=""
    [ -f "$REPO_ROOT/drivers/$_d" ] || _miss="not staged"
    grep -q "    $_d)" "$REPO_ROOT/install.sh" || _miss="${_miss:+$_miss, }no driver_sha arm"
    [ -z "$_miss" ] && ok "catalog: $_d" || bad "catalog: $_d — $_miss"
done
_CORES=$(ls "$REPO_ROOT"/emulators/*.AppImage 2>/dev/null | wc -l | tr -d ' ')
_CERTAI=$(sed -n 's/^CERT_RPCS3="\(.*\)"$/\1/p' "$REPO_ROOT/install.sh" | head -1)
if [ "$_CORES" = 2 ]; then ok "rpcs3 core catalog holds 2 builds (A/B cap)"
elif [ "$_CORES" = 0 ]; then skip "no cores staged locally"
else bad "rpcs3 core catalog holds $_CORES builds — the cap is 2 (A/B only, never published)"; fi
if [ -f "$REPO_ROOT/emulators/$_CERTAI" ]; then
    ok "certified core is in the catalog"
elif [ "$_CORES" = 0 ]; then
    skip "certified core not staged locally ($_CERTAI) — no catalog on this host (fresh clone/CI)"
else
    bad "certified core is NOT in the catalog ($_CERTAI) — a populated deploy host must hold the pin it certifies; retiring a core BEFORE the CERT pins move is the 2026-08-27 stock-nuke (the zero-source gate above says whether a release covers it)"
fi

echo
# --------------------------------------------------------------------------
# DOES THE PINNED SHA MATCH THE ARTIFACT ON DISK? (added 2026-08-10)
# --------------------------------------------------------------------------
# The 0.8.4 gate learned to ask whether a pinned artifact EXISTS. It never
# asked whether the pinned sha is the sha of the thing we actually staged —
# and a filename is not a build. Live miss this exists to stop (found at the
# 0.8.5 cut): the forge re-minted the RPCS3 core on 2026-08-07 09:09, staged
# it (80,153,723 B / 9a8a4fd7…) and wrote its .sha256 sidecar, while
# install.sh, the PowerShell port and gtk_stack.json all kept pinning the
# build it replaced (80,369,730 B / 395177a6…). Every name check passed;
# every file existed; the three pins agreed with each other. They were just
# all one build behind the forge. The kernel is the same hazard with the
# teeth in: -0.3.1 and -0.4.1 are BOTH exactly 60,246,528 bytes, so size
# cannot discriminate them — only the hash can.
echo "-- pinned sha == staged artifact --"
_sha_of() { shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1; }
_check_pin() {   # $1=label  $2=file on disk  $3=pinned sha
    if [ ! -f "$2" ]; then skip "$1: not staged locally ($(basename "$2"))"; return; fi
    _got=$(_sha_of "$2")
    if [ -z "$3" ]; then skip "$1: no sha pinned"
    elif [ "$_got" = "$3" ]; then ok "$1: pin matches the staged build"
    else
        bad "$1: pin is NOT the staged build"
        printf '        staged %s  (%s B)\n' "$(printf '%s' "$_got" | cut -c1-16)…" "$(wc -c < "$2" | tr -d ' ')"
        printf '        pinned %s\n' "$(printf '%s' "$3" | cut -c1-16)…"
    fi
}
_J_RP=$(sed -n 's/.*"sha256": "\([0-9a-f]\{64\}\)".*/\1/p' "$REPO_ROOT/config/gtk_stack.json" | sed -n 3p)
_J_KN=$(sed -n 's/.*"sha256": "\([0-9a-f]\{64\}\)".*/\1/p' "$REPO_ROOT/config/gtk_stack.json" | sed -n 1p)
_I_RPSHA=$(sed -n 's/^CERT_RPCS3_SHA="\(.*\)"$/\1/p' "$REPO_ROOT/install.sh" | head -1)
_CERT=$(sed -n 's/^CERT_RPCS3="\(.*\)"$/\1/p' "$REPO_ROOT/install.sh" | head -1)
_check_pin "install.sh CERT_RPCS3_SHA" "$REPO_ROOT/emulators/$_CERT" "$_I_RPSHA"
if [ "$_J_RP" = "$_I_RPSHA" ]; then ok "gtk_stack.json rpcs3 sha matches install.sh"
else bad "gtk_stack.json rpcs3 sha DRIFTED from install.sh CERT_RPCS3_SHA"; fi
_KASSET=$(sed -n 's/.*"asset": "\(KERNEL\.[^"]*\)".*/\1/p' "$REPO_ROOT/config/gtk_stack.json" | head -1)
_check_pin "gtk_stack.json kernel sha" "$HOME/rocknix-gtk/artifacts/$_KASSET" "$_J_KN"
# THE DRIVER GETS THE SAME CHECK. Added 2026-08-10 after this gate — written
# the same day to catch exactly this — shipped covering only rpcs3 and kernel
# and missed a THIRD stale pin on its first real use: gtk_stack.json pinned
# 8a16efa6 (17,136,072 B, the .prev and the published asset) while the forge
# had re-minted a83c2306 (17,136,464 B) into drivers/. A gate that checks two
# of three artifacts is a gate with a hole in exactly the shape of the bug.
_J_TN=$(sed -n 's/.*"sha256": "\([0-9a-f]\{64\}\)".*/\1/p' "$REPO_ROOT/config/gtk_stack.json" | sed -n 2p)
_TASSET=$(sed -n '/"turnip": {/,/}/p' "$REPO_ROOT/config/gtk_stack.json" \
    | sed -n 's/.*"asset": "\([^"]*\)".*/\1/p' | head -1)
_check_pin "gtk_stack.json turnip sha" "$REPO_ROOT/drivers/$_TASSET" "$_J_TN"
# The image lane bakes the shipped card — it must name the SAME kernel.
_IMG_KN=$(sed -n 's|^KERNEL="\${KERNEL:-.*/\(KERNEL\.[^}]*\)}"|\1|p' "$REPO_ROOT/os-install/build/build_gtk_image_v2.sh" | head -1)
if [ -n "$_IMG_KN" ] && [ "$_IMG_KN" = "$_KASSET" ]; then
    ok "image lane bakes the pinned kernel ($_KASSET)"
else
    bad "image lane kernel DRIFTED (image: ${_IMG_KN:-none} vs pinned: $_KASSET)"
fi
# forge mints what the manifest ships, or the next cut re-opens this hole.
_F_KN="KERNEL.rocknix-gtk-$(sed -n 's/^FORGE_KERNEL_DATE="\${FORGE_KERNEL_DATE:-\(.*\)}"$/\1/p' "$REPO_ROOT/forge.sh" | head -1)-$(sed -n 's/^FORGE_KERNEL_VER="\${FORGE_KERNEL_VER:-\(.*\)}"$/\1/p' "$REPO_ROOT/forge.sh" | head -1)"
if [ "$_F_KN" = "$_KASSET" ]; then ok "forge.sh kernel lane targets the pinned kernel"
else bad "forge.sh would mint $_F_KN but the manifest ships $_KASSET"; fi

echo
# --------------------------------------------------------------------------
# THE GAME-TUNE NOTEBOOK IS CURRENT (added 2026-08-10)
# --------------------------------------------------------------------------
# config/config_<ID>.yml is a SHIPPED reference: it is what a fresh clone
# reads to learn the settled tune for a title. Every tune is authored on the
# rig in the Pitstop TUNING tab, and install.sh pulled the results only as far
# as gitignored Tier-B state — so the published notebook drifted a full
# release cycle behind the rig (24 of 41 titles at the 0.8.5 cut). install.sh
# now closes that gap on every deploy; this gate makes sure a cut cannot ship
# without it. Skips itself cleanly on a clone with no rig mirror.
echo "-- game-tune notebook current --"
if [ -x "$REPO_ROOT/tools/sync_game_configs.sh" ]; then
    _sync_out=$("$REPO_ROOT/tools/sync_game_configs.sh" --check 2>&1)
    _sync_rc=$?
    if [ "$_sync_rc" = 0 ]; then
        ok "config/ matches the rig's live tunes ($(printf '%s' "$_sync_out" | sed -n 's/^-- game tunes: \(.*\)$/\1/p'))"
    else
        bad "config/ game tunes are STALE vs the rig — run ./tools/sync_game_configs.sh and commit"
        printf '%s\n' "$_sync_out" | sed -n 's/^/      /p' | grep -E 'drift|new |stale' | head -6
    fi
else
    skip "tools/sync_game_configs.sh not found"
fi

echo
# --------------------------------------------------------------------------
# POWERSHELL PORT LOCKSTEP (added 2026-08-07)
# --------------------------------------------------------------------------
# The Windows port pulls rig-side heredocs from install.sh at runtime, so its
# LOGIC cannot drift — but its host-side cert pins are PS-native literals and
# drifted TWO releases (0.7.5) before anyone noticed: a fresh Windows install
# 404'd on releases/latest and silently fail-softed to stock. The port is NOT
# retiring (operator-affirmed 2026-08-07); it must ship the same stack.
echo "-- PowerShell port pin lockstep --"
PS1_FILE="$REPO_ROOT/windows_installer/etk-install.ps1"
if [ -f "$PS1_FILE" ] && [ -f "$REPO_ROOT/install.sh" ]; then
    I_RP=$(sed -n 's/^CERT_RPCS3="\(.*\)"$/\1/p' "$REPO_ROOT/install.sh" | head -1)
    I_RPSHA=$(sed -n 's/^CERT_RPCS3_SHA="\(.*\)"$/\1/p' "$REPO_ROOT/install.sh" | head -1)
    P_RP=$(sed -n 's/^\$certRpcs3[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$PS1_FILE" | head -1)
    P_RPSHA=$(sed -n 's/^\$certRpcs3Sha[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$PS1_FILE" | head -1)
    if [ -n "$P_RP" ] && [ "$P_RP" = "$I_RP" ] && [ "$P_RPSHA" = "$I_RPSHA" ]; then
        ok "PS port CERT_RPCS3 pin matches install.sh"
    else
        bad "PS port CERT_RPCS3 pin DRIFTED (ps1: ${P_RP:-none} vs install.sh: $I_RP)"
    fi
    I_TN=$(sed -n 's/^CERTIFIED_BUILDS="\(.*\)"$/\1/p' "$REPO_ROOT/install.sh" | head -1 | tr ' ' '\n' | sort)
    P_TN=$(sed -n 's/.*Name[[:space:]]*=[[:space:]]*"\(etk_turnip[^"]*\)".*/\1/p' "$PS1_FILE" | sort)
    if [ -n "$P_TN" ] && [ "$I_TN" = "$P_TN" ]; then
        ok "PS port Turnip catalog matches install.sh CERTIFIED_BUILDS"
    else
        bad "PS port Turnip catalog DRIFTED (ps1: $(echo $P_TN) vs install.sh: $(echo $I_TN))"
    fi
else
    skip "windows_installer/etk-install.ps1 not found"
fi

echo
if [ "$FAIL" = 0 ]; then
    printf "${c_ok}== release sanity: PASS ==${c_off}\n"
else
    printf "${c_bad}== release sanity: FAIL — fix artifact names before cutting ==${c_off}\n"
fi
exit "$FAIL"
