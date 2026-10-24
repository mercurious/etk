#!/bin/bash
# test_boot_identity.sh — pins the GTK boot-identity line (config/etk-gtk-version.service
# as install.sh templates it) for the 0.10.0 cut.
#
# WHY: the line on the boot console is the §0 surface "the boot string names this
# release". 0.10.0 makes it name the WHOLE certified stack: kit version + kernel date
# (as before) + the edition name (APP_EDITION, beside APP_VERSION in etk_pitstop.py)
# + the kernel generation read off the Image name. It once announced a hardcoded
# "0.7.0" for three releases because the version was hand-written in the unit.
#
# HOW: no rig. The unit's four placeholders are templated with install.sh's OWN sed
# expression (extracted from the script, both deploy sites must agree), the
# GTK_KVER/GTK_EDITION derivations are extracted and evaluated against a fake
# etk_pitstop.py and Image name, then the unit's ExecStart runs against a fake
# /proc/cmdline. Asserts the rendered line, the stock-boot silence, the no-edition
# form, and that no '@' placeholder survives templating.
#
# DISCRIMINATION: `--against <rev>` takes install.sh, the unit and etk_pitstop.py
# from that revision. Against 456b078 (pre-change) the edition/kernel cases FAIL.
#
#   tools/test_boot_identity.sh                     # working tree — must PASS
#   tools/test_boot_identity.sh --against 456b078   # pre-change — must FAIL

set -u
cd "$(dirname "$0")/.." || exit 1
REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"
TD=$(mktemp -d); trap 'rm -rf "$TD"' EXIT
mkdir -p "$TD/bin"
if [ -n "$REV" ]; then
    git show "$REV:install.sh" > "$TD/install.sh"
    git show "$REV:config/etk-gtk-version.service" > "$TD/unit.tmpl"
    git show "$REV:bin/etk_pitstop.py" > "$TD/bin/etk_pitstop.py"
else
    cp install.sh "$TD/install.sh"; cp config/etk-gtk-version.service "$TD/unit.tmpl"
    cp bin/etk_pitstop.py "$TD/bin/etk_pitstop.py"
fi
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — got '$2' want '$3'"; fi; }

# --- install.sh: one sed expression, deployed from both kernel branches --------------
SEDS=$(grep -o "sed -i '[^']*' /storage/.config/system.d/etk-gtk-version.service" "$TD/install.sh" | sort -u)
check "both deploy sites template the unit with the same expression" "$(echo "$SEDS" | wc -l | tr -d ' ')" "1"
SEDEXPR=$(echo "$SEDS" | head -1 | sed "s/^sed -i '//; s/' \/storage.*//")
for tok in KDATE GTKVER EDITION KVER; do
    case "$SEDEXPR" in *"@$tok@"*) ok "install.sh templates @$tok@" ;; *) bad "install.sh templates @$tok@" ;; esac
done
KVER_LINE=$(grep -m1 '^ *GTK_KVER=' "$TD/install.sh" | sed 's/^ *//')
ED_LINE=$(grep -m1 '^ *GTK_EDITION=' "$TD/install.sh" | sed 's/^ *//; s#\./bin/etk_pitstop\.py#'"$TD"'/bin/etk_pitstop.py#')
[ -n "$KVER_LINE" ] && ok "install.sh derives GTK_KVER" || bad "install.sh derives GTK_KVER"
[ -n "$ED_LINE" ]   && ok "install.sh derives GTK_EDITION from APP_EDITION" || bad "install.sh derives GTK_EDITION from APP_EDITION"

# --- render: template the unit as install.sh does, run its ExecStart --------------------
render() {  # $1 edition  $2 image basename  $3 cmdline
    sed -i "s/^APP_EDITION = .*/APP_EDITION = \"$1\"/" "$TD/bin/etk_pitstop.py"
    grep -q '^APP_EDITION' "$TD/bin/etk_pitstop.py" || echo "APP_EDITION = \"$1\"" >> "$TD/bin/etk_pitstop.py"
    KERNEL_IMAGE="/x/$2"
    GTK_KDATE=$(basename "$KERNEL_IMAGE" | grep -oE '[0-9]{8}' | head -1)
    GTK_VER=$(grep -m1 '^APP_VERSION' "$TD/bin/etk_pitstop.py" | cut -d'"' -f2)
    GTK_EDITION=""; GTK_KVER=""
    [ -n "$ED_LINE" ] && eval "$ED_LINE"
    [ -n "$KVER_LINE" ] && eval "$KVER_LINE"
    local expr; expr=$(eval "echo \"$SEDEXPR\"")
    sed "$expr" "$TD/unit.tmpl" > "$TD/unit"
    echo "$3" > "$TD/cmdline"
    local cmd; cmd=$(grep '^ExecStart=' "$TD/unit" | sed "s#^ExecStart=##; s#/proc/cmdline#$TD/cmdline#")
    eval "$cmd"
}
ABL="console=ttyMSM0 msm.context_keepalive=1 panic=30 root=LABEL=ROCKNIX-GTK"
check "ABL boot names kit, date, edition, kernel" \
      "$(render 'Pitlink' KERNEL.rocknix-gtk-20261001-0.6.3 "$ABL")" \
      "ROCKNIX-GTK $(grep -m1 '^APP_VERSION' "$TD/bin/etk_pitstop.py" | cut -d'"' -f2)-20261001 -- Pitlink Edition -- kernel 0.6.3"
check "GRUB-era boot (KERNEL.gtktest) still prints" \
      "$(render 'Pitlink' KERNEL.rocknix-gtk-20260901-0.5.1 "BOOT_IMAGE=/KERNEL.gtktest root=LABEL=ROCKNIX" | cut -d' ' -f1-2)" \
      "ROCKNIX-GTK $(grep -m1 '^APP_VERSION' "$TD/bin/etk_pitstop.py" | cut -d'"' -f2)-20260901"
check "stock boot stays silent" "$(render 'Pitlink' KERNEL.rocknix-gtk-20261001-0.6.3 'console=ttyMSM0 root=LABEL=ROCKNIX')" ""
check "empty edition drops the clause, keeps the kernel" \
      "$(render '' KERNEL.rocknix-gtk-20261001-0.6.3 "$ABL")" \
      "ROCKNIX-GTK $(grep -m1 '^APP_VERSION' "$TD/bin/etk_pitstop.py" | cut -d'"' -f2)-20261001 -- kernel 0.6.3"
check "four-component kernel generation parses" \
      "$(render '' KERNEL.rocknix-gtk-20261001-0.6.4.4 "$ABL" | sed 's/.*kernel //')" "0.6.4.4"
render 'Pitlink' KERNEL.rocknix-gtk-20261001-0.6.3 "$ABL" >/dev/null
if grep -q '@[A-Z]*@' "$TD/unit"; then bad "no placeholder survives templating ($(grep -o '@[A-Z]*@' "$TD/unit" | tr '\n' ' '))"; else ok "no placeholder survives templating"; fi
if LC_ALL=C grep -q '[^ -~]' <(grep '^ExecStart' "$TD/unit.tmpl"); then bad "ExecStart is ASCII (fbcon console)"; else ok "ExecStart is ASCII (fbcon console)"; fi


# --- the image lane: a CARD's seed unit is rendered by build_gtk_image_v2.sh, never by install.sh ----
# The v0.10.0 card walk (2026-10-23, car12) booted "ROCKNIX-GTK 0.10.0-20261001@EDITION@ -- kernel @KVER@"
# because the lane rendered only the two old placeholders. The lane's render block is extracted by its
# markers and run against the same fakes, so lane and install.sh must agree or this FAILS.
LANE=os-install/build/build_gtk_image_v2.sh
if [ -n "$REV" ]; then git show "$REV:$LANE" > "$TD/lane.sh" 2>/dev/null || : > "$TD/lane.sh"; else cp "$LANE" "$TD/lane.sh"; fi
lane_render() {  # $1 edition  $2 image basename  -> prints the line the card's console would show
    rm -rf "$TD/lane"; mkdir -p "$TD/lane/.seed_config/system.d/multi-user.target.wants" "$TD/lane/repo/bin" "$TD/lane/repo/config"
    cp "$TD/unit.tmpl" "$TD/lane/repo/config/etk-gtk-version.service"
    printf 'ExecStart=/bin/sh -c "echo STALE-SNAPSHOT"\n' > "$TD/lane/.seed_config/system.d/etk-gtk-version.service"
    cp "$TD/bin/etk_pitstop.py" "$TD/lane/repo/bin/etk_pitstop.py"
    sed -i "s/^APP_EDITION = .*/APP_EDITION = \"$1\"/" "$TD/lane/repo/bin/etk_pitstop.py"
    grep -q '^APP_EDITION' "$TD/lane/repo/bin/etk_pitstop.py" || echo "APP_EDITION = \"$1\"" >> "$TD/lane/repo/bin/etk_pitstop.py"
    # the REAL seed's link forms (ls -l seed_config/system.d/multi-user.target.wants): absolute
    # /storage/.config/system.d/<unit> for kit units, absolute /usr/lib/systemd/system/<unit> for
    # OS units, and the 10-04 dangling relative ../etk-sd-rebind.service. The first fixture used
    # relative links only and let a prune that resolved absolute targets in the container pass.
    local W="$TD/lane/.seed_config/system.d/multi-user.target.wants"
    : > "$TD/lane/.seed_config/system.d/etk-osguard.service"; : > "$TD/lane/.seed_config/system.d/etk.service"
    ln -s /storage/.config/system.d/etk-osguard.service "$W/etk-osguard.service"   # live kit unit
    ln -s /storage/.config/system.d/etk.service         "$W/etk.service"           # live kit unit (the invariant)
    ln -s /usr/lib/systemd/system/sshd.service          "$W/sshd.service"          # OS unit: unseen here, must be kept
    ln -s ../etk-sd-rebind.service                      "$W/etk-sd-rebind.service" # dangling relative (10-04 seed)
    ln -s /storage/.config/system.d/etk-gone.service    "$W/etk-gone.service"      # dangling absolute
    local block; block=$(sed -n '/# --- seed boot-identity render (begin) ---/,/# --- seed boot-identity render (end) ---/p' "$TD/lane.sh")
    [ -n "$block" ] || block=$(sed -n '/^ *_GV=\$(grep -m1/,/^ *done$/p' "$TD/lane.sh")   # pre-marker lane: its own two-term render
    [ -n "$block" ] || { echo "NO-LANE-RENDER-BLOCK"; return; }
    ( ER="$TD/lane"; REPO="$TD/lane/repo"; KERNEL="/x/$2"; die(){ echo "LANE-DIE: $*" >&2; exit 1; }; eval "$block" ) > "$TD/lane.out" 2>&1
    echo "$ABL" > "$TD/cmdline"
    local cmd; cmd=$(grep '^ExecStart=' "$TD/lane/.seed_config/system.d/etk-gtk-version.service" | sed "s#^ExecStart=##; s#/proc/cmdline#$TD/cmdline#")
    eval "$cmd"
}
EXPECT="ROCKNIX-GTK $(grep -m1 '^APP_VERSION' "$TD/bin/etk_pitstop.py" | cut -d'"' -f2)-20261001 -- Pitlink Edition -- kernel 0.6.3"
check "image lane renders kit, date, edition, kernel into the card's seed unit" "$(lane_render 'Pitlink' KERNEL.rocknix-gtk-20261001-0.6.3)" "$EXPECT"
check "image lane and install.sh print the same boot line" "$(lane_render 'Pitlink' KERNEL.rocknix-gtk-20261001-0.6.3)" "$(render 'Pitlink' KERNEL.rocknix-gtk-20261001-0.6.3 "$ABL")"
check "image lane: empty edition drops the clause" "$(lane_render '' KERNEL.rocknix-gtk-20261001-0.6.4)" \
      "ROCKNIX-GTK $(grep -m1 '^APP_VERSION' "$TD/bin/etk_pitstop.py" | cut -d'"' -f2)-20261001 -- kernel 0.6.4"
lane_render 'Pitlink' KERNEL.rocknix-gtk-20261001-0.6.3 >/dev/null
LEFT=$(grep -o '@[A-Z]*@' "$TD/lane/.seed_config/system.d/etk-gtk-version.service" 2>/dev/null | sort -u | tr '\n' ' ')
[ -z "$LEFT" ] && ok "no placeholder survives the image lane" || bad "no placeholder survives the image lane (card would print: $LEFT)"
W="$TD/lane/.seed_config/system.d/multi-user.target.wants"
if [ ! -L "$W/etk-sd-rebind.service" ] && [ ! -L "$W/etk-gone.service" ]; then ok "image lane prunes dangling seed wants-links (relative and absolute)"
else bad "image lane prunes dangling seed wants-links (relative and absolute) — left: $(ls "$W" | grep -E 'rebind|gone' | tr '\n' ' ')"; fi
if [ -L "$W/etk-osguard.service" ] && [ -L "$W/etk.service" ] && [ -L "$W/sshd.service" ]; then ok "image lane keeps live kit links (absolute /storage/.config) and OS links (/usr/lib/systemd)"
else bad "image lane keeps live kit links (absolute /storage/.config) and OS links (/usr/lib/systemd) — kept: $(ls "$W" | tr '\n' ' ')"; fi
grep -q 'LANE-DIE' "$TD/lane.out" && bad "image lane render block completes ($(grep LANE-DIE "$TD/lane.out" | head -1))" || ok "image lane render block completes"

echo "boot identity: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" -eq 0 ]
