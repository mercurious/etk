# ==========================================================
# ETK PIT WALL CONSOLE TUI
# ==========================================================
# Renders a fixed-position dashboard with progress bars + a 2-line
# scrolling datalog, evoking commander.sh's pit wall aesthetic.
#
# Sourced by install.sh and uninstall.sh on the HOST only — NEVER
# pushed to the rig.
#
# Activation gate: console mode unless any of:
#   - --verbose CLI flag, or ETK_VERBOSE=1 in etk.conf, was set
#   - stdout is not a TTY (pipe, redirect, CI)
#   - terminal is narrower than 76 columns
#   - TERM is "dumb" or unset
# Fall-through is plain raw output (historical install.sh behaviour).
#
# Caller configuration (set BEFORE tui_init):
#   TUI_HEADER_TITLE         e.g. "ETK INSTALL" / "ETK UNINSTALL"  (max 14 chars)
#   TUI_TOTAL_STEPS          step count for the dashboard (default 6)
#   TUI_STEP_LABELS          array of 15-char-padded step labels
# Caller content for tui_finish (set BEFORE tui_finish):
#   TUI_FINISH_BANNER         large finish line, e.g. "━━━ KIT ARMED ━━━━..."
#   TUI_FINISH_LINE1          status summary (may contain literal \033 escapes)
#   TUI_FINISH_LINE2          primary instruction
#   TUI_FINISH_LINE3          secondary instruction / health command (optional)
#   TUI_FINISH_VERBOSE_HEAD   verbose-mode finish headline
#   TUI_FINISH_VERBOSE_BODY   verbose-mode follow-up lines (may contain \n)
#
# Public API:
#   tui_should_activate                 -> 0 if ok to activate, 1 otherwise
#   tui_init                            -> clears screen, draws skeleton
#   tui_cleanup                         -> restores cursor (called via trap)
#   tui_step_start <idx>                -> mark step idx WORK
#   tui_step_progress <idx> <pct>       -> set step idx to pct (0-100)
#   tui_step_done <idx>                 -> mark step idx DONE
#   tui_log <message>                   -> push a line to the datalog
#   tui_rsync <idx> <lo> <hi> <label> <rsync args...>
#                                       -> rsync mapped to step pct lo..hi
#   tui_finish                          -> draw completion banner from vars
#   say <message>                       -> tui_log in console, echo in verbose
# ==========================================================

# State
TUI_ACTIVE=0
TUI_BAR_WIDTH=28
TUI_TOTAL_STEPS="${TUI_TOTAL_STEPS:-6}"
TUI_HEADER_TITLE="${TUI_HEADER_TITLE:-ETK INSTALL}"

# Row layout (1-indexed). Step-area rows are recomputed in tui_init from
# TUI_TOTAL_STEPS so 4-step and 6-step dashboards both lay out cleanly.
TUI_ROW_HEADER_TOP=1
TUI_ROW_HEADER_L2=2
TUI_ROW_HEADER_L3=3
TUI_ROW_HEADER_BOT=4
TUI_ROW_OVERALL=6
TUI_ROW_BOX_TOP=8
TUI_ROW_STEPS_START=9
TUI_ROW_BOX_BOT=15        # recomputed at tui_init
TUI_ROW_DATALOG_LABEL=17  # recomputed at tui_init
TUI_ROW_DATALOG_1=18      # recomputed at tui_init
TUI_ROW_DATALOG_2=19      # recomputed at tui_init
TUI_ROW_PARK=22           # recomputed at tui_init

# Default install step labels. Uninstall sets its own before tui_init.
if [ -z "${TUI_STEP_LABELS+set}" ]; then
    TUI_STEP_LABELS=(
        "PROVISION DIRS "
        "HARVEST SHADERS"
        "DEPLOY DAEMONS "
        "PUSH VAULT     "
        "PITSTOP UI     "
        "ARM SENTRY     "
    )
fi
# Per-step state — initialised/resized in tui_init based on TUI_TOTAL_STEPS.
TUI_STEP_PCT=()
TUI_STEP_STATE=()

# Datalog ring (2 lines)
TUI_LOG1=""
TUI_LOG2=""

# Throttling for rsync progress floods
TUI_LAST_REDRAW_EPOCH=0

# --- ACTIVATION ----------------------------------------------------------
tui_should_activate() {
    [ "${ETK_VERBOSE:-0}" = "0" ] || return 1
    [ -t 1 ] || return 1
    local cols
    cols=$(tput cols 2>/dev/null || echo 0)
    [ "$cols" -ge 76 ] || return 1
    [ -n "$TERM" ] && [ "$TERM" != "dumb" ] || return 1
    return 0
}

# --- CORE DRAWING --------------------------------------------------------
tui_goto() { printf '\033[%d;%dH' "$1" "$2"; }
tui_clear_eol() { printf '\033[K'; }

tui_draw_header() {
    local title="${TUI_HEADER_TITLE:-ETK INSTALL}"
    local rig="${RIG_SSH#root@}"
    local tier="${ETK_BUILD_TYPE:-FULL}"
    local version="v14.1.0"

    tui_goto $TUI_ROW_HEADER_TOP 1
    printf '\033[36m╔══════════════════════════════════════════════════════════════════════╗\033[0m'
    tui_goto $TUI_ROW_HEADER_L2 1
    # Visible width tally: 1(║) + 2 + 14(%-14s title) + 2 + 2(━━) + 2 + 8(PIT WALL) + 14 + 5("RIG: ") + 21(%-21s rig) + 1(║) = 72.
    printf '\033[36m║\033[0m  \033[1;36m%-14s\033[0m  \033[36m━━\033[0m  \033[1;36mPIT WALL\033[0m              RIG: \033[1;33m%-21s\033[0m\033[36m║\033[0m' "$title" "$rig"
    tui_goto $TUI_ROW_HEADER_L3 1
    printf '\033[36m║\033[0m                                         TIER: \033[1;33m%-4s\033[0m  •  %-13s \033[36m║\033[0m' "$tier" "$version"
    tui_goto $TUI_ROW_HEADER_BOT 1
    printf '\033[36m╚══════════════════════════════════════════════════════════════════════╝\033[0m'
}

tui_draw_overall() {
    local pct="$1"
    local width=44
    local filled=$(( pct * width / 100 ))
    local empty=$(( width - filled ))
    tui_goto $TUI_ROW_OVERALL 1
    printf '  OVERALL    \033[36m'
    local i
    for i in $(seq 1 $filled); do printf '▓'; done
    printf '\033[2m'
    for i in $(seq 1 $empty); do printf '░'; done
    printf '\033[0m  \033[1;33m%3d%%\033[0m' "$pct"
    tui_clear_eol
}

tui_draw_step() {
    local idx="$1"
    local row=$(( TUI_ROW_STEPS_START + idx ))
    local label="${TUI_STEP_LABELS[$idx]}"
    local pct="${TUI_STEP_PCT[$idx]}"
    local state="${TUI_STEP_STATE[$idx]}"
    local step_num=$(( idx + 1 ))
    local i

    tui_goto $row 1
    printf '  \033[36m│\033[0m  \033[2m[%d/%d]\033[0m  %s ' "$step_num" "$TUI_TOTAL_STEPS" "$label"

    # Each status region must render EXACTLY 13 visible chars so the closing
    # │ at the right of the row lines up with the box's ┐ and ┘. ANSI escape
    # codes don't count toward visible width — count carefully.
    case "$state" in
        PEND)
            printf '\033[2m'
            for i in $(seq 1 $TUI_BAR_WIDTH); do printf '░'; done
            printf '\033[0m'
            # 13 visible: 6 spaces + — + 6 spaces
            printf '      \033[2m—\033[0m      '
            ;;
        WORK)
            local filled=$(( pct * TUI_BAR_WIDTH / 100 ))
            local empty=$(( TUI_BAR_WIDTH - filled ))
            printf '\033[36m'
            for i in $(seq 1 $filled); do printf '█'; done
            printf '\033[2m'
            for i in $(seq 1 $empty); do printf '░'; done
            printf '\033[0m'
            # 13 visible: 4 spaces + %3d (3 cols) + % (1) + 5 spaces
            printf '    \033[1;33m%3d%%\033[0m     ' "$pct"
            ;;
        DONE)
            printf '\033[32m'
            for i in $(seq 1 $TUI_BAR_WIDTH); do printf '█'; done
            printf '\033[0m'
            # 13 visible: 4 spaces + DONE (4) + 5 spaces
            printf '    \033[1;32mDONE\033[0m     '
            ;;
    esac
    printf '\033[36m│\033[0m'
    tui_clear_eol
}

tui_draw_step_skeleton() {
    tui_goto $TUI_ROW_BOX_TOP 1
    printf '  \033[36m┌──────────────────────────────────────────────────────────────────┐\033[0m'
    local i
    for i in $(seq 0 $(( TUI_TOTAL_STEPS - 1 ))); do tui_draw_step "$i"; done
    tui_goto $TUI_ROW_BOX_BOT 1
    printf '  \033[36m└──────────────────────────────────────────────────────────────────┘\033[0m'
}

tui_draw_datalog() {
    tui_goto $TUI_ROW_DATALOG_LABEL 1
    printf '  \033[1;36m▸ DATALOG\033[0m'
    tui_clear_eol
    tui_goto $TUI_ROW_DATALOG_1 1
    printf '    \033[2m> %s\033[0m' "$TUI_LOG1"
    tui_clear_eol
    tui_goto $TUI_ROW_DATALOG_2 1
    printf '    > %s' "$TUI_LOG2"
    tui_clear_eol
}

# --- PUBLIC API ----------------------------------------------------------
tui_init() {
    TUI_ACTIVE=1
    # Recompute row layout from TUI_TOTAL_STEPS so 4-step (uninstall) and
    # 6-step (install) dashboards both lay out cleanly. Layout below the
    # step box: 1 blank, datalog label, 2 datalog lines, blank, park.
    TUI_ROW_BOX_BOT=$(( TUI_ROW_STEPS_START + TUI_TOTAL_STEPS ))
    TUI_ROW_DATALOG_LABEL=$(( TUI_ROW_BOX_BOT + 2 ))
    TUI_ROW_DATALOG_1=$(( TUI_ROW_DATALOG_LABEL + 1 ))
    TUI_ROW_DATALOG_2=$(( TUI_ROW_DATALOG_LABEL + 2 ))
    TUI_ROW_PARK=$(( TUI_ROW_DATALOG_2 + 3 ))
    # (Re)initialise per-step state arrays to match TUI_TOTAL_STEPS.
    TUI_STEP_PCT=()
    TUI_STEP_STATE=()
    local i
    for i in $(seq 1 $TUI_TOTAL_STEPS); do
        TUI_STEP_PCT+=(0)
        TUI_STEP_STATE+=("PEND")
    done
    tput civis 2>/dev/null   # hide cursor
    printf '\033[2J\033[H'    # clear screen, home cursor
    trap tui_cleanup INT TERM EXIT
    tui_draw_header
    tui_draw_overall 0
    tui_draw_step_skeleton
    tui_draw_datalog
    tui_goto $TUI_ROW_PARK 1
}

tui_cleanup() {
    [ "$TUI_ACTIVE" = "1" ] || return 0
    tput cnorm 2>/dev/null   # show cursor
    tui_goto $TUI_ROW_PARK 1
    printf '\n'
    trap - INT TERM EXIT
    TUI_ACTIVE=0
}

tui_update_overall() {
    local sum=0
    local i
    for i in $(seq 0 $(( TUI_TOTAL_STEPS - 1 ))); do
        sum=$(( sum + ${TUI_STEP_PCT[$i]} ))
    done
    local pct=$(( sum / TUI_TOTAL_STEPS ))
    tui_draw_overall "$pct"
}

tui_step_start() {
    local idx="$1"
    local label="${TUI_STEP_LABELS[$idx]}"
    if [ "$TUI_ACTIVE" = "1" ]; then
        TUI_STEP_STATE[$idx]="WORK"
        TUI_STEP_PCT[$idx]=0
        tui_draw_step "$idx"
        tui_update_overall
        tui_goto $TUI_ROW_PARK 1
    else
        local step_num=$(( idx + 1 ))
        # Trim trailing whitespace only (preserve multi-word labels like
        # "HARVEST SHADERS"). `${label% *}` strips the last word — wrong.
        local clean_label=$(printf '%s' "$label" | sed 's/[[:space:]]*$//')
        printf '\033[0;36m>>> [%d/%d] %s...\033[0m\n' "$step_num" "$TUI_TOTAL_STEPS" "$clean_label"
    fi
}

tui_step_progress() {
    local idx="$1"
    local pct="$2"
    [ "$pct" -gt 100 ] 2>/dev/null && pct=100
    [ "$pct" -lt 0 ] 2>/dev/null && pct=0
    if [ "$TUI_ACTIVE" = "1" ]; then
        if [ "${TUI_STEP_PCT[$idx]}" != "$pct" ]; then
            TUI_STEP_PCT[$idx]=$pct
            tui_draw_step "$idx"
            tui_update_overall
            tui_goto $TUI_ROW_PARK 1
        fi
    fi
}

tui_step_done() {
    local idx="$1"
    if [ "$TUI_ACTIVE" = "1" ]; then
        TUI_STEP_STATE[$idx]="DONE"
        TUI_STEP_PCT[$idx]=100
        tui_draw_step "$idx"
        tui_update_overall
        tui_goto $TUI_ROW_PARK 1
    fi
}

tui_log() {
    if [ "$TUI_ACTIVE" = "1" ]; then
        local msg="$1"
        # Strip ANSI color codes — env.sh's $G/$R/$Y/$N store them as the
        # literal 7-char string "\033[0;32m" (single-quoted in env.sh, so
        # the backslash is a real byte, not an escape). The datalog row's
        # %s format prints those bytes as junk. Datalog is plain by design.
        msg=$(printf '%s' "$msg" | sed -E 's/\\033\[[0-9;]*m//g')
        # Truncate to fit one terminal line. The datalog row uses 6 cols of
        # prefix ("    > "), so the message must be ≤74 chars at 80 cols.
        # Use 72 for safety. Without this, long messages wrap to the next
        # row and leave residue that tui_clear_eol can't reach.
        if [ ${#msg} -gt 72 ]; then
            msg="${msg:0:69}..."
        fi
        TUI_LOG1="$TUI_LOG2"
        TUI_LOG2="$msg"
        tui_draw_datalog
        tui_goto $TUI_ROW_PARK 1
    fi
}

# say <message> — informational; datalog in console mode, echo in verbose.
# Pass a message without leading whitespace; verbose mode adds the indent.
say() {
    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_log "$1"
    else
        printf '    %b\n' "$1"
    fi
}

# tui_rsync <step_idx> <pct_lo> <pct_hi> <op_label> <rsync args...>
# Maps rsync's overall progress (0..100) onto the step's lo..hi range so
# multiple chunked rsyncs within one step advance the same bar smoothly.
# In verbose mode, falls through to $RSYNC_CMD (the historical behaviour).
tui_rsync() {
    local step_idx="$1"; shift
    local lo="$1"; shift
    local hi="$1"; shift
    local op_label="$1"; shift

    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_step_progress "$step_idx" "$lo"
        tui_log "$op_label"
        local range=$(( hi - lo ))
        # Use --progress (NOT --info=progress2). macOS ships with rsync 2.6.9
        # which errors out on the rsync-3.x --info flag — and the silent
        # error makes the bar still snap to $hi while zero files transfer.
        # --progress emits per-file lines on BOTH rsync 2.x and 3.x ending
        # with "to-check=N/M" (2.x) or "to-chk=N/M" (3.x); the regex below
        # matches either. tr '\r' '\n' converts rsync's carriage-return
        # overwrite into iterable lines.
        rsync -az --progress "$@" 2>&1 \
            | tr '\r' '\n' \
            | while IFS= read -r line; do
                if [[ "$line" =~ to-(chk|check)=([0-9,]+)/([0-9,]+) ]]; then
                    local rem="${BASH_REMATCH[2]//,/}"
                    local tot="${BASH_REMATCH[3]//,/}"
                    if [ "$tot" -gt 0 ] 2>/dev/null; then
                        local dn=$(( tot - rem ))
                        local file_progress=$(( 100 * dn / tot ))
                        local step_p=$(( lo + range * file_progress / 100 ))
                        tui_step_progress "$step_idx" "$step_p"
                        tui_log "$op_label  •  $dn / $tot files"
                    fi
                fi
              done
        local rsync_rc=${PIPESTATUS[0]}
        if [ "$rsync_rc" != "0" ]; then
            tui_log "[FAIL] rsync exit=$rsync_rc — re-run with --verbose to diagnose"
        else
            tui_step_progress "$step_idx" "$hi"
        fi
    else
        printf '\033[0;36m    %s\033[0m\n' "$op_label"
        $RSYNC_CMD "$@"
    fi
}

# tui_rsync_checksum — same shape as tui_rsync but forces --checksum
# (used for etk.conf where same-byte-length edits would otherwise be skipped
#  by rsync's default size+mtime check).
tui_rsync_checksum() {
    local step_idx="$1"; shift
    local lo="$1"; shift
    local hi="$1"; shift
    local op_label="$1"; shift

    if [ "$TUI_ACTIVE" = "1" ]; then
        tui_step_progress "$step_idx" "$lo"
        tui_log "$op_label"
        local range=$(( hi - lo ))
        rsync -az --checksum --progress "$@" 2>&1 \
            | tr '\r' '\n' \
            | while IFS= read -r line; do
                if [[ "$line" =~ to-(chk|check)=([0-9,]+)/([0-9,]+) ]]; then
                    local rem="${BASH_REMATCH[2]//,/}"
                    local tot="${BASH_REMATCH[3]//,/}"
                    if [ "$tot" -gt 0 ] 2>/dev/null; then
                        local dn=$(( tot - rem ))
                        local file_progress=$(( 100 * dn / tot ))
                        local step_p=$(( lo + range * file_progress / 100 ))
                        tui_step_progress "$step_idx" "$step_p"
                    fi
                fi
              done
        local rsync_rc=${PIPESTATUS[0]}
        if [ "$rsync_rc" != "0" ]; then
            tui_log "[FAIL] rsync exit=$rsync_rc — re-run with --verbose to diagnose"
        else
            tui_step_progress "$step_idx" "$hi"
        fi
    else
        printf '\033[0;36m    %s\033[0m\n' "$op_label"
        $RSYNC_CMD --checksum "$@"
    fi
}

tui_finish() {
    # Banner + lines are caller-supplied via TUI_FINISH_* vars. Empty vars
    # render as blank rows. printf '%b' interprets literal \033 escapes so
    # callers can embed ANSI colours in their content.
    local banner="${TUI_FINISH_BANNER:-}"
    local line1="${TUI_FINISH_LINE1:-}"
    local line2="${TUI_FINISH_LINE2:-}"
    local line3="${TUI_FINISH_LINE3:-}"
    if [ "$TUI_ACTIVE" = "1" ]; then
        # Clear the entire completion region first (datalog through park)
        # to wipe any residue from long log messages that may have wrapped
        # to lines tui_clear_eol couldn't reach.
        local row
        for row in $(seq $TUI_ROW_DATALOG_LABEL $TUI_ROW_PARK); do
            tui_goto "$row" 1
            tui_clear_eol
        done
        tui_goto $TUI_ROW_DATALOG_LABEL 1
        printf '  \033[1;32m%b\033[0m' "$banner"
        tui_clear_eol
        tui_goto $TUI_ROW_DATALOG_1 1
        printf '   %b' "$line1"
        tui_clear_eol
        tui_goto $TUI_ROW_DATALOG_2 1
        printf '   %b' "$line2"
        tui_clear_eol
        if [ -n "$line3" ]; then
            tui_goto $(( TUI_ROW_DATALOG_2 + 2 )) 1
            printf '   %b\n\n' "$line3"
        else
            tui_goto $TUI_ROW_PARK 1
            printf '\n'
        fi
        tput cnorm 2>/dev/null
        TUI_ACTIVE=0
        trap - INT TERM EXIT
    else
        # Verbose-mode finish: caller supplies a headline + body block.
        local vhead="${TUI_FINISH_VERBOSE_HEAD:-DEPLOYMENT COMPLETE.}"
        local vbody="${TUI_FINISH_VERBOSE_BODY:-}"
        printf '\n\033[0;32m>>> %b\033[0m\n' "$vhead"
        if [ -n "$vbody" ]; then
            printf '%b\n' "$vbody"
        fi
    fi
}

# tui_fail <message> — print error below the dashboard and exit non-zero.
# Cleans up the TUI state first so the error is readable.
tui_fail() {
    tui_cleanup
    printf '\033[0;31m[FAIL] %s\033[0m\n' "$1" >&2
    exit 1
}
