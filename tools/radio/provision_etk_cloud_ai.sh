#!/usr/bin/env bash
# ==========================================================
# ETK RADIO -- provision etk-cloud-ai on the node
# ==========================================================
# !! THE OPERATOR RUNS THIS SCRIPT. CLAUDE NEVER DOES -- not with --dry-run,
# !! not "just to check", not one step of it by hand over ssh.
#
# WHY (TRACK_MANUAL section 1.1, Law #9): this crosses TWO of the three
# bytes-to-atoms thresholds at once.
#   * MINT -- it runs on SOMEONE ELSE'S COMPUTER and pulls ~10 GB of model
#     weights. The Always-Free tenancy is free until it isn't; egress, storage
#     and a tenancy someone's name is on are atoms. Money is atoms.
#   * DEPLOY -- it installs a systemd unit, replaces an Ollama drop-in, mints a
#     bearer token, and stands up a public TLS listener. A misconfigured line
#     here is a model server on 0.0.0.0.
# --dry-run, --status and --check are NOT exemptions anywhere in this kit, and
# there is deliberately no --dry-run here to be tempted by. Claude's job is to
# prepare the inputs -- this file, the Modelfiles, the unit, the Caddyfile, the
# config -- and then hand off ONE command in a fenced block. Read this file;
# don't run it.
#
# WHAT IT DOES (docs/RADIO_SPEC.md section 9), each step printing a
# "you know it worked when" line so the operator can falsify it on the spot:
#   1. install Ollama (installer FETCHED TO A FILE and its head PRINTED first)
#   2. /etc/systemd/system/ollama.service.d/override.conf: loopback + one model
#   3. daemon-reload, restart ollama
#   4. ollama pull <model> and <fast>
#   5. ollama create etk-radio:9b / etk-radio:4b from tools/radio/Modelfile.*
#   6. /etc/etk-radio/{config.json,token} -- the token minted and printed ONCE
#   7. install + enable etk-radio.service
#   8. .env (asks for SITE_ADDRESS) + docker compose up -d caddy
#   9. print the two doors to open and the verification commands
#
# Usage (on the node, from the repo checkout):
#   tools/radio/provision_etk_cloud_ai.sh [--model qwen3.5:9b] [--fast qwen3.5:4b]
#                                         [--yes] [--skip-caddy] [--skip-models]
#   --yes         do not pause before running the fetched Ollama installer
#   --skip-caddy  service only; no TLS front door yet (health over `ssh -L`)
#   --skip-models assume the two models are already pulled and tagged
#
# It is idempotent by design: every step checks before it writes, and the token
# is minted ONCE and never re-minted by a re-run.
# ==========================================================
set -euo pipefail

MODEL="qwen3.5:9b"
FAST="qwen3.5:4b"
ASSUME_YES=0
SKIP_CADDY=0
SKIP_MODELS=0

while [ $# -gt 0 ]; do
    case "$1" in
        --model)  MODEL="${2:?--model needs a value}"; shift 2 ;;
        --fast)   FAST="${2:?--fast needs a value}"; shift 2 ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --skip-caddy)  SKIP_CADDY=1; shift ;;
        --skip-models) SKIP_MODELS=1; shift ;;
        -h|--help) sed -n '1,45p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
CONF_DIR="/etc/etk-radio"
TOKEN_FILE="$CONF_DIR/token"
CONFIG_FILE="$CONF_DIR/config.json"
UNIT_SRC="$HERE/etk-radio.service"
UNIT_DST="/etc/systemd/system/etk-radio.service"
DROPIN_DIR="/etc/systemd/system/ollama.service.d"
DROPIN="$DROPIN_DIR/override.conf"
OLLAMA_INSTALLER="$HOME/ollama-install.sh"
STEP=0

step()  { STEP=$((STEP + 1)); printf '\n=== STEP %d: %s\n' "$STEP" "$1"; }
know()  { printf '    you know it worked when: %s\n' "$1"; }
note()  { printf '    %s\n' "$1"; }
die()   { printf '\nFAILED at step %d: %s\n' "$STEP" "$1" >&2; exit 1; }

confirm() {
    [ "$ASSUME_YES" = "1" ] && return 0
    printf '\n    %s [y/N] ' "$1"
    read -r reply
    case "$reply" in y|Y|yes|YES) return 0 ;; *) die "stopped by the operator" ;; esac
}

command -v sudo >/dev/null 2>&1 || die "sudo is required (systemd units, /etc)"
[ -f "$UNIT_SRC" ] || die "run this from the repo checkout: $UNIT_SRC is missing"

printf '==========================================================\n'
printf 'ETK RADIO provisioning -- debrief model %s, fast model %s\n' "$MODEL" "$FAST"
printf 'repo: %s\n' "$REPO"
printf '==========================================================\n'

# ---------------------------------------------------------------- 1. ollama
step "install Ollama"
if command -v ollama >/dev/null 2>&1; then
    note "already installed: $(ollama --version 2>&1 | head -n 1)"
    know "the line above names a version"
else
    note "fetching the installer to a FILE first -- nothing is piped into a shell here"
    curl -fsSL --connect-timeout 15 -o "$OLLAMA_INSTALLER" https://ollama.com/install.sh \
        || die "could not fetch the Ollama installer"
    printf '\n--- first 40 lines of %s ---\n' "$OLLAMA_INSTALLER"
    head -n 40 "$OLLAMA_INSTALLER"
    printf '--- (%s lines, %s bytes total) ---\n' \
        "$(wc -l < "$OLLAMA_INSTALLER")" "$(wc -c < "$OLLAMA_INSTALLER")"
    confirm "run this installer?"
    sh "$OLLAMA_INSTALLER" || die "the Ollama installer failed"
    know "'ollama --version' answers and 'systemctl status ollama' is active"
fi

# ------------------------------------------------------- 2. the systemd drop-in
step "pin Ollama to loopback, one model, 30-minute keep-alive"
sudo mkdir -p "$DROPIN_DIR"
sudo tee "$DROPIN" >/dev/null <<'EOF'
# ETK RADIO (docs/RADIO_SPEC.md sections 1.6, 5, 9). Written by
# tools/radio/provision_etk_cloud_ai.sh.
#
# OLLAMA_HOST is the load-bearing line: the model must NEVER be reachable except
# through the service on 127.0.0.1:8737, which checks the bearer. NUM_PARALLEL=1
# and MAX_LOADED_MODELS=1 keep one 8 GiB resident set on a 23 GB box that also
# has to build the kit; KEEP_ALIVE=30m holds the prefix-cached doctrine between
# calls (measured: 82-118 tok/s warm against 17.9 cold on the 9b).
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
EOF
know "cat $DROPIN shows exactly those four Environment lines"

step "reload systemd and restart Ollama"
sudo systemctl daemon-reload || die "daemon-reload failed"
sudo systemctl restart ollama || die "could not restart ollama"
sleep 3
know "ss -ltn | grep 11434 shows 127.0.0.1:11434 and NOTHING on 0.0.0.0"
ss -ltn 2>/dev/null | grep -E '11434' || note "(nothing listening on 11434 yet -- give it a moment and re-check)"

# ------------------------------------------------------------------ 4. pulls
if [ "$SKIP_MODELS" = "1" ]; then
    step "model pulls -- SKIPPED (--skip-models)"
else
    step "pull $MODEL and $FAST (this is the multi-GB part; minutes, not seconds)"
    ollama pull "$MODEL" || die "could not pull $MODEL"
    ollama pull "$FAST"  || die "could not pull $FAST"
    know "ollama list shows both, ~6.3 GB and ~3.4 GB on disk"
    ollama list
fi

# --------------------------------------------------------------- 5. Modelfiles
step "create etk-radio:9b and etk-radio:4b from the Modelfiles"
MF_DEBRIEF="$HERE/Modelfile.debrief"
MF_FAST="$HERE/Modelfile.fast"
for mf in "$MF_DEBRIEF" "$MF_FAST"; do
    [ -f "$mf" ] || die "missing $mf -- is this checkout on the 'radio' branch and up to date?"
done
note "the Modelfiles carry the doctrine (spec 4.2); their sha256 becomes the"
note "debrief's prompt_sha256, which is the tune_tag of advice"
( cd "$HERE" && ollama create etk-radio:9b -f "$MF_DEBRIEF" ) || die "ollama create etk-radio:9b failed"
( cd "$HERE" && ollama create etk-radio:4b -f "$MF_FAST" )    || die "ollama create etk-radio:4b failed"
know "ollama list shows etk-radio:9b and etk-radio:4b beside the base models"

# ------------------------------------------------------------ 6. config + token
step "write $CONFIG_FILE and mint the bearer token"
sudo mkdir -p "$CONF_DIR"
if [ -f "$CONFIG_FILE" ]; then
    note "$CONFIG_FILE already exists -- left alone (edit it by hand to change a knob)"
else
    # The example is the contract; only the home-relative paths are filled in, so a
    # key added to the example on a later pull lands here on the next provision.
    python3 - "$HERE/config.example.json" "$HOME" <<'PY' | sudo tee "$CONFIG_FILE" >/dev/null
import json, os, sys
src, home = sys.argv[1], sys.argv[2]
cfg = json.load(open(src, encoding="utf-8"))
cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
for key in ("jobs_db", "results_dir", "packs_dir", "corpus_root", "forge_runs_dir"):
    cfg[key] = os.path.join(home, str(cfg[key]).replace("~/", "", 1))
print(json.dumps(cfg, indent=2))
PY
    sudo chmod 0644 "$CONFIG_FILE"
    know "python3 -c 'import json;json.load(open(\"$CONFIG_FILE\"))' is silent"
fi

if sudo test -s "$TOKEN_FILE"; then
    note "a token already exists at $TOKEN_FILE -- NOT re-minted."
    note "re-minting would break the rig's radio.json until the next install.sh."
    note "to rotate deliberately: sudo rm $TOKEN_FILE and run this script again."
else
    umask 077
    openssl rand -hex 32 | sudo tee "$TOKEN_FILE" >/dev/null || die "could not mint the token"
    sudo chmod 0600 "$TOKEN_FILE"
    sudo chown root:root "$TOKEN_FILE"
    printf '\n    ---------------------------------------------------------------\n'
    printf '    RADIO_TOKEN (printed ONCE -- copy it into etk.conf on the laptop):\n\n'
    printf '      %s\n' "$(sudo cat "$TOKEN_FILE")"
    printf '\n    ---------------------------------------------------------------\n'
    know "the line above is 64 hex characters, and it never appears in a log again"
fi
# The service runs as ubuntu and must be able to read the token.
sudo chown root:ubuntu "$TOKEN_FILE" 2>/dev/null || true
sudo chmod 0640 "$TOKEN_FILE"

# ---------------------------------------------------------------- 7. the unit
step "install and enable etk-radio.service"
sudo install -m 0644 "$UNIT_SRC" "$UNIT_DST" || die "could not install the unit"
sudo systemctl daemon-reload
sudo systemctl enable --now etk-radio.service || die "could not start etk-radio.service"
sleep 2
systemctl --no-pager --lines=5 status etk-radio.service || true
know "ss -ltn shows 127.0.0.1:8737 (never 0.0.0.0) and journalctl -u etk-radio names both models"
ss -ltn 2>/dev/null | grep -E '8737' || note "(nothing on 8737 yet -- read journalctl -u etk-radio -n 50)"

# ------------------------------------------------------------------ 8. caddy
if [ "$SKIP_CADDY" = "1" ]; then
    step "Caddy -- SKIPPED (--skip-caddy). Reach the service over: ssh -N -L 8737:127.0.0.1:8737 etk-cloud"
else
    step "front door: .env + Caddy"
    if [ -f "$HERE/.env" ]; then
        note ".env already exists: SITE_ADDRESS=$(grep -E '^SITE_ADDRESS=' "$HERE/.env" | cut -d= -f2-)"
    else
        printf '\n    SITE_ADDRESS is the node RESERVED IP with dots as dashes plus .sslip.io\n'
        printf '    (example shape: 203-0-113-7.sslip.io). Enter it: '
        read -r site
        [ -n "$site" ] || die "no SITE_ADDRESS given"
        sed "s|^SITE_ADDRESS=.*|SITE_ADDRESS=$site|" "$HERE/.env.example" > "$HERE/.env"
        chmod 0600 "$HERE/.env"
        know "grep SITE_ADDRESS $HERE/.env shows your node, not the 203-0-113-7 placeholder"
    fi
    command -v docker >/dev/null 2>&1 || die "docker is not installed on this node"
    ( cd "$HERE" && docker compose up -d caddy ) || die "docker compose up failed"
    know "docker ps shows etk-radio-caddy up, and docker logs etk-radio-caddy names a certificate"
fi

# ------------------------------------------------------------ 9. the two doors
step "THE TWO DOORS (the operator opens these; this script does not)"
cat <<'DOORS'
    1. TENANCY SECURITY LIST -- ingress TCP 80 and 443 from 0.0.0.0/0.
       Without 80 the ACME HTTP challenge cannot complete and there is no
       certificate; without 443 the rig cannot reach the service at all.
    2. THE NODE'S OWN iptables -- the free-tier Ubuntu image ships a default
       INPUT policy that drops everything but ssh. Both ports again, and the
       rule has to survive a reboot (netfilter-persistent / iptables-save).

    Opening ports is an infrastructure change on someone's tenancy: it is a
    bytes-to-atoms act and it stays in the operator's hand. Nothing above
    touched a firewall.
DOORS

step "VERIFY (run these; each one can falsify the install)"
cat <<'VERIFY'
    ON THE NODE:
      ss -ltn
        -> MUST show 127.0.0.1:11434 and 127.0.0.1:8737
        -> MUST NOT show 0.0.0.0:11434 or 0.0.0.0:8737. Either one is a stop.
      journalctl -u etk-radio -n 50 --no-pager
        -> "listening on 127.0.0.1:8737 (models etk-radio:9b / etk-radio:4b ...)"
      curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8737/v1/health
        -> 401. An unauthenticated 200 is a STOP: the bearer check is not running.

    FROM THE LAPTOP (token in a header file, never in argv -- paddock_sync law):
      umask 077; printf 'Authorization: Bearer %s\n' "$RADIO_TOKEN" > /dev/shm/h
      curl -fsS -H "@/dev/shm/h" https://<SITE_ADDRESS>/v1/health; rm -f /dev/shm/h
        -> JSON naming both models, "ollama": true, and a corpus_commit
      curl -s -o /dev/null -w '%{http_code}\n' https://<SITE_ADDRESS>/v1/health
        -> 401 with no body. If this answers 200, STOP and re-read the token file.

    THEN: etk.conf on the laptop gets RADIO_URL + RADIO_TOKEN, and ./install.sh
    STEP 7b writes radio.json to the rig. That is a separate operator run.
VERIFY

printf '\nprovisioning complete.\n'
