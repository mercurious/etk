#!/usr/bin/env bash
# ==========================================================================
# tools/release_sanity.sh — ETK release sanity gates (run at every cut)
# --------------------------------------------------------------------------
# CHECK: artifact filenames are VERSION-ONLY (AI_MANIFEST law #8).
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
if [ -n "$_CERT" ] && command -v curl >/dev/null 2>&1; then
    _code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' -L \
        "https://github.com/mercurious/etk/releases/latest/download/$_CERT" 2>/dev/null || echo 000)
    case "$_code" in
        200) ok "CERT_RPCS3 resolves on releases/latest (HTTP 200)" ;;
        000) skip "CERT_RPCS3 reachability: no network" ;;
        *)   skip "CERT_RPCS3 returns HTTP $_code on releases/latest — expected until this cut publishes" ;;
    esac
fi

echo
if [ "$FAIL" = 0 ]; then
    printf "${c_ok}== release sanity: PASS ==${c_off}\n"
else
    printf "${c_bad}== release sanity: FAIL — fix artifact names before cutting ==${c_off}\n"
fi
exit "$FAIL"
