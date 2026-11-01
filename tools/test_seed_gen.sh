#!/bin/bash
# test_seed_gen.sh — pins the card image's seed to install.sh (0.11.0)
#
# WHY: the GTK card never runs install.sh; /flash/mount-storage.sh copies
# $ETK_ROOT/.seed_config into /storage/.config on boot 2. Until 0.11.0 that seed was
# a static snapshot of one rig (gitignored, 2026-07/10) and every unit install.sh
# grew afterwards was missing on a card: the v0.10.0 card walk (car12, 2026-10-23)
# booted with no internal-mic UCM bind, no USB-net heal, no battery guard, and
# `etk-sd-rebind.service not-found` from a dangling link. os-install/build/gen_seed.sh
# now renders the seed from install.sh's own heredocs + the config/ files it ships.
#
# HOW: no rig. gen_seed.sh runs against the (working tree | --against rev) install.sh
# and config/, then the image lane's boot-identity render + prune block runs on the
# result exactly as build_gtk_image_v2.sh would (extracted by its markers, the way
# tools/test_boot_identity.sh does). Asserts:
#   * the seed's unit + boot-script set == the set install.sh WRITES under
#     /storage/.config/{system.d,custom_scripts} (the list tools/test_usbnet_heal.sh
#     derives for UNINSTALL COVERAGE, minus paths install.sh only removes);
#   * every wants-link resolves inside the seed (or under /usr/lib/systemd/), every
#     unit install.sh enables is linked under each of its WantedBy= targets, and
#     multi-user.target wants etk.service (the Sentry; the lane's invariant);
#   * no @PLACEHOLDER@ survives once the lane's render ran; the boot line matches;
#   * the bodies ARE install.sh's heredocs (byte-identical samples), the unquoted
#     VPU unit has $ETK_ROOT expanded, boot scripts are +x, the mic trio and the
#     capture-S16 pin ride, the Pitlink switch and OS links do not;
#   * the generator FOLLOWS install.sh: a unit added to a copy of install.sh lands
#     in the seed with its link; a unit enabled but never written fails the render;
#   * the lane calls gen_seed.sh by default and stamps .kit_commit.
#
# DISCRIMINATION: `--against <rev>` takes install.sh, config/, gen_seed.sh and the
# lane from that revision. Against 68b14d3 (pre-change: no generator, snapshot seed)
# nearly every case FAILS.
#
#   tools/test_seed_gen.sh                     # working tree — must PASS
#   tools/test_seed_gen.sh --against 68b14d3   # pre-change — must FAIL

set -u
cd "$(dirname "$0")/.." || exit 1
REV=""
[ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"
TD=$(mktemp -d); [ -n "${KEEP:-}" ] && echo "sandbox: $TD" || trap 'rm -rf "$TD"' EXIT
R="$TD/repo"; mkdir -p "$R/os-install/build" "$R/bin" "$R/config/ucm"
FILES="install.sh os-install/build/gen_seed.sh os-install/build/build_gtk_image_v2.sh os-install/build/mount-storage.sh bin/etk_pitstop.py \
       config/etk-gtk-version.service config/etk-ucm.service config/etk-ucm-bind.sh config/ucm/sm8250-HiFi-RP.flip2.conf \
       config/wireplumber-dp-s16.conf config/wireplumber-capture-s16.conf config/MangoHud.conf config/097-etk-turnip-dials.default"
for f in $FILES; do
    if [ -n "$REV" ]; then git show "$REV:$f" > "$R/$f" 2>/dev/null || rm -f "$R/$f"; else cp "$f" "$R/$f"; fi
done
GEN="$R/os-install/build/gen_seed.sh"; LANE="$R/os-install/build/build_gtk_image_v2.sh"; INST="$R/install.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 — got '$2' want '$3'"; fi; }
body() { awk -v m="$1" 'index($0, "<< \x27" m "\x27") {inb=1; next} inb && $0 == m {exit} inb {print}' "$INST"; }

# --- 1. render: gen_seed.sh, then the lane's render + prune block on top ----------------
SEED="$TD/er/.seed_config"; mkdir -p "$TD/er"
if [ -f "$GEN" ] && bash "$GEN" --repo "$R" --out "$SEED" --quiet > "$TD/gen.out" 2>&1; then ok "gen_seed.sh renders the seed from install.sh (exit 0)"
else bad "gen_seed.sh renders the seed from install.sh (exit 0) — $( [ -f "$GEN" ] && tail -n1 "$TD/gen.out" || echo 'no generator at this revision')"; fi
BLOCK=$(sed -n '/# --- seed boot-identity render (begin) ---/,/# --- seed boot-identity render (end) ---/p' "$LANE" 2>/dev/null)
( ER="$TD/er"; REPO="$R"; KERNEL="/x/KERNEL.rocknix-gtk-20261001-0.6.4"; die(){ echo "LANE-DIE: $*" >&2; exit 1; }; eval "$BLOCK" ) > "$TD/lane.out" 2>&1
grep -q 'LANE-DIE' "$TD/lane.out" && bad "lane render + prune block completes on the generated seed ($(grep LANE-DIE "$TD/lane.out" | head -1))" || ok "lane render + prune block completes on the generated seed"

# --- 2. the unit set: what install.sh WRITES under .config is what the card gets -------
# the UNINSTALL COVERAGE list (tools/test_usbnet_heal.sh) + timers, minus paths install.sh
# only ever removes (the retired etk-audio-watchdog) — a path is written when a line
# redirects into it (> path) or scp/rsyncs onto it (:path), and that line is not an rm.
WANT=""; for p in $(grep -o -E '/storage/\.config/(system\.d/[A-Za-z0-9_.@-]+\.(service|timer)|custom_scripts/[A-Za-z0-9_.@-]+\.sh)' "$INST" | sort -u); do
    grep -E "(>|:)[ \"']*$p([ \"']|$)" "$INST" | grep -vq 'rm -f' && WANT="$WANT ${p#/storage/.config/}"
done
WANT=$(printf '%s\n' $WANT | sort -u)
# the Sentry's target is a variable ("$BOOT_SENTRY"): the literal path appears in its assignment only
printf '%s\n' "$WANT" | grep -q '^custom_scripts/01-etk-sentry.sh$' || WANT=$(printf '%s\ncustom_scripts/01-etk-sentry.sh\n' "$WANT" | sort -u)
GOT=$(cd "$SEED" 2>/dev/null && find system.d custom_scripts -maxdepth 1 -type f 2>/dev/null | sort)
check "seed unit + boot-script set == the set install.sh writes" "$(printf '%s' "$GOT" | tr '\n' ' ')" "$(printf '%s' "$WANT" | tr '\n' ' ')"
echo "        ($(printf '%s\n' "$WANT" | grep -c 'system.d/') units, $(printf '%s\n' "$WANT" | grep -c 'custom_scripts/') boot scripts)"
for u in etk-ucm etk-usbnet-heal etk-usb-nowake etk-mdns-heal etk-battery-guard etk-battery-guard-arm etk-battery-guard-resume etk-ufs-pin etk-pitlink-usb etk-sd-rebind etk-vpu-fw; do
    [ -f "$SEED/system.d/$u.service" ] && ok "seed carries $u.service" || bad "seed carries $u.service"
done
[ -f "$SEED/system.d/etk-mdns-heal.timer" ] && ok "seed carries etk-mdns-heal.timer" || bad "seed carries etk-mdns-heal.timer"

# --- 3. wants-links: resolve, cover every enable, keep the invariant --------------------
DANGLING=""; OSLINKS=""
for l in "$SEED"/system.d/*.wants/*; do
    [ -L "$l" ] || continue
    t=$(readlink "$l")
    case "$t" in
        /storage/.config/*) [ -e "$SEED/${t#/storage/.config/}" ] || DANGLING="$DANGLING $(basename "$(dirname "$l")")/$(basename "$l")" ;;
        /usr/lib/systemd/*) OSLINKS="$OSLINKS $(basename "$l")" ;;
        *) [ -e "$(dirname "$l")/$t" ] || DANGLING="$DANGLING $(basename "$(dirname "$l")")/$(basename "$l")" ;;
    esac
done
[ -z "$DANGLING" ] && ok "every wants-link resolves inside the seed (or names an OS unit)" || bad "dangling wants-links:$DANGLING"
[ -z "$OSLINKS" ] && ok "no OS-unit links (the snapshot's sshd/simple-http-server were inert: ROCKNIX gates sshd on ssh.enabled)" || bad "seed carries OS-unit links:$OSLINKS"
[ -L "$SEED/system.d/multi-user.target.wants/etk.service" ] && ok "multi-user.target wants etk.service (the Sentry; the lane's invariant)" || bad "etk.service not wanted — the card would activate nothing"
ENABLED=$(grep -oE 'systemctl enable (/storage/\.config/system\.d/)?[A-Za-z0-9_.@-]+\.(service|timer)' "$INST" | sed -E 's#.*/##; s#^systemctl enable ##' | sort -u)
ENABLED="$ENABLED $(sed -n 's/^for u in \(etk-[a-z -]*\); do$/\1/p' "$INST" | head -1 | tr ' ' '\n' | sed 's/$/.service/')"
MISS=""
for u in $ENABLED; do
    [ -f "$SEED/system.d/$u" ] || { MISS="$MISS $u(no-unit)"; continue; }
    for tgt in $(sed -n 's/^WantedBy=//p' "$SEED/system.d/$u" | head -1); do
        [ -L "$SEED/system.d/$tgt.wants/$u" ] || MISS="$MISS $tgt.wants/$u"
    done
done
[ -z "$MISS" ] && ok "every unit install.sh enables is linked under each of its WantedBy= targets ($(echo $ENABLED | wc -w | tr -d ' ') enables)" || bad "enables without a link:$MISS"
[ -L "$SEED/system.d/basic.target.wants/etk-gtk-version.service" ] && ok "boot identity linked under basic.target (STEP 6.4's ln -sf)" || bad "boot identity not linked under basic.target"
[ -L "$SEED/system.d/sleep.target.wants/etk-usb-nowake.service" ] && [ -L "$SEED/system.d/rocknix.target.wants/etk-usb-nowake.service" ] \
    && ok "a two-target unit (usb-nowake: rocknix + sleep) is linked under both" || bad "usb-nowake not linked under both rocknix.target and sleep.target"
[ -L "$SEED/system.d/suspend.target.wants/etk-battery-guard-resume.service" ] && [ -L "$SEED/system.d/timers.target.wants/etk-mdns-heal.timer" ] \
    && ok "battery-guard-resume under suspend.target, mdns-heal.timer under timers.target" || bad "battery-guard-resume / mdns-heal.timer links missing"
[ -e "$SEED/system.d/multi-user.target.wants/etk-mdns-heal.service" ] && bad "etk-mdns-heal.service linked (install.sh enables only the timer)" || ok "etk-mdns-heal.service is pulled by its timer only (not linked)"
KEPT=$(sed -n 's/^ *seed wants-links kept: //p' "$TD/lane.out" | head -1)
check "lane prunes nothing from a generated seed (kept == links)" "${KEPT:-none}" "$(find "$SEED"/system.d/*.wants -type l 2>/dev/null | wc -l | tr -d ' ')"
grep -q 'pruned dangling' "$TD/lane.out" && bad "lane pruned a link from the generated seed: $(grep pruned "$TD/lane.out" | head -1)" || ok "lane log shows no prune"

# --- 4. placeholders + the boot line ---------------------------------------------------
LEFT=$(grep -rho '@[A-Z]*@' "$SEED" 2>/dev/null | sort -u | tr '\n' ' ')
[ -z "$LEFT" ] && ok "no @PLACEHOLDER@ survives anywhere in the rendered seed" || bad "placeholders survive: $LEFT"
echo "console=ttyMSM0 msm.context_keepalive=1 root=LABEL=ROCKNIX-GTK" > "$TD/cmdline"
CMD=$(grep '^ExecStart=' "$SEED/system.d/etk-gtk-version.service" 2>/dev/null | sed "s#^ExecStart=##; s#/proc/cmdline#$TD/cmdline#")
V=$(grep -m1 '^APP_VERSION' "$R/bin/etk_pitstop.py" | cut -d'"' -f2); E=$(grep -m1 '^APP_EDITION' "$R/bin/etk_pitstop.py" | cut -d'"' -f2)
check "the card's boot line names the kit, date, edition, kernel" "$(eval "$CMD" 2>/dev/null)" "ROCKNIX-GTK $V-20261001${E:+ -- $E Edition} -- kernel 0.6.4"

# --- 5. the bodies ARE install.sh's -----------------------------------------------------
cmp -s <(body USBNETHEAL) "$SEED/custom_scripts/etk-usbnet-heal.sh" 2>/dev/null && ok "etk-usbnet-heal.sh is install.sh's USBNETHEAL body, byte for byte" || bad "etk-usbnet-heal.sh differs from install.sh's heredoc"
cmp -s <(body RBSVC) "$SEED/system.d/etk-sd-rebind.service" 2>/dev/null && ok "etk-sd-rebind.service is install.sh's RBSVC body, byte for byte" || bad "etk-sd-rebind.service differs from install.sh's heredoc"
cmp -s <(body BATTGUARD) "$SEED/custom_scripts/etk-battery-guard.sh" 2>/dev/null && ok "etk-battery-guard.sh is install.sh's BATTGUARD body, byte for byte" || bad "etk-battery-guard.sh differs from install.sh's heredoc"
cmp -s <(body SENTRY) "$SEED/custom_scripts/01-etk-sentry.sh" 2>/dev/null && ok "01-etk-sentry.sh is install.sh's SENTRY body (target resolved from \$BOOT_SENTRY)" || bad "01-etk-sentry.sh differs from install.sh's heredoc"
if [ -f "$SEED/system.d/etk-vpu-fw.service" ]; then
    grep -q '\$' "$SEED/system.d/etk-vpu-fw.service" && bad "etk-vpu-fw.service (unquoted heredoc) still carries a \$VAR" || ok "etk-vpu-fw.service (unquoted heredoc) has \$ETK_ROOT expanded"
    grep -q '^ConditionPathExists=/storage/games-internal/roms/etk/bin/etk_vpu_fw.sh$' "$SEED/system.d/etk-vpu-fw.service" && ok "etk-vpu-fw.service conditions on the kit's path" || bad "etk-vpu-fw.service condition path wrong"
else bad "etk-vpu-fw.service missing (unquoted heredoc not extracted)"; bad "etk-vpu-fw.service condition path wrong"; fi
NOX=$(find "$SEED" -name '*.sh' ! -perm -u+x 2>/dev/null | sed "s#$SEED/##" | tr '\n' ' ')
[ -z "$NOX" ] && ok "every seeded .sh is executable (the v1 203/EXEC lesson)" || bad "non-executable scripts: $NOX"
for p in /storage/.config/etk-turnip-bind.sh /storage/.config/etk-rpcs3-bind.sh /storage/.config/etk-rpcs3-launch.sh /storage/.config/etk-power-apply.sh /storage/.config/custom_scripts/02-etk-coredump.sh /storage/.config/profile.d/098-etk-stage3; do
    [ -f "$SEED/${p#/storage/.config/}" ] && ok "seed carries ${p#/storage/.config/}" || bad "seed carries ${p#/storage/.config/}"
done
REFMISS=""
for u in "$SEED"/system.d/*.service; do
    for p in $(grep -oE '/storage/\.config/[A-Za-z0-9_./@-]+' "$u" 2>/dev/null | sort -u); do
        case "$p" in /storage/.config/profile.d/095-etk-pitlink) continue ;; esac
        [ -e "$SEED/${p#/storage/.config/}" ] || REFMISS="$REFMISS $(basename "$u")->${p#/storage/.config/}"
    done
done
[ -z "$REFMISS" ] && ok "every /storage/.config path a seeded unit names exists in the seed" || bad "units name files the seed lacks:$REFMISS"

# --- 6. the mic, the pins, the knobs -----------------------------------------------------
[ -f "$SEED/system.d/etk-ucm.service" ] && [ -x "$SEED/etk-ucm-bind.sh" ] && cmp -s "$SEED/etk-ucm/HiFi-RP.conf" "$R/config/ucm/sm8250-HiFi-RP.flip2.conf" 2>/dev/null \
    && ok "the mic trio rides: etk-ucm.service + etk-ucm-bind.sh (+x) + etk-ucm/HiFi-RP.conf == config/ucm/" || bad "mic trio incomplete"
cmp -s "$SEED/system.d/etk-ucm.service" "$R/config/etk-ucm.service" 2>/dev/null && ok "etk-ucm.service == config/etk-ucm.service (what STEP 6.76 ships)" || bad "etk-ucm.service differs from config/"
[ -f "$SEED/wireplumber/wireplumber.conf.d/51-etk-capture-s16.conf" ] && [ -f "$SEED/wireplumber/wireplumber.conf.d/50-etk-dp-audio-s16.conf" ] \
    && ok "WirePlumber S16 pins ride (capture S16 is required by the internal mic)" || bad "WirePlumber S16 pins missing"
grep -q '^ETK_BATTERY_FLOOR=10$' "$SEED/etk_battery_guard.conf" 2>/dev/null && grep -q '^ETK_BATTERY_CHECK_MIN=30$' "$SEED/etk_battery_guard.conf" 2>/dev/null \
    && ok "battery guard conf seeded with install.sh's defaults (10 %, 30 min)" || bad "battery guard conf missing or not the defaults"
[ -e "$SEED/profile.d/095-etk-pitlink" ] && bad "Pitlink switch seeded ON (must ship off)" || ok "Pitlink ships off: unit linked, 095-etk-pitlink absent (ConditionPathExists keeps it down)"
[ -e "$SEED/profile.d/096-etk-rpcs3-flags" ] && bad "RPCS3 env flags seeded (operator-only)" || ok "no RPCS3 env flags seeded"
[ -f "$SEED/MangoHud/MangoHud.conf" ] && ok "MangoHud.conf rides (STEP 3's push)" || bad "MangoHud.conf missing"

# --- 7. the generator FOLLOWS install.sh (not a list) -----------------------------------
R2="$TD/repo2"; rm -rf "$R2"; cp -a "$R" "$R2"
cat >> "$R2/install.sh" <<'ADD'
ssh $RIG_SSH "sh -s" <<'NEWREMOTE'
cat << 'NEWUNIT' > /storage/.config/system.d/etk-newthing.service
[Unit]
Description=a unit added after this harness was written

[Service]
ExecStart=/bin/true

[Install]
WantedBy=rocknix.target sleep.target
NEWUNIT
systemctl enable /storage/.config/system.d/etk-newthing.service >/dev/null 2>&1
NEWREMOTE
ADD
S2="$TD/er2/.seed_config"
if [ -f "$GEN" ] && bash "$GEN" --repo "$R2" --out "$S2" --quiet >/dev/null 2>&1 && [ -f "$S2/system.d/etk-newthing.service" ] \
   && [ -L "$S2/system.d/rocknix.target.wants/etk-newthing.service" ] && [ -L "$S2/system.d/sleep.target.wants/etk-newthing.service" ]; then
    ok "a unit added to install.sh lands in the seed with a link under each WantedBy= (the generator follows install.sh)"
else bad "a unit added to install.sh does not reach the seed"; fi
R3="$TD/repo3"; rm -rf "$R3"; cp -a "$R" "$R3"
printf 'systemctl enable /storage/.config/system.d/etk-ghost.service >/dev/null 2>&1\n' >> "$R3/install.sh"
if [ -f "$GEN" ] && ! bash "$GEN" --repo "$R3" --out "$TD/er3/.seed_config" --quiet > "$TD/gen3.out" 2>&1 && grep -q 'SEED_FAIL' "$TD/gen3.out"; then
    ok "a unit install.sh enables but never writes FAILS the render (SEED_FAIL), never a silent card"
else bad "an enable without a unit did not fail the render"; fi

# --- 8. the lane ships the generated seed ------------------------------------------------
grep -q 'gen_seed.sh" --repo "\$REPO" --out "\$ER/.seed_config"' "$LANE" 2>/dev/null && ok "image lane renders the seed with gen_seed.sh into \$ER/.seed_config" || bad "image lane does not call gen_seed.sh"
grep -q '^SEED_GEN="\${SEED_GEN:-1}"' "$LANE" 2>/dev/null && ok "SEED_GEN defaults ON (a card is hostless by default)" || bad "SEED_GEN is not the default"
grep -q 'ETKR/.seed_config' "$R/os-install/build/mount-storage.sh" 2>/dev/null && ok "mount-storage.sh installs \$ETK_ROOT/.seed_config on boot 2 (where the lane puts it)" || bad "hook path drifted from the lane"
grep -q 'printf .%s.n. "\$KIT_COMMIT" > "\$ER/.kit_commit"' "$LANE" 2>/dev/null && ok "image lane stamps .kit_commit (the v0.10.0 card read it unstamped)" || bad "image lane does not stamp .kit_commit"
grep -q 'SEED_CONFIG=/work/build/seed_config' tools/forge/lane_image.sh 2>/dev/null && [ -z "$REV" ] && bad "lane_image.sh still passes the static seed_config snapshot" || ok "lane_image.sh no longer stages the static seed_config snapshot"

echo "seed gen: $PASS passed, $FAIL failed${REV:+ (against $REV)}"
[ "$FAIL" -eq 0 ]
