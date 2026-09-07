#!/bin/sh
# ==========================================================
# ETK RADIO SENDER (rig-side) — docs/RADIO_SPEC.md §8
# ==========================================================
# Hand ONE session to the race engineer on the node, collect the debrief,
# and put its headline on the TELEMETRY PIT NOTE. Nothing else on the rig
# talks to the node.
#
#   radio_send.sh debrief <epoch>   build/POST the pack, poll, store, toast
#   radio_send.sh drain             GET every job parked in pending/
#   radio_send.sh ask <question-id> one question on the fast model (sync)
#   radio_send.sh health            the node's /v1/health, printed
#
# question-ids (§7.3 RADIO CHECK): why_crash arm_ahead next_run explain_row
#                                  cpu_or_gpu
#
# GATES (§1 decision 9, default-off and fail-soft):
#   ETK_RADIO=0                 -> exit 0, silently. The kill switch.
#   no config/radio.json        -> exit 0, silently. The feature does not exist.
# NOTHING HERE CAN FAIL THE CALLER. session_postmortem hands this to nohup and
# walks away; every exit code below is for radio/radio.log and for a human
# reading it, never for the postmortem's <2 s path.
#
# TOKEN LAW (bin/paddock_sync.sh's, inherited verbatim): the token must NEVER
# appear in argv or in a log. It travels only in a header file under /dev/shm,
# written under umask 077 and removed on exit, and reaches curl as -H "@$HDR".
# Grep this file: "$TOKEN" appears exactly once, in the printf that writes that
# file. If you ever add a curl line that carries it any other way,
# tools/radio/test_service.py fails.
#
# BUSYBOX (POSIX sh only; the rig has no bash for this path). Constructs
# deliberately avoided, each one a real ash failure: [[ ]] · local · arrays ·
# ${var,,}/${var^^} · $'...' · echo -e/-n (printf only) · += · function kw ·
# process substitution · here-strings · set -o pipefail · trap ERR · read -a ·
# mapfile · GNU long options on busybox applets · sed -i · stat --format ·
# find -printf · grep -P · seq · mktemp --tmpdir. Arithmetic stays integer:
# `sh` cannot do decimals (awk would, and is not needed here).
#
# env.sh is BASH (it expands ${BASH_SOURCE[0]}), so a POSIX sh cannot simply
# source it — a bad substitution would abort us. It is sourced in a SUBSHELL
# whose death costs us the values and never the run, and every path we need is
# re-derived from ETK_ROOT with a fallback, the rule bin/radio_pack.py follows.
#
# Exit codes (log only): 0 ok/gated-off · 2 no jq or curl · 3 bad credential ·
# 4 pack unavailable · 5 submit failed · 6 job failed on the node ·
# 7 debrief unreadable · 8 pit note not written · 9 nothing to ask about ·
# 10 ask failed · 11 health failed · 64 usage.
# ==========================================================

ETK_ROOT="${ETK_ROOT:-/storage/games-internal/roms/etk}"

_ENV_DUMP=$( ( . "$ETK_ROOT/scripts/env.sh" >/dev/null 2>&1
               printf '%s\n%s\n' "${TELEMETRY_DIR:-}" "${PIT_NOTE_FILE:-}" ) 2>/dev/null )
set -u

TELEMETRY_DIR=$(printf '%s\n' "$_ENV_DUMP" | sed -n 1p)
PIT_NOTE_FILE=$(printf '%s\n' "$_ENV_DUMP" | sed -n 2p)
[ -n "$TELEMETRY_DIR" ] || TELEMETRY_DIR="$ETK_ROOT/etk_telemetry"
[ -n "$PIT_NOTE_FILE" ] || PIT_NOTE_FILE="$TELEMETRY_DIR/pit_note.txt"

RADIO_DIR="$TELEMETRY_DIR/radio"
PENDING_DIR="$RADIO_DIR/pending"
LOGFILE="$RADIO_DIR/radio.log"
ASKS_LOG="$RADIO_DIR/asks.log"
NOTIFY="$ETK_ROOT/bin/etk_notify.sh"
PACKER="$ETK_ROOT/bin/radio_pack.py"
CRED="${RADIO_CRED:-/storage/roms/etk/config/radio.json}"
POLL_S="${RADIO_POLL_S:-20}"
WAIT_S="${RADIO_WAIT_S:-720}"
INTERACTIVE="${RADIO_INTERACTIVE:-0}"

# --- gates, before anything is created, opened, or logged -------------------
[ "${ETK_RADIO:-1}" = "0" ] && exit 0
[ -f "$CRED" ] || exit 0

umask 077
WORK="${TMPDIR:-/tmp}/radio_send.$$"
HDR="/dev/shm/radio_hdr.$$"
mkdir -p "$WORK" 2>/dev/null
trap 'rm -rf "$WORK"; rm -f "$HDR"' EXIT INT TERM

log() {
    mkdir -p "$RADIO_DIR" 2>/dev/null
    printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >> "$LOGFILE" 2>/dev/null
    return 0
}

# §7.5: an AUTOMATIC send that fails is a log line, never a toast. Only an
# operator-pressed send (RADIO_INTERACTIVE=1) is allowed to interrupt the screen.
toast() {
    [ -f "$NOTIFY" ] || return 0
    sh "$NOTIFY" "$1" "${2:-}" >/dev/null 2>&1
    return 0
}
toast_fail() {
    [ "$INTERACTIVE" = "1" ] || return 0
    toast "RADIO: no signal" "${1:-the engineer did not answer}"
    return 0
}

command -v jq >/dev/null 2>&1 || { log "jq missing -- radio disabled"; exit 2; }
command -v curl >/dev/null 2>&1 || { log "curl missing -- radio disabled"; exit 2; }

URL=$(jq -r '.url // empty' "$CRED" 2>/dev/null)
TOKEN=$(jq -r '.token // empty' "$CRED" 2>/dev/null)
[ -n "$URL" ] || { log "bad credential: no url in $CRED"; exit 3; }
[ -n "$TOKEN" ] || { log "bad credential: no token in $CRED"; exit 3; }
URL=${URL%/}

printf 'Authorization: Bearer %s\n' "$TOKEN" > "$HDR"
TOKEN=""            # the value is in the header file now; keep no second copy

api_get() {         # api_get <path> <outfile>
    curl -fsS --connect-timeout 10 --max-time 30 -H "@$HDR" -o "$2" "$URL$1"
}
api_post() {        # api_post <path> <bodyfile> <outfile>
    curl -fsS --connect-timeout 10 --max-time 30 -H "@$HDR" \
        -H "Content-Type: application/json" --data-binary "@$2" -o "$3" "$URL$1"
}

# --- pit note (§7.1): atomic, ASCII, at most two lines ----------------------
write_pit_note() {
    [ -n "${1:-}" ] || return 0
    mkdir -p "$RADIO_DIR" 2>/dev/null
    printf '%s\n' "$1" | tr -d '\r' | tr -cd '\11\12\40-\176' | head -n 2 \
        > "$PIT_NOTE_FILE.tmp" 2>/dev/null || return 8
    mv "$PIT_NOTE_FILE.tmp" "$PIT_NOTE_FILE" 2>/dev/null || return 8
    return 0
}

# store_debrief <epoch> <job-response-file>
store_debrief() {
    OUT="$RADIO_DIR/$1.debrief.json"
    mkdir -p "$RADIO_DIR" 2>/dev/null
    jq -e '.debrief' "$2" > "$OUT.tmp" 2>/dev/null || {
        rm -f "$OUT.tmp"
        log "debrief $1: response carried no debrief"
        return 7
    }
    mv "$OUT.tmp" "$OUT" || { log "debrief $1: could not store"; return 7; }
    HEADLINE=$(jq -r '.debrief.headline // empty' "$2" 2>/dev/null)
    write_pit_note "$HEADLINE"
    log "debrief $1: stored $OUT"
    toast "RADIO: debrief ready" "$HEADLINE"
    return 0
}

# park_pending <job> <epoch>
park_pending() {
    mkdir -p "$PENDING_DIR" 2>/dev/null
    printf '{"job":"%s","epoch":"%s","sent":%s}\n' "$1" "$2" "$(date +%s)" \
        > "$PENDING_DIR/$1.json.tmp" 2>/dev/null || return 0
    mv "$PENDING_DIR/$1.json.tmp" "$PENDING_DIR/$1.json" 2>/dev/null
    log "debrief $2: job $1 still working after ${WAIT_S}s -- parked in pending/"
    return 0
}

# poll_job <job> <epoch>  -> 0 stored · 1 parked · 6 failed on the node
poll_job() {
    WAITED=0
    while [ "$WAITED" -lt "$WAIT_S" ]; do
        sleep "$POLL_S"
        WAITED=$((WAITED + POLL_S))
        if ! api_get "/v1/jobs/$1" "$WORK/job.json"; then
            log "debrief $2: poll failed (job $1) -- retrying"
            continue
        fi
        STATUS=$(jq -r '.status // empty' "$WORK/job.json" 2>/dev/null)
        case "$STATUS" in
            done)
                store_debrief "$2" "$WORK/job.json"
                return $?
                ;;
            failed)
                log "debrief $2: job $1 failed on the node: $(jq -r '.error // "no reason given"' "$WORK/job.json" 2>/dev/null)"
                toast_fail "the engineer could not finish row $2"
                return 6
                ;;
            queued|deferred|running)
                : ;;
            *)
                log "debrief $2: unexpected job status '$STATUS'" ;;
        esac
    done
    park_pending "$1" "$2"
    return 1
}

# The newest pack by its EPOCH, not by mtime: the epoch is the join key of every
# archive on this card, and a restored/copied file's mtime lies about which session it
# is. A glob, not `ls`, so no output parsing and no word splitting.
latest_pack_epoch() {
    _best=""
    for _f in "$RADIO_DIR"/*.pack.json; do
        [ -f "$_f" ] || continue
        _b=$(basename "$_f")
        _e="${_b%.pack.json}"
        case "$_e" in ''|*[!0-9]*) continue ;; esac
        if [ -z "$_best" ] || [ "$_e" -gt "$_best" ]; then
            _best="$_e"
        fi
    done
    [ -n "$_best" ] || return 1
    printf '%s\n' "$_best"
    return 0
}

# --- commands ---------------------------------------------------------------
cmd_debrief() {
    [ -n "${1:-}" ] || { log "debrief: no epoch given"; return 64; }
    PACK="$RADIO_DIR/$1.pack.json"
    if [ ! -s "$PACK" ]; then
        mkdir -p "$RADIO_DIR" 2>/dev/null
        if ! python3 "$PACKER" "$1" --out "$PACK" >/dev/null 2>&1; then
            log "debrief $1: pack build failed"
            toast_fail "no pack for row $1"
            return 4
        fi
    fi
    [ -s "$PACK" ] || { log "debrief $1: pack is empty"; return 4; }

    if ! api_post "/v1/debrief" "$PACK" "$WORK/submit.json"; then
        log "debrief $1: submit failed (node unreachable or rejected the pack)"
        toast_fail "could not reach the engineer"
        return 5
    fi
    JOB=$(jq -r '.job // empty' "$WORK/submit.json" 2>/dev/null)
    if [ -z "$JOB" ]; then
        log "debrief $1: node returned no job id"
        toast_fail "the engineer returned no job"
        return 5
    fi
    log "debrief $1: job $JOB queued"
    [ "$INTERACTIVE" = "1" ] && toast "RADIO: sent, engineer thinking" "row $1"
    poll_job "$JOB" "$1"
    return $?
}

cmd_drain() {
    [ -d "$PENDING_DIR" ] || { log "drain: nothing pending"; return 0; }
    COLLECTED=0
    for _p in "$PENDING_DIR"/*.json; do
        [ -f "$_p" ] || continue
        JOB=$(jq -r '.job // empty' "$_p" 2>/dev/null)
        EPOCH=$(jq -r '.epoch // empty' "$_p" 2>/dev/null)
        if [ -z "$JOB" ] || [ -z "$EPOCH" ]; then
            rm -f "$_p"
            continue
        fi
        if ! api_get "/v1/jobs/$JOB" "$WORK/job.json"; then
            log "drain: job $JOB unreachable -- left pending"
            continue
        fi
        STATUS=$(jq -r '.status // empty' "$WORK/job.json" 2>/dev/null)
        case "$STATUS" in
            done)
                if store_debrief "$EPOCH" "$WORK/job.json"; then
                    rm -f "$_p"
                    COLLECTED=$((COLLECTED + 1))
                fi
                ;;
            failed)
                log "drain: job $JOB failed on the node: $(jq -r '.error // "no reason given"' "$WORK/job.json" 2>/dev/null)"
                rm -f "$_p"
                ;;
            *)
                log "drain: job $JOB still $STATUS -- left pending" ;;
        esac
    done
    log "drain: collected $COLLECTED debrief(s)"
    return 0
}

cmd_ask() {
    [ -n "${1:-}" ] || { log "ask: no question id"; return 64; }
    EPOCH="${2:-}"
    [ -n "$EPOCH" ] || EPOCH=$(latest_pack_epoch)
    if [ -z "$EPOCH" ]; then
        log "ask: no pack on the rig to ask about"
        toast_fail "no session to ask about"
        return 9
    fi
    printf '{"pack_epoch":%s,"question_id":"%s"}\n' "$EPOCH" "$1" > "$WORK/ask.json"
    if ! api_post "/v1/ask" "$WORK/ask.json" "$WORK/answer.json"; then
        log "ask $1: the engineer did not answer for row $EPOCH"
        toast_fail "no answer on the radio"
        return 10
    fi
    ANSWER=$(jq -r '.answer // empty' "$WORK/answer.json" 2>/dev/null)
    printf '%s\n' "$ANSWER"
    mkdir -p "$RADIO_DIR" 2>/dev/null
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$EPOCH" "$1" "$ANSWER" \
        >> "$ASKS_LOG" 2>/dev/null
    log "ask $1: answered for row $EPOCH"
    return 0
}

cmd_health() {
    if ! api_get "/v1/health" "$WORK/health.json"; then
        log "health: no answer from $URL"
        toast_fail "the node is not answering"
        return 11
    fi
    jq . "$WORK/health.json" 2>/dev/null || cat "$WORK/health.json"
    log "health: ok"
    return 0
}

case "${1:-}" in
    debrief) shift; cmd_debrief "${1:-}"; exit $? ;;
    drain)   cmd_drain; exit $? ;;
    ask)     shift; cmd_ask "${1:-}" "${2:-}"; exit $? ;;
    health)  cmd_health; exit $? ;;
    *)
        printf 'usage: radio_send.sh debrief <epoch> | drain | ask <question-id> | health\n' >&2
        exit 64
        ;;
esac
