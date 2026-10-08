#!/bin/bash
# test_car_identity.sh — pins the two-rig CAR CHECK (scripts/etk_car.sh + the
# install.sh gate).
#
# WHY: 2026-10-07 the garage gained a second Flip2. car12 (12GB) came up on the
# host's USB at 169.254.170.2 — car8's address — and accepted car8's key; only
# ssh host-key checking kept car8-bound commands (install.sh included) off it.
# Cars are named by RAM (car<N> = N GB) and the check proves the unit reached
# is the car meant (dossiers/TwoRigGarage_20261007.md).
#
# HOW: sandbox with a fake `ssh` on PATH that answers the probe from canned
# unit files ($TD/units/<host>: car= model= memkb= panel= host=) and models the
# assign write. No network, no rig. The install.sh gate is exercised by
# extracting the block between its `# >>> CAR CHECK` / `# <<< CAR CHECK`
# markers and running it with a sentinel after it.
#
# DISCRIMINATION: `--against <rev>` takes install.sh AND scripts/etk_car.sh from
# that revision. Against ee353f3 (pre-change: no gate, no tool) all 18 run cases FAIL.
#
#   tools/test_car_identity.sh              # working tree — must PASS
#   tools/test_car_identity.sh --against ee353f3   # pre-change — must FAIL

set -u
cd "$(dirname "$0")/.." || exit 1
REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"

TD=$(mktemp -d); [ -n "${KEEP:-}" ] || trap 'rm -rf "$TD"' EXIT; [ -n "${KEEP:-}" ] && echo "sandbox: $TD"
mkdir -p "$TD/bin" "$TD/units" "$TD/scripts"
if [ -n "$REV" ]; then
    git show "$REV:install.sh" > "$TD/install.sh" 2>/dev/null || : > "$TD/install.sh"
    git show "$REV:scripts/etk_car.sh" > "$TD/scripts/etk_car.sh" 2>/dev/null || rm -f "$TD/scripts/etk_car.sh"
else
    cp install.sh "$TD/install.sh"; cp scripts/etk_car.sh "$TD/scripts/etk_car.sh"
fi

# --- fake ssh: the unit is whatever file names the target's host -------------
cat > "$TD/bin/ssh" <<'FAKE'
#!/bin/bash
target=""
while [ $# -gt 0 ]; do
    case "$1" in -o|-i|-p) shift 2 ;; -*) shift ;; *) target="$1"; shift; break ;; esac
done
cmd="$*"; host="${target#*@}"; u="$UNITS/$host"
[ -f "$u" ] || exit 255                       # unreachable
if printf '%s' "$cmd" | grep -q 'car.tmp'; then  # assign: printf '%s\n' 'carN' > ...
    name=$(printf '%s' "$cmd" | sed -n "s/.*printf '%s\\\\n' '\([^']*\)'.*/\1/p")
    sed -i "s/^car=.*/car=$name/" "$u"; echo "$name"; exit 0
fi
cat "$u"; echo ETK_CAR_PROBE_OK
FAKE
chmod +x "$TD/bin/ssh"
export PATH="$TD/bin:$PATH" UNITS="$TD/units"

unit() { printf 'car=%s\nmodel=%s\nmemkb=%s\npanel=%s\nhost=%s\n' "$2" "$3" "$4" "$5" "$1" > "$TD/units/$1"; }
reset_units() {
    unit car8host  ""      "Retroid Pocket Flip2"          7735836  "ch13726a,rp5"     SM8250
    unit car12host ""      "Retroid Pocket Flip2 Visionox" 11809768 "vtdr6130,rpflip2" sm8250-12gb
    unit oddname   "car08" "Retroid Pocket Flip2"          7735836  "ch13726a,rp5"     SM8250
}

PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
# expect <label> <want-rc> <want-substring> <command...>
expect() {
    local label="$1" wrc="$2" wsub="$3"; shift 3
    local out rc; out=$("$@" 2>&1); rc=$?
    if [ "$rc" = "$wrc" ] && printf '%s' "$out" | grep -qF -- "$wsub"; then ok "$label"
    else bad "$label (rc=$rc want $wrc; out: $(printf '%s' "$out" | head -c 160))"; fi
}
LIB() { ( [ -f "$TD/scripts/etk_car.sh" ] || { echo "no scripts/etk_car.sh"; exit 99; }
          . "$TD/scripts/etk_car.sh"; "$@" ); }

reset_units
# --- library ------------------------------------------------------------------
expect "car8 at car8 (unassigned) passes with an assign hint" 0 "unassigned"  LIB etk_car_verify root@car8host car8
expect "car8 expected, car12 reached -> REFUSED on spec"      1 "reached a 12 GB unit" LIB etk_car_verify root@car12host car8
expect "car12 expected, car8 reached -> REFUSED on spec"      1 "reached a 8 GB unit"  LIB etk_car_verify root@car8host car12
expect "name mismatch (unit says car08) -> REFUSED"           1 "says it is car08"     LIB etk_car_verify root@oddname car8
expect "no CAR -> report only, rc 0"                          0 "report only"          LIB etk_car_verify root@car12host ""
expect "no CAR + unreachable -> never blocks"                 0 "could not read"       LIB etk_car_verify root@nobody ""
expect "CAR set + unreachable -> rc 3"                        3 "cannot read"          LIB etk_car_verify root@nobody car8
expect "CAR not a car name -> rc 2"                           2 "not a car name"       LIB etk_car_verify root@car8host rig1
expect "assign car8 onto the 12GB unit -> REFUSED"            1 "assign REFUSED"       LIB etk_car_assign root@car12host car8
expect "assign over a different existing name -> REFUSED"     1 "already says it is car08" LIB etk_car_assign root@oddname car8
expect "assign car12 onto the 12GB unit"                      0 "is now car12"         LIB etk_car_assign root@car12host car12
expect "after assign: car12 verified by name"                 0 "car12 verified"       LIB etk_car_verify root@car12host car12
for pair in "7735836 8" "11809768 12" "8388608 8" "8388609 9"; do
    set -- $pair
    expect "RAM rounding: $1 kB -> $2 GB" 0 "$2" LIB etk_car_ram_gb "$1"
done

# --- install.sh gate ------------------------------------------------------------
reset_units
GATE="$TD/gate.sh"
awk '/^# >>> CAR CHECK$/{f=1} f{print} /^# <<< CAR CHECK$/{f=0}' "$TD/install.sh" > "$TD/block.sh"
{ echo 'R= G= C= Y= N='; echo 'cd "$1"'; cat "$TD/block.sh"; echo 'echo REACHED_LIVE_GUARD'; } > "$GATE"
gate() { ( RIG_SSH="$1" CAR="$2" bash "$GATE" "$TD" ); }
if [ ! -s "$TD/block.sh" ]; then
    bad "install.sh carries the CAR CHECK block"
else
    ok "install.sh carries the CAR CHECK block"
    expect "gate: CAR=car8 but car12 at RIG_SSH -> install refused" 1 "Install refused" gate root@car12host car8
    out=$(gate root@car12host car8 2>&1); printf '%s' "$out" | grep -q REACHED_LIVE_GUARD \
        && bad "gate: refused run must not continue" || ok "gate: refused run stops before the live-session guard"
    expect "gate: CAR=car8 at car8 -> continues"            0 "REACHED_LIVE_GUARD" gate root@car8host car8
    expect "gate: no CAR -> continues (single-rig install)" 0 "REACHED_LIVE_GUARD" gate root@car12host ""
fi
# order: after pairing, before the first ssh of the live-session guard
L_PAIR=$(grep -n 'bash ./scripts/etk_pair.sh' "$TD/install.sh" | head -1 | cut -d: -f1)
L_GATE=$(grep -n '^# >>> CAR CHECK$' "$TD/install.sh" | head -1 | cut -d: -f1)
L_LIVE=$(grep -n 'pgrep -f "AppRun.wrappe\[d\]' "$TD/install.sh" | head -1 | cut -d: -f1)
if [ -n "$L_GATE" ] && [ -n "$L_PAIR" ] && [ -n "$L_LIVE" ] && [ "$L_PAIR" -lt "$L_GATE" ] && [ "$L_GATE" -lt "$L_LIVE" ]; then
    ok "gate sits after pairing (L$L_PAIR) and before the live-session guard (L$L_LIVE)"
else
    bad "gate order (pair=${L_PAIR:-?} gate=${L_GATE:-none} live=${L_LIVE:-?})"
fi

echo "---- $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" -eq 0 ]
