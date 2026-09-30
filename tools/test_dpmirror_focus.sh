#!/bin/bash
# ============================================================
# test_dpmirror_focus.sh — ES focus guard harness for bin/dpmirror_d.sh
# ------------------------------------------------------------
# 2026-09-30: an ES window that maps while DP-1 is connected is moved there by
# ROCKNIX's `for_window ... move output DP-1` and left UNFOCUSED (seat focus on
# the bare workspace) — SDL then drops every gamepad event: "the pad is dead".
# Sources the daemon's functions (DPMIRROR_LIB=1 seam; the loop never starts)
# with swaymsg/pgrep/pkill/amixer shimmed, feeds sway trees shaped like the live
# capture, and asserts the guard's contract:
#   * focus on a bare workspace, or on the wl-mirror view -> ES focused
#   * ES already focused (steady state) -> ZERO sway commands
#   * any other app window present, no ES window, a game running, or
#     ETK_DP_ESFOCUS=0 -> hands off
#   * wired into _reconcile's DP-connected branch only
# The broken states are fixtures too. Runs on the host and the rig (bash + python3):
#   host: bash tools/test_dpmirror_focus.sh [path/to/dpmirror_d.sh]
#   rig:  scp bin/dpmirror_d.sh tools/test_dpmirror_focus.sh /tmp/;
#         ssh 'bash /tmp/test_dpmirror_focus.sh /tmp/dpmirror_d.sh'
# Exit 0 = all pass; nonzero = failure count.
# ============================================================
set -u
DAEMON="${1:-$(cd "$(dirname "$0")/.." && pwd)/bin/dpmirror_d.sh}"
[ -f "$DAEMON" ] || { echo "usage: $0 [dpmirror_d.sh]"; exit 99; }
T="${TMPDIR:-/tmp}/dpmfocus_test.$$"
mkdir -p "$T/shim"
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
check() { D="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$D"; else bad "$D"; fi; }

cat > "$T/shim/swaymsg" << 'SH'
#!/bin/sh
case "$*" in
    *get_tree*) echo q >> "$QUERIES"; cat "$TREE" ;;
    *) echo "$*" >> "$CMDS"; echo '[ { "success": true } ]' ;;
esac
SH
printf '#!/bin/sh\nexit "${PGREP_RC:-1}"\n' > "$T/shim/pgrep"
printf '#!/bin/sh\nexit 0\n' > "$T/shim/pkill"
printf '#!/bin/sh\nexit 0\n' > "$T/shim/amixer"
chmod +x "$T/shim/"*
export PATH="$T/shim:$PATH" CMDS="$T/cmds" QUERIES="$T/queries" TREE="$T/tree.json"

# mk_tree <focused con id> [extra app_id] [noes] — ids as captured live 2026-09-30:
# 4 = workspace "1" on DP-1, 11 = ES, 12 = wl-mirror on DSI-1, 20 = the extra window
mk_tree() {
    python3 - "$@" > "$TREE" << 'PY'
import json, sys
foc = int(sys.argv[1]); extra = sys.argv[2] if len(sys.argv) > 2 else ""; noes = "noes" in sys.argv[3:]
def con(i, app, pid): return {"id": i, "type": "con", "app_id": app, "pid": pid, "focused": i == foc, "nodes": [], "floating_nodes": []}
def ws(i, name, kids): return {"id": i, "type": "workspace", "name": name, "focused": i == foc, "nodes": kids, "floating_nodes": []}
dp_kids = [] if noes else [con(11, "emulationstation", 9294)]
if extra:
    dp_kids.append(con(20, extra, 777))
tree = {"id": 1, "type": "root", "name": "root", "focused": False, "floating_nodes": [], "nodes": [
    {"id": 2, "type": "output", "name": "__i3", "focused": False, "floating_nodes": [], "nodes": [ws(3, "__i3_scratch", [])]},
    {"id": 5, "type": "output", "name": "DSI-1", "focused": False, "floating_nodes": [], "nodes": [ws(6, "2", [con(12, "at.yrlf.wl_mirror", 10026)])]},
    {"id": 7, "type": "output", "name": "DP-1", "focused": False, "floating_nodes": [], "nodes": [ws(4, "1", dp_kids)]}]}
print(json.dumps(tree))
PY
}
reset() { rm -f "$CMDS" "$QUERIES" "$T/dpmirror.log"; }
focused_es() { grep -qx '\[app_id="emulationstation"\] focus' "$CMDS" 2>/dev/null; }
no_cmds() { [ ! -s "$CMDS" ]; }

check "daemon exposes the DPMIRROR_LIB test seam (the guard is testable)" grep -q 'DPMIRROR_LIB' "$DAEMON"
if grep -q 'DPMIRROR_LIB' "$DAEMON"; then
    # shellcheck disable=SC1090
    SHM_DIR="$T" DPMIRROR_LIB=1 . "$DAEMON"
    LOG="$T/dpmirror.log"                      # env.sh (on the rig) repoints SHM_DIR: keep our log private
    CONF="$T/etk.conf"; : > "$CONF"           # never read the rig's real etk.conf
    _swaysock() { return 0; }
    check "_es_focus_guard is defined" declare -F _es_focus_guard

    echo "== live capture: focus on bare workspace 1 (DP-1), ES unfocused"
    reset; mk_tree 4; _es_focus_guard
    check "ES focused" focused_es
    check "log names where focus was" grep -q "focus-guard: ES window unfocused (focus on workspace:1)" "$T/dpmirror.log"

    echo "== steady state: ES already focused"
    reset; mk_tree 11; _es_focus_guard
    check "zero sway commands" no_cmds

    echo "== the wl-mirror view holds focus"
    reset; mk_tree 12; _es_focus_guard
    check "ES focused (the mirror is never an input target)" focused_es

    echo "== another app window exists and is focused (Pitstop, a terminal, another emulator)"
    reset; mk_tree 20 foot; _es_focus_guard
    check "hands off" no_cmds

    echo "== another app window exists, focus on the bare workspace"
    reset; mk_tree 4 retroarch; _es_focus_guard
    check "hands off (never raise ES over another window)" no_cmds

    echo "== no ES window yet (essway still starting)"
    reset; mk_tree 4 "" noes; _es_focus_guard
    check "hands off" no_cmds

    echo "== RPCS3 running"
    reset; mk_tree 4; PGREP_RC=0 _es_focus_guard
    check "hands off, without even querying the tree" sh -c "[ ! -s '$CMDS' ] && [ ! -s '$QUERIES' ]"

    echo "== kill-switch ETK_DP_ESFOCUS=0"
    printf 'ETK_DP_ESFOCUS=0\n' > "$CONF"
    reset; mk_tree 4; _es_focus_guard
    check "hands off" no_cmds
    : > "$CONF"

    echo "== wiring: _reconcile, DP connected (record-only) vs disconnected"
    printf 'ETK_DP_MIRROR=0\n' > "$T/etk.conf"; CONF="$T/etk.conf"; DP_STATUS="$T/dp_status"
    echo connected > "$DP_STATUS"; DP_LAST=1
    reset; mk_tree 4; _reconcile
    check "DP connected: reconcile focuses the stranded ES" focused_es
    echo disconnected > "$DP_STATUS"; DP_LAST=0
    reset; mk_tree 4; _reconcile
    check "DP disconnected: reconcile leaves focus alone" no_cmds
fi

rm -rf "$T"
echo; echo "$PASS passed, $FAIL failed"
exit $FAIL
