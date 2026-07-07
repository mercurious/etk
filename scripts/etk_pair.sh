#!/bin/bash
# ==========================================================
# ETK HOST -> RIG SSH PAIRING WIZARD  (etk_pair.sh)
# ==========================================================
# Idempotent, test-first, <=1 password. Pairs the host with the rig once so
# every subsequent ssh/scp from install.sh is silent. Safe to re-run: a
# reachable host pays zero passwords and adds zero duplicate keys.
#
# Design + rationale: dossiers/SSHPairingWizardDossier.md (§C flow, §E-R the
# on-rig recon, §F-R1 the Phase 1 decisions). Key facts proven on the rig:
#   - root home = /storage; AuthorizedKeysFile = .ssh/authorized_keys
#   - OpenSSH sshd (no dropbear); StrictModes no (perms can't silently reject)
#   - the Windows path appends keys with a stray CR (\r) -> must strip rig-side
#   - a real user key may already be in authorized_keys -> never clobber, and
#     dedup must compare CR-stripped (an old \r-terminated line won't match a
#     clean grep -qxF, which would otherwise append a duplicate).
#
# THE TEST THAT MATTERS: install.sh calls `ssh $RIG_SSH` (the BARE target,
# e.g. root@SM8250.local) -- NOT the etk-rig alias. So every probe here uses
# the bare target. Because the dedicated key (etk_rig) is not a default
# identity name, the ~/.ssh/config block MUST map the real host so the bare
# target offers it; otherwise pairing "succeeds" yet install.sh still gets
# barraged for passwords. (Regression caught 2026-05-29; see §F-R1.)
#
# SINGLE SOURCE OF TRUTH: the rig-side append/perms/CR-strip logic lives ONCE
# in the ETKPAIRKEY heredoc below. The PowerShell port (Phase 3) pulls that
# same body verbatim via Get-Heredoc (etk-common.ps1), exactly like SENTRY /
# SVC / ETKMAKO -- so the pairing logic cannot drift between the two hosts.
#
# Usage:
#   scripts/etk_pair.sh [user@host | host]   # explicit target
#   scripts/etk_pair.sh                       # uses $RIG_SSH or etk.conf
# ==========================================================

C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_ROOT="$(cd "$SELF_DIR/.." && pwd)"

KEY_FILE="$HOME/.ssh/etk_rig"
PUB_FILE="$KEY_FILE.pub"
SSH_CONFIG="$HOME/.ssh/config"
CFG_MARKER="# ETK pairing (etk_pair.sh) -- do not edit by hand"

# Probe opts: never prompt (BatchMode), fail fast, auto-accept the host key
# the first time (§B.5). NO -i / IdentitiesOnly here -- the bare-target probe
# must mirror exactly what install.sh does, letting ~/.ssh/config + default
# keys decide. The dedicated-key-only probe adds those opts explicitly.
PROBE_OPTS="-o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new"

# --- resolve target: arg > $RIG_SSH env > etk.conf (no env.sh side effects) ---
TARGET="${1:-}"
if [ -z "$TARGET" ] && [ -n "$RIG_SSH" ]; then
    TARGET="$RIG_SSH"
fi
if [ -z "$TARGET" ] && [ -f "$REPO_ROOT/etk.conf" ]; then
    TARGET=$(grep -E '^RIG_SSH=' "$REPO_ROOT/etk.conf" | head -n1 \
             | sed 's/^RIG_SSH=//; s/^"//; s/"$//')
fi
if [ -z "$TARGET" ]; then
    echo -e "${R}[FAIL] No rig target.${N}"
    echo -e "       Pass one (e.g. scripts/etk_pair.sh root@SM8250.local),"
    echo -e "       or set RIG_SSH in etk.conf first (run ./install.sh)."
    exit 1
fi

# Normalize: split user@host; default user root if a bare host was given.
case "$TARGET" in
    *@*) RIG_USER="${TARGET%%@*}"; RIG_HOST="${TARGET#*@}" ;;
    *)   RIG_USER="root";          RIG_HOST="$TARGET"; TARGET="root@$TARGET" ;;
esac

# --- helpers -------------------------------------------------------------
# Bare-target probe: the real install.sh path (config + default keys).
probe_bare() {
    ssh $PROBE_OPTS "$TARGET" "echo ETK_OK" 2>/dev/null | grep -q ETK_OK
}
# Dedicated-key-only probe: is OUR etk_rig key accepted by the rig, regardless
# of whether ~/.ssh/config routes the bare target to it yet?
probe_etkrig() {
    [ -f "$KEY_FILE" ] || return 1
    ssh $PROBE_OPTS -o IdentitiesOnly=yes -i "$KEY_FILE" "$TARGET" "echo ETK_OK" 2>/dev/null | grep -q ETK_OK
}
# Append the config block once (marker-guarded; never clobbers, §G.4). The
# multi-pattern `Host <host> etk-rig` is what makes the BARE target offer the
# dedicated key -- the load-bearing fix.
ensure_config_block() {
    mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh" 2>/dev/null
    if grep -qF "$CFG_MARKER" "$SSH_CONFIG" 2>/dev/null; then
        return 0
    fi
    [ -s "$SSH_CONFIG" ] && printf '\n' >> "$SSH_CONFIG"
    {
        printf '%s\n' "$CFG_MARKER"
        printf 'Host %s etk-rig\n' "$RIG_HOST"
        printf '    HostName %s\n' "$RIG_HOST"
        printf '    User %s\n' "$RIG_USER"
        # ~ form is portable: OpenSSH expands it on Mac/Linux, Git Bash, AND
        # Windows OpenSSH (an absolute MSYS path like /c/Users/... would break
        # the Windows ssh.exe the PowerShell port uses against the same file).
        printf '    IdentityFile ~/.ssh/etk_rig\n'
        printf '    IdentitiesOnly yes\n'
    } >> "$SSH_CONFIG"
    chmod 600 "$SSH_CONFIG" 2>/dev/null
    echo -e "    ${G}[OK] Added ETK block to ${SSH_CONFIG} (routes ${RIG_HOST} -> etk_rig).${N}"
}
# Persist the resolved target to etk.conf if that file exists (install.sh
# creates it during discovery; we only refresh the value).
persist_conf() {
    local conf="$REPO_ROOT/etk.conf"
    [ -f "$conf" ] || return 0
    if grep -qE '^RIG_SSH=' "$conf"; then
        sed -i.bak "s|^RIG_SSH=.*|RIG_SSH=\"$TARGET\"|" "$conf" && rm -f "$conf.bak"
    else
        printf 'RIG_SSH="%s"\n' "$TARGET" >> "$conf"
    fi
}

echo -e "${C}>>> ETK SSH pairing — target: ${TARGET}${N}"

# --- STEP 1: test first (the real path). Reachable already? Zero passwords. -
# Passes for an existing working setup (user's own key) OR a prior correct
# ETK pair -> behavior unchanged when already paired (§G.6).
if probe_bare; then
    echo -e "    ${G}[OK] Already reachable passwordlessly — nothing to do.${N}"
    exit 0
fi

# --- STEP 2: ensure a dedicated, no-passphrase key (never clobber the user's
#     own id_*). Empty -N => no agent, no future prompts. --------------------
if [ ! -f "$KEY_FILE" ]; then
    mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh" 2>/dev/null
    echo -e "    ${C}>>> Generating dedicated key ${KEY_FILE} (no passphrase)...${N}"
    if ! ssh-keygen -t ed25519 -N "" -f "$KEY_FILE" -C "etk-host" >/dev/null; then
        echo -e "    ${R}[FAIL] ssh-keygen failed.${N}"; exit 1
    fi
fi
[ -f "$PUB_FILE" ] || { echo -e "    ${R}[FAIL] Public key ${PUB_FILE} missing.${N}"; exit 1; }

# --- STEP 3: install the key on the rig. If our key is ALREADY accepted (so
#     STEP 1 only failed because the bare target wasn't routed), skip the
#     password entirely and just fix the config. Otherwise: THE one password.
if probe_etkrig; then
    echo -e "    ${G}[OK] Dedicated key already accepted by rig — no password needed.${N}"
else
    PUBKEY=$(head -n1 "$PUB_FILE" | tr -d '\r\n')
    [ -z "$PUBKEY" ] && { echo -e "    ${R}[FAIL] Empty public key.${N}"; exit 1; }
    echo -e "    ${Y}>>> Pairing — you will be asked for the rig password ONCE${N}"
    echo -e "    ${Y}    (Rocknix default is 'rocknix' unless you changed it).${N}"
    PAIR_OUT=$(ssh -o StrictHostKeyChecking=accept-new "$TARGET" "ETK_PUBKEY='$PUBKEY' bash -s" <<'ETKPAIRKEY'
# --- ETK rig-side key install (SINGLE SOURCE OF TRUTH; do not duplicate) ---
# Input: $ETK_PUBKEY = the host public key line, supplied by the caller. Both
# hosts pass it as an env var so this body is transport-agnostic:
#   bash:  ssh "$TARGET" "ETK_PUBKEY='...' bash -s"  <<'ETKPAIRKEY'
#   PS:    ssh $RigSsh "echo <base64 of: ETK_PUBKEY=...\n<this body>> | base64 -d | bash"
# Busybox-safe: tr / grep -qxF / printf / mkdir / chmod / touch only.
KEY=$(printf '%s' "$ETK_PUBKEY" | tr -d '\r')
[ -z "$KEY" ] && { echo "ETK_PAIR_ERR empty-key"; exit 3; }
SSH_DIR="$HOME/.ssh"
AUTH="$SSH_DIR/authorized_keys"
mkdir -p "$SSH_DIR" || { echo "ETK_PAIR_ERR mkdir $SSH_DIR"; exit 4; }
chmod 700 "$SSH_DIR" 2>/dev/null
touch "$AUTH" 2>/dev/null || { echo "ETK_PAIR_ERR touch $AUTH"; exit 5; }
# Repair any pre-existing CRLF lines so the dedup compare is clean and the
# file is well-formed (Phase 0 found an old etk-host key appended \r\n).
if [ -s "$AUTH" ]; then
    CLEAN=$(tr -d '\r' < "$AUTH")
    printf '%s\n' "$CLEAN" > "$AUTH"
fi
# Append only if the exact (CR-stripped) line is absent. Preserves all
# other keys (e.g. an existing Mac user key).
if grep -qxF "$KEY" "$AUTH"; then
    echo "ETK_PAIR_OK already-present"
else
    printf '%s\n' "$KEY" >> "$AUTH"
    echo "ETK_PAIR_OK appended"
fi
chmod 600 "$AUTH" 2>/dev/null
ETKPAIRKEY
)
    PAIR_RC=$?
    if [ $PAIR_RC -ne 0 ] || ! echo "$PAIR_OUT" | grep -q ETK_PAIR_OK; then
        echo -e "    ${R}[FAIL] Key install did not complete.${N}"
        [ -n "$PAIR_OUT" ] && echo "       rig said: $PAIR_OUT"
        echo -e "       (Wrong password, or no SSH route to ${TARGET}.)"
        exit 1
    fi
    echo -e "    ${G}[OK] Key installed on rig (${PAIR_OUT#ETK_PAIR_OK }).${N}"
fi

# --- STEP 4: config block so the BARE target offers the dedicated key. ------
ensure_config_block

# --- STEP 5: verify the REAL path — bare-target, passwordless. --------------
if probe_bare; then
    echo -e "    ${G}[OK] Verified: 'ssh ${TARGET}' is passwordless. Pairing complete.${N}"
    persist_conf
    exit 0
fi

# Bare target still fails — diagnose precisely (§G.7).
echo -e "    ${R}[FAIL] Verification failed — 'ssh ${TARGET}' still needs a password.${N}"
if probe_etkrig; then
    echo -e "    ${Y}    The key works, but the bare target isn't routed to it.${N}"
    echo -e "       Check the ETK block in ${SSH_CONFIG} maps Host '${RIG_HOST}'"
    echo -e "       to IdentityFile '${KEY_FILE}' (it should, unless hand-edited)."
else
    echo -e "    ${Y}    The rig is not accepting the key. Most likely on this build:${N}"
    echo -e "       - wrong AuthorizedKeysFile path (expected \$HOME/.ssh/authorized_keys)"
    echo -e "       - key never landed (perms/route) — retry, or use the manual fallback"
    echo -e "    ${Y}    Manual fallback: WINDOWS_HOST_README.md 'First-time SSH handshake'.${N}"
fi
exit 1
