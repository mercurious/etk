#!/bin/sh
# test_pair_usb.sh — pins etk_pair.sh's USB-gadget routing.
#
# WHY: pairing routed only the ONE host it ran against, and its config block is
# written once (marker-guarded). A rig paired over WiFi (root@SM8250.local)
# therefore stayed password-prompted on the USB link (root@169.254.170.2)
# forever — re-running etk_pair.sh exited at STEP 1 ("already reachable")
# before it ever touched ~/.ssh/config (2026-10-05).
#
# HOW: runs the real etk_pair.sh in a sandbox HOME with a fake `ssh` on PATH
# that models OpenSSH routing: a probe succeeds when it passes `-i` (the
# dedicated-key probe), or when ~/.ssh/config has a Host line naming the bare
# target with the etk_rig key. No network, no rig, no real ~/.ssh touched.
#
# DISCRIMINATION: `--against <rev>` runs the suite on that revision's
# etk_pair.sh. Against the pre-fix code (84474b3) five cases FAIL (WiFi-paired x3,
# fresh-pair USB, lookalike: it never writes a USB block); the dedup and
# user-key cases pass either way — they pin what the fix must NOT do.
#
#   tools/test_pair_usb.sh                   # working tree
#   tools/test_pair_usb.sh --against 84474b3 # must FAIL (pre-fix)

set -u
cd "$(dirname "$0")/.." || exit 1

REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"

TD=$(mktemp -d); [ -n "${KEEP:-}" ] || trap 'rm -rf "$TD"' EXIT; [ -n "${KEEP:-}" ] && echo "sandbox: $TD"
PAIR="$TD/etk_pair.sh"
if [ -n "$REV" ]; then git show "$REV:scripts/etk_pair.sh" > "$PAIR" || exit 1
else cp scripts/etk_pair.sh "$PAIR"; fi
# etk_pair.sh resolves REPO_ROOT from its own dir; give it an empty one
mkdir -p "$TD/scripts" && mv "$PAIR" "$TD/scripts/etk_pair.sh"; PAIR="$TD/scripts/etk_pair.sh"

# --- fake ssh: routing model, not transport ---------------------------------
mkdir -p "$TD/bin"
cat > "$TD/bin/ssh" <<'FAKE'
#!/bin/sh
# last non-option arg before the remote command is the target; we only need to
# know whether -i was passed, and which host was named.
withkey=0 target=""
while [ $# -gt 0 ]; do
    case "$1" in
        -i) withkey=1; shift 2; continue ;;
        -o) shift 2; continue ;;
        -*) shift; continue ;;
        *)  [ -z "$target" ] && target="$1"; shift ;;
    esac
done
host="${target#*@}"
# -i + IdentitiesOnly offers ONLY etk_rig: the rig's verdict on that key is final
if [ "$withkey" = 1 ]; then
    [ "${FAKE_RIG_ACCEPTS_KEY:-1}" = 1 ] && { echo ETK_OK; exit 0; }
    exit 255
fi
# bare probe: OpenSSH offers etk_rig only if a Host line names this host AND
# that block carries the key (the block shapes we write)
if awk -v h="$host" '
    tolower($1)=="host" { inb=0; for (i=2;i<=NF;i++) if ($i==h) inb=1; next }
    inb && tolower($1)=="identityfile" && $2 ~ /etk_rig$/ { ok=1 }
    END { exit !ok }' "$HOME/.ssh/config" 2>/dev/null; then
    echo ETK_OK; exit 0
fi
[ "${FAKE_USER_KEY_HOST:-}" = "$host" ] && { echo ETK_OK; exit 0; }   # user's own key works there
exit 255
FAKE
chmod +x "$TD/bin/ssh"

PASS=0 FAIL=0
chk() { if eval "$2"; then PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"
        else FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fi; }

fresh_home() {  # <name> -> HOME with an etk_rig key and an empty config
    H="$TD/home-$1"; mkdir -p "$H/.ssh"
    : > "$H/.ssh/etk_rig"; echo "ssh-ed25519 AAAAtest etk-host" > "$H/.ssh/etk_rig.pub"
    : > "$H/.ssh/config"
}
run_pair() {  # <target> [env...]
    t="$1"; shift
    env "$@" HOME="$H" PATH="$TD/bin:$PATH" RIG_SSH= bash "$PAIR" "$t" >"$H/out" 2>&1
}
usb_blocks() { grep -cE '^Host .*169\.254\.170\.2( |$)' "$H/.ssh/config"; }

echo "== etk_pair.sh USB-gadget routing ${REV:+(against $REV)}"

# 1. Already paired over WiFi (main block exists), USB never routed — the bug.
fresh_home wifi
printf '%s\n' "# ETK pairing (etk_pair.sh) -- do not edit by hand" "Host SM8250.local etk-rig" \
    "    HostName SM8250.local" "    User root" "    IdentityFile ~/.ssh/etk_rig" "    IdentitiesOnly yes" > "$H/.ssh/config"
run_pair root@SM8250.local
chk "WiFi-paired rig: re-run routes the USB link" '[ "$(usb_blocks)" = 1 ]'
chk "WiFi-paired rig: USB bare target now passwordless" 'HOME="$H" "$TD/bin/ssh" root@169.254.170.2 echo ETK_OK >/dev/null'
run_pair root@SM8250.local
chk "idempotent: a second run adds no second USB block" '[ "$(usb_blocks)" = 1 ]'

# 2. Fresh pair over WiFi (key accepted, nothing routed yet): both blocks land.
fresh_home fresh
run_pair root@SM8250.local
chk "fresh pair: main block written" 'grep -q "^Host SM8250.local etk-rig" "$H/.ssh/config"'
chk "fresh pair: USB block written too" '[ "$(usb_blocks)" = 1 ]'

# 3. Pairing AGAINST the gadget IP: the main block already routes it — no dup.
fresh_home ip
run_pair root@169.254.170.2
chk "paired via the IP: exactly one block names it" '[ "$(usb_blocks)" = 1 ]'

# 4. A hand-added block already routes the IP — left alone.
fresh_home hand
printf '%s\n' "Host 169.254.170.2 etk-rig-usb" "    HostName 169.254.170.2" "    User root" \
    "    IdentityFile ~/.ssh/etk_rig" "    IdentitiesOnly yes" > "$H/.ssh/config"
run_pair root@SM8250.local
chk "hand-routed IP: no second block" '[ "$(usb_blocks)" = 1 ]'

# 5. Reachable via the USER's own key, rig does NOT accept etk_rig: never
#    reroute the USB link to a key the rig refuses.
fresh_home userkey
run_pair root@SM8250.local FAKE_USER_KEY_HOST=SM8250.local FAKE_RIG_ACCEPTS_KEY=0
chk "user-key rig, etk_rig refused: USB link untouched" '[ "$(usb_blocks)" = 0 ]'

# 6. Lookalike address must not count as routed (169.254.170.20 != .2).
fresh_home lookalike
printf '%s\n' "Host 169.254.170.20" "    HostName 169.254.170.20" > "$H/.ssh/config"
run_pair root@SM8250.local
chk "169.254.170.20 does not mask .2" '[ "$(usb_blocks)" = 1 ]'

printf '%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
