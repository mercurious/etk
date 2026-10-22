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

echo "boot identity: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" -eq 0 ]
