#!/bin/bash
# ==========================================================
# ETK CAR CHECK — which car is actually on the other end?  (etk_car.sh)
# ==========================================================
# The garage hosts more than one rig (2026-10-07: car8 = the founding 8GB Flip2,
# car12 = the 12GB Visionox Flip2). Name a car anything: etk.conf's
# CARS="name,ssh[,ramGB] ..." lists them (letters, digits, - and _). A name like
# car8 with no CARS entry still means an 8 GB unit at CAR8_SSH (the original
# scheme). Before a host tool ACTS on a rig it checks that the unit it reached
# is the car it meant:
#   1. spec — when the car's RAM is known, the unit's own MemTotal, rounded up to
#      whole GB, must equal it. This needs nothing on the unit, so it catches a
#      never-assigned unit too.
#   2. name — /storage/.etk/car on the unit (written by `assign`), if present,
#      must equal the expected car. With no RAM known, the name is the only
#      proof, so an unassigned unit is refused.
# Why: on day one car12 came up on the host's USB at 169.254.170.2 (car8's
# address) and accepted car8's key; only ssh host-key checking stopped
# car8-bound commands reaching it (dossiers/TwoRigGarage_20261007.md §2).
#
# Opt-in: with no CAR set (etk.conf or env) `verify` only REPORTS what it
# reached and returns 0 — a single-rig install behaves exactly as before.
#
# Usage (CLI):
#   scripts/etk_car.sh show   [target]          # what is on the other end
#   scripts/etk_car.sh verify [target] [car]    # 0 ok · 1 refused · 2 usage · 3 unreachable
#   scripts/etk_car.sh assign  target   car     # write /storage/.etk/car (OPERATOR-run)
#   scripts/etk_car.sh target  [car]            # the ssh target (ETK_RIG, else the car's, else RIG_SSH)
# Sourced (install.sh): `. scripts/etk_car.sh; etk_car_verify "$RIG_SSH" "${CAR:-}"`
# Target resolution when omitted: the expected car's CARS entry, else its
# CAR<N>_SSH, else RIG_SSH, else root@SM8250.local (etk.conf is read if present).
# ==========================================================

ETK_CAR_FILE="/storage/.etk/car"

# A tool that sources this without etk.conf (abl_slot.sh, bls_prestage.sh) still sees the garage.
if [ -z "${CARS:-}" ]; then
    _etk_conf="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)/etk.conf"
    [ -f "$_etk_conf" ] && CARS=$(sed -n 's/^[[:space:]]*CARS="\([^"]*\)".*/\1/p' "$_etk_conf" | tail -n 1)
fi

# Remote side is read-only and BusyBox-safe.
etk_car_probe() {   # $1 target -> key=value lines on stdout; rc 3 if unreachable
    local out try
    for try in 1 2; do   # one retry: a rig busy on WiFi can miss a single connect
    out=$(ssh -o ConnectTimeout=8 -o BatchMode=yes "$1" '
        printf "car=%s\n"   "$(cat '"$ETK_CAR_FILE"' 2>/dev/null | head -n1)"
        printf "model=%s\n" "$(tr -d "\000" < /proc/device-tree/model 2>/dev/null)"
        printf "memkb=%s\n" "$(awk "/^MemTotal:/{print \$2}" /proc/meminfo 2>/dev/null)"
        printf "panel=%s\n" "$(tr "\000" " " < /proc/device-tree/soc@0/display-subsystem@ae00000/dsi@ae94000/panel@0/compatible 2>/dev/null)"
        printf "host=%s\n"  "$(hostname 2>/dev/null)"
        echo ETK_CAR_PROBE_OK' 2>/dev/null)
    printf '%s\n' "$out" | grep -q '^ETK_CAR_PROBE_OK$' && break
    [ "$try" = 2 ] && return 3
    done
    printf '%s\n' "$out" | grep -v '^ETK_CAR_PROBE_OK$'
}

etk_car_field() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n1; }

etk_car_ram_gb() {  # MemTotal kB -> whole GB, rounded UP (7,735,000 kB -> 8)
    awk -v k="${1:-0}" 'BEGIN { g = k / 1048576; n = int(g); if (g > n) n++; print n }'
}

etk_car_number() {  # car12 -> 12 ; anything else -> empty
    case "$1" in car[0-9]*) n="${1#car}"; case "$n" in *[!0-9]*) ;; *) echo "$n" ;; esac ;; esac
}

etk_car_valid() {  # a name that is safe in a filename and a quoted remote command
    case "$1" in ""|*[!A-Za-z0-9_-]*|[-_]*) return 1 ;; esac
    [ "${#1}" -le 32 ]
}

etk_car_entry() {  # $1 name -> its CARS entry (name,ssh[,ramGB]); rc 1 if listed nowhere
    local e
    for e in ${CARS:-}; do
        case "$e" in "$1",*) printf '%s\n' "$e"; return 0 ;; esac
    done
    return 1
}

etk_car_ram() {  # $1 name -> the GB it should have; empty = unknown
    local e
    if e=$(etk_car_entry "$1"); then
        printf '%s\n' "$e" | cut -d, -f3
    else
        etk_car_number "$1"
    fi
}

etk_car_describe() {  # $1 probe output -> one-line description of the unit
    local p="$1"
    printf '%s, %s GB (MemTotal %s kB), panel %s, host %s, named %s' \
        "$(etk_car_field "$p" model)" "$(etk_car_ram_gb "$(etk_car_field "$p" memkb)")" \
        "$(etk_car_field "$p" memkb)" "$(etk_car_field "$p" panel | sed 's/ *$//')" \
        "$(etk_car_field "$p" host)" "$(etk_car_field "$p" car | sed 's/^$/(unassigned)/')"
}

etk_car_verify() {  # $1 target  $2 expected car ("" = report only)
    local target="$1" want="${2:-}" p rc n gb have
    p=$(etk_car_probe "$target"); rc=$?
    if [ $rc -ne 0 ]; then
        if [ -z "$want" ]; then   # opt-in: a single-rig install is never blocked
            echo "CAR CHECK (report only, no CAR set): could not read the unit at $target"
            return 0
        fi
        echo "CAR CHECK: cannot read the unit at $target (unreachable or not passwordless)"
        return 3
    fi
    if [ -z "$want" ]; then
        echo "CAR CHECK (report only, no CAR set): $target is $(etk_car_describe "$p")"
        return 0
    fi
    if ! etk_car_valid "$want"; then
        echo "CAR CHECK: CAR='$want' is not a car name (letters, digits, - and _, up to 32)"
        return 2
    fi
    n=$(etk_car_ram "$want")
    case "$n" in *[!0-9]*)
        echo "CAR CHECK: CARS gives $want a RAM of '$n' (expected whole GB)"
        return 2 ;;
    esac
    gb=$(etk_car_ram_gb "$(etk_car_field "$p" memkb)")
    have=$(etk_car_field "$p" car)
    if [ -n "$n" ] && [ "$gb" != "$n" ]; then
        echo "CAR CHECK REFUSED: expected $want ($n GB) at $target, but reached a $gb GB unit — $(etk_car_describe "$p")"
        return 1
    fi
    if [ -n "$have" ] && [ "$have" != "$want" ]; then
        echo "CAR CHECK REFUSED: expected $want at $target, but the unit says it is $have — $(etk_car_describe "$p")"
        return 1
    fi
    if [ -z "$have" ]; then
        if [ -z "$n" ]; then
            echo "CAR CHECK REFUSED: nothing proves $target is $want: no RAM in CARS and the unit is unassigned — run: scripts/etk_car.sh assign $target $want"
            return 1
        fi
        echo "CAR CHECK: $want spec matches at $target ($n GB) but the unit is unassigned — run: scripts/etk_car.sh assign $target $want"
        return 0
    fi
    echo "CAR CHECK: $want verified at $target — $(etk_car_describe "$p")"
    return 0
}

etk_car_assign() {  # $1 target  $2 car — writes the name on the unit, read-back verified
    local target="$1" want="$2" p rc n gb have back
    etk_car_valid "$want" || { echo "assign: '$want' is not a car name (letters, digits, - and _, up to 32)"; return 2; }
    n=$(etk_car_ram "$want")
    p=$(etk_car_probe "$target"); rc=$?
    [ $rc -eq 0 ] || { echo "assign: cannot read the unit at $target"; return 3; }
    gb=$(etk_car_ram_gb "$(etk_car_field "$p" memkb)")
    [ -z "$n" ] || [ "$gb" = "$n" ] || { echo "assign REFUSED: $want means $n GB but $target is a $gb GB unit — $(etk_car_describe "$p")"; return 1; }
    have=$(etk_car_field "$p" car)
    if [ -n "$have" ] && [ "$have" != "$want" ]; then
        echo "assign REFUSED: $target already says it is $have (remove $ETK_CAR_FILE by hand to rename)"; return 1
    fi
    back=$(ssh -o ConnectTimeout=8 -o BatchMode=yes "$target" \
        "mkdir -p ${ETK_CAR_FILE%/*} && printf '%s\n' '$want' > $ETK_CAR_FILE.tmp && mv $ETK_CAR_FILE.tmp $ETK_CAR_FILE && cat $ETK_CAR_FILE" 2>/dev/null)
    [ "$back" = "$want" ] || { echo "assign FAILED: read-back '$back' != '$want'"; return 1; }
    echo "assigned: $target is now $want — $(etk_car_describe "$(etk_car_probe "$target")")"
}

etk_car_resolve_target() {  # $1 car ("" ok) -> ssh target
    local want="$1" n v e
    if e=$(etk_car_entry "$want"); then
        printf '%s\n' "$e" | cut -d, -f2
        return
    fi
    n=$(etk_car_number "$want")
    if [ -n "$n" ]; then
        v="CAR${n}_SSH"
        [ -n "${!v:-}" ] && { echo "${!v}"; return; }
    fi
    echo "${RIG_SSH:-root@SM8250.local}"
}

# --- CLI (only when executed, not when sourced) ---
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    _conf="$(cd "$(dirname "$0")/.." && pwd)/etk.conf"
    [ -f "$_conf" ] && . "$_conf"
    cmd="${1:-}"; shift || true
    case "$cmd" in
        show)
            t="${1:-$(etk_car_resolve_target "${CAR:-}")}"
            p=$(etk_car_probe "$t") || { echo "cannot read the unit at $t"; exit 3; }
            echo "$t: $(etk_car_describe "$p")" ;;
        verify)
            if [ $# -ge 2 ]; then t="$1"; c="$2"
            elif [ $# -eq 1 ]; then t="$1"; c="${CAR:-}"
            else c="${CAR:-}"; t="$(etk_car_resolve_target "$c")"; fi
            etk_car_verify "$t" "$c"; exit $? ;;
        target)
            echo "${ETK_RIG:-$(etk_car_resolve_target "${1:-${CAR:-}}")}" ;;
        assign)
            [ $# -eq 2 ] || { echo "usage: $0 assign <target> <car>"; exit 2; }
            etk_car_assign "$1" "$2"; exit $? ;;
        *)
            sed -n '/^# Usage (CLI):/,/^# Target resolution/p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
    esac
fi
