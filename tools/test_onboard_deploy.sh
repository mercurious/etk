#!/bin/bash
# ==========================================================
# tools/test_onboard_deploy.sh -- install.sh STEP 6.86 (ONBOARD knobs + VPU firmware unit)
# ==========================================================
# Runs install.sh's real STEP 6.86 block (extracted) with a stub `ssh` that executes each
# remote body on a FAKE RIG in a temp dir (/storage and /sys rewritten into it): a stub
# systemctl that records calls and, on `restart`, runs the unit's own ExecStart line (so a
# wrong path in the unit fails here); the real bin/etk_vpu_fw.sh (pin sed'ed per case)
# against a fake sysfs and a fake iris core. Then uninstall.sh's STEP 6.86 lines,
# expanded the way the host expands its unquoted CLEAN heredoc, on the same fake rig.
# Never contacts a rig; never touches the real /sys or /storage.
#
#   tools/test_onboard_deploy.sh                  # working tree -- must PASS
#   tools/test_onboard_deploy.sh --against main   # pre-change -- must FAIL
set -u
cd "$(dirname "$0")/.." || exit 1
REV=""; [ "${1:-}" = "--against" ] && REV="${2:?--against needs a revision}"
FAIL=0; PASS=0
ok()   { PASS=$((PASS+1)); printf 'ok   %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$*"; }
summary() { echo "test_onboard_deploy: $PASS passed, $FAIL failed${REV:+ (against $REV)}"; [ "$FAIL" = 0 ]; }
TD=$(mktemp -d /tmp/etk_onbdeploy_XXXXXX)
[ -n "${KEEP:-}" ] && echo "sandbox: $TD" || trap 'rm -rf "$TD"' EXIT
for f in install.sh uninstall.sh etk.conf.example bin/etk_vpu_fw.sh; do
    o="$TD/$(basename "$f")"
    if [ -n "$REV" ]; then git show "$REV:$f" > "$o" 2>/dev/null || : > "$o"; else cp "$f" "$o" 2>/dev/null || : > "$o"; fi
done

# --- static: knobs, push list, uninstall coverage ---
for k in 'ETK_ONBOARD=1' 'ETK_ONBOARD_RUNNING_FPS=0' 'ETK_ONBOARD_QUALITY=70' 'ETK_VPU_FW=1'; do
    grep -q "^$k\$" "$TD/etk.conf.example" && ok "etk.conf.example documents $k" || fail "etk.conf.example lacks $k"
done
grep -qE "^tui_rsync .*--delete .*\./bin/ \\\$RIG_SSH:\\\$ETK_ROOT/bin/" "$TD/install.sh" \
  && grep -qE 'chmod \+x \$ETK_ROOT/bin/\*' "$TD/install.sh" \
  && ok "bin/ ships wholesale + chmod +x bin/* (covers etk_vpu_fw.sh and onboard_cap.sh; no extra push line)" \
  || fail "install.sh no longer pushes ./bin/ wholesale -- etk_vpu_fw.sh / onboard_cap.sh need push-list lines"
[ -s "$TD/etk_vpu_fw.sh" ] && ok "bin/etk_vpu_fw.sh exists (rides the bin/ push)" || fail "bin/etk_vpu_fw.sh missing"
for w in /storage/.config/system.d/etk-vpu-fw.service 'rm -rf /storage/etk-fw' 'rm -f /storage/etk_vpu_fw.log' firmware_class/parameters/path; do
    grep -qF "$w" "$TD/uninstall.sh" && ok "uninstall.sh: $w" || fail "uninstall.sh never does: $w"
done

# --- the block ---
awk '/^# STEP 6\.86:/ {i=1} i {print} i && /VPU firmware removed \(kill-switch/ {getline; print; exit}' "$TD/install.sh" > "$TD/block.sh"
if [ ! -s "$TD/block.sh" ] || [ ! -s "$TD/etk_vpu_fw.sh" ]; then fail "no STEP 6.86 block (or no script) to run"; summary; exit 1; fi
bash -n "$TD/block.sh" && ok "the STEP 6.86 block parses" || fail "STEP 6.86 block has a syntax error"
grep -q 'rig_toast' "$TD/block.sh" && fail "STEP 6.86 adds a rig_toast (the stage roster is pinned by test_notify.py)" || ok "no new stage toast (roster stays pinned)"

DEV=aa00000.video-codec
head -c 32768 /dev/urandom > "$TD/venus.mbn"
GOOD=$(sha256sum "$TD/venus.mbn" | cut -d' ' -f1)
EROOT=/storage/games-internal/roms/etk
UNIT=storage/.config/system.d/etk-vpu-fw.service
FWP=sys/module/firmware_class/parameters/path
cat > "$TD/probe.sh" << 'PROBE'
#!/bin/sh
# fake iris core: opens once qcom-iris was bound with the blob staged under the path
[ -e "$PROBE_SB/up" ] && exit 0
b=$(cat "$PROBE_SB/sys/bus/platform/drivers/qcom-iris/bind"); p=$(cat "$PROBE_SB/sys/module/firmware_class/parameters/path")
if [ "$b" = aa00000.video-codec ] && [ -n "$p" ] && cmp -s "$p/qcom/vpu/vpu20_p4.mbn" "$PROBE_SB/lib/firmware/qcom/vpu-1.0/venus.mbn"; then : > "$PROBE_SB/up"; exit 0; fi
echo "Cannot allocate memory"; exit 1
PROBE
chmod +x "$TD/probe.sh"

mkrig() {  # mkrig <case> <pin>: a fake rig with a dead encoder and the real script on it
    R=$TD/rig/$1; rm -rf "$R"
    mkdir -p "$R/sys/bus/platform/devices/$DEV" "$R/sys/bus/platform/drivers/qcom-iris/$DEV" "$R/sys/module/firmware_class/parameters" \
             "$R/sys/class/video4linux/video1" "$R/dev" "$R/lib/firmware/qcom/vpu-1.0" "$R$EROOT/bin" "$R/stubs" "$R/storage/.config"
    echo qcom-iris-encoder > "$R/sys/class/video4linux/video1/name"
    : > "$R/$FWP"; : > "$R/sys/bus/platform/drivers/qcom-iris/bind"; : > "$R/sys/bus/platform/drivers/qcom-iris/unbind"
    cp "$TD/venus.mbn" "$R/lib/firmware/qcom/vpu-1.0/venus.mbn"
    : > "$R/env.sh"
    sed -e "s|^VPU_FW_SHA256=.*|VPU_FW_SHA256=\"$2\"|" -e "s|\${VPU_SYSFS_ROOT:-}|\${VPU_SYSFS_ROOT:-$R}|" "$TD/etk_vpu_fw.sh" > "$R$EROOT/bin/etk_vpu_fw.sh"
    cat > "$R/stubs/systemctl" << STUB
#!/bin/sh
echo "systemctl \$*" >> "$R/systemctl.log"
U="$R/storage/.config/system.d/\${2:-none}"
case "\$1" in
  is-enabled) grep -q "^systemctl enable .*/\$2\$" "$R/systemctl.log" && [ -e "\$U" ] && echo enabled || echo disabled ;;
  restart) c=\$(sed -n 's/^ConditionPathExists=//p' "\$U"); [ -z "\$c" ] || [ -e "\$c" ] || exit 0
           \$(sed -n 's/^ExecStart=//p' "\$U") >> "$R/unit.out" 2>&1 ;;
esac
exit 0
STUB
    chmod +x "$R/stubs/systemctl"
}
cat > "$TD/runner.sh" << 'RUNNER'
# install.sh's environment for one STEP 6.86 run; ssh = execute on the fake rig $R
RIG_SSH=root@rig G= N= Y= C=; ETK_ROOT=/storage/games-internal/roms/etk
say() { printf 'SAY %s\n' "$1"; }
ssh() {
    local in er; in=$(cat); echo "SSH $*" >> "$R/ssh.log"
    case "$*" in *"sh -s"*) ;; *) echo "SSH? $*" >> "$R/ssh.log"; return 0 ;; esac
    er=$(printf '%s' "$*" | sed -n "s/.*ETK_ROOT='\([^']*\)' sh -s.*/\1/p")
    [ -n "$er" ] || { echo "SSH-NOROOT $*" >> "$R/ssh.log"; return 1; }
    printf '%s\n' "$in" | sed -e "s#/storage#$R/storage#g" -e "s#/sys/#$R/sys/#g" > "$R/remote.sh"
    env ETK_ROOT="$R$er" PATH="$R/stubs:$PATH" VPU_SYSFS_ROOT="$R" VPU_DEV_DIR="$R/dev" VPU_FW_DIR="$R/storage/etk-fw" \
        VPU_STOCK_FW="$R/lib/firmware/qcom/vpu-1.0/venus.mbn" VPU_LOG="$R/storage/etk_vpu_fw.log" VPU_WAIT_S=1 \
        VPU_PROBE="$PROBE" PROBE_SB="$R" ETK_VPU_ENV="$R/env.sh" sh "$R/remote.sh"
}
. "$BLOCK" < /dev/null
RUNNER
step() {  # step <case> [VAR=val ...] -> $R/say
    R=$TD/rig/$1; shift
    env -i PATH="$PATH" HOME="$TD" R="$R" BLOCK="$TD/block.sh" PROBE="$TD/probe.sh" "$@" bash "$TD/runner.sh" > "$R/say" 2>&1
}
says() { grep -F "$2" "$TD/rig/$1/say" > /dev/null; }

# --- 1. defaults: DEPLOY, the unit runs apply through its own ExecStart, verify OK on the line ---
mkrig def "$GOOD"; step def; R=$TD/rig/def
[ -f "$R/$UNIT" ] && grep -q "^systemctl enable $R/storage/.config/system.d/etk-vpu-fw.service$" "$R/systemctl.log" \
  && grep -q '^systemctl restart etk-vpu-fw.service' "$R/systemctl.log" && ok "defaults -> DEPLOY (unit written, enabled, run now)" || fail "defaults: no deploy ($(cat "$R/systemctl.log" 2>/dev/null | tr '\n' ' '))"
grep -qx "ExecStart=/bin/bash $R$EROOT/bin/etk_vpu_fw.sh apply" "$R/$UNIT" && ok "ExecStart = /bin/bash \$ETK_ROOT/bin/etk_vpu_fw.sh apply (ETK_ROOT from env.sh, not a second literal)" || fail "ExecStart: $(grep ExecStart "$R/$UNIT")"
grep -qx 'Type=oneshot' "$R/$UNIT" && grep -qx 'RemainAfterExit=yes' "$R/$UNIT" && grep -qx 'WantedBy=rocknix.target' "$R/$UNIT" \
  && grep -q '^After=.*local-fs.target' "$R/$UNIT" && grep -q '^After=.*etk-sd-rebind.service' "$R/$UNIT" \
  && grep -qx "ConditionPathExists=$R$EROOT/bin/etk_vpu_fw.sh" "$R/$UNIT" \
  && ok "unit: oneshot, RemainAfterExit, After= local-fs + the SD rebind (script lives on the card), Condition on the script" || fail "unit shape: $(tr '\n' '|' < "$R/$UNIT")"
grep -q 'WOKE: ' "$R/storage/etk_vpu_fw.log" 2>/dev/null && [ "$(cat "$R/$FWP")" = "$R/storage/etk-fw" ] && [ "$(cat "$R/sys/bus/platform/drivers/qcom-iris/bind")" = $DEV ] \
  && ok "the unit's apply ran on the fake rig: seeded, path set, rebound, WOKE" || fail "unit apply did not wake: $(tail -n2 "$R/storage/etk_vpu_fw.log" 2>/dev/null) $(cat "$R/unit.out" 2>/dev/null)"
says def "[ETK] VPU firmware armed (etk-vpu-fw.service; iris H.264 encoder: VPU verify OK: $R/dev/video1 opens" \
  && ok "install line names the verify result (OK)" || fail "install line: $(grep VPU "$R/say")"
says def "ONBOARD live mirror: on" && says def "PANEL during a game 0 fps, JPEG quality 70" && ok "ONBOARD on by default, line names RUNNING_FPS 0 + quality 70" || fail "ONBOARD line: $(grep ONBOARD "$R/say")"
LC_ALL=C grep -q '[^ -~]' "$R/say" && fail "non-ASCII in the install lines" || ok "install lines are plain ASCII (Latin-1 safe)"

# --- 2. unpinned script (the shipped placeholder): deploys, but the line says the encoder is not up ---
mkrig ph "UNPINNED-sha256-of-stock-venus.mbn"; step ph; R=$TD/rig/ph
[ -f "$R/$UNIT" ] && says ph "[WARN] VPU firmware unit armed, but the encoder is not up (VPU verify FAIL:" && says ph "not pinned" \
  && ok "unpinned -> unit armed, WARN line carries verify FAIL + the REFUSED reason" || fail "unpinned: $(grep VPU "$R/say")"
[ ! -e "$R/storage/etk-fw" ] && [ ! -s "$R/sys/bus/platform/drivers/qcom-iris/bind" ] && ok "unpinned -> nothing seeded, no rebind on the rig" || fail "unpinned rig was touched"

# --- 3. ETK_VPU_FW=0 on the deployed rig: REMOVE (unit, path, blob) through the script's undo ---
step def ETK_VPU_FW=0; R=$TD/rig/def
[ ! -e "$R/$UNIT" ] && grep -q '^systemctl disable etk-vpu-fw.service' "$R/systemctl.log" && [ ! -e "$R/storage/etk-fw" ] \
  && [ -z "$(cat "$R/$FWP")" ] && ok "ETK_VPU_FW=0 -> REMOVE: unit gone, disabled, path cleared, /storage/etk-fw deleted" || fail "remove: unit=$([ -e "$R/$UNIT" ] && echo present) path='$(cat "$R/$FWP")' fw=$(ls "$R/storage")"
says def "VPU firmware removed (kill-switch ETK_VPU_FW=0" && ok "install line names the kill-switch" || fail "remove line: $(grep VPU "$R/say" | tail -n1)"
mkrig ns "$GOOD"; R=$TD/rig/ns; rm -f "$R$EROOT/bin/etk_vpu_fw.sh"; mkdir -p "$R/storage/etk-fw/qcom/vpu"; printf '%s' "$R/storage/etk-fw" > "$R/$FWP"
step ns ETK_VPU_FW=0
[ ! -e "$R/storage/etk-fw" ] && [ -z "$(cat "$R/$FWP")" ] && ok "REMOVE without the script on the rig: inline path clear + rm" || fail "inline remove: path='$(cat "$R/$FWP")'"

# --- 4. ONBOARD knobs ---
mkrig on0 "$GOOD"; step on0 ETK_ONBOARD=0
says on0 "ONBOARD live mirror: off (kill-switch ETK_ONBOARD=0" && ok "ETK_ONBOARD=0 -> install line names the kill-switch" || fail "ONBOARD=0: $(grep ONBOARD "$TD/rig/on0/say")"
mkrig pl0 "$GOOD"; step pl0 ETK_PITLINK_USB=0 ETK_ONBOARD_RUNNING_FPS=5 ETK_ONBOARD_QUALITY=50
says pl0 "[WARN] ONBOARD rides the Pitlink USB daemon" && says pl0 "PANEL during a game 5 fps, JPEG quality 50" \
  && ok "ETK_PITLINK_USB=0 warns ONBOARD has no carrier; knob values reach the line" || fail "pitlink=0: $(grep ONBOARD "$TD/rig/pl0/say")"

# --- 5. uninstall.sh's STEP 6.86 lines (host-expanded like CLEAN) on a deployed rig ---
awk '/# VPU firmware \(install.sh STEP 6\.86\)/ {i=1} i {print} i && /Removed: etk-vpu-fw.service/ {exit}' "$TD/uninstall.sh" > "$TD/un.snip"
if [ -s "$TD/un.snip" ]; then
    { echo 'cat << CLEAN'; cat "$TD/un.snip"; echo 'CLEAN'; } > "$TD/un.gen"
    ETK_ROOT=$EROOT ZAP_VAULT=0 bash "$TD/un.gen" | sed -e "s#/storage#\$R/storage#g" -e "s#/sys/#\$R/sys/#g" > "$TD/un.remote"
    for c in plain displaced; do
        mkrig "un$c" "$GOOD"; step "un$c"; R=$TD/rig/un$c
        : > "$R/up"   # (the encoder stays up; uninstall never rebinds)
        [ "$c" = displaced ] && mkdir -p "$R/storage/etk-fw" && printf '/foreign/fw\n' > "$R/storage/etk-fw/.displaced_path"
        echo "x" > "$R/storage/etk_vpu_fw.log"
        R="$R" PATH="$R/stubs:$PATH" sh -c ". '$TD/un.remote'" > /dev/null 2>&1
        want=""; [ "$c" = displaced ] && want=/foreign/fw
        [ ! -e "$R/$UNIT" ] && [ ! -e "$R/storage/etk-fw" ] && [ ! -e "$R/storage/etk_vpu_fw.log" ] && [ "$(cat "$R/$FWP")" = "$want" ] \
          && ok "uninstall ($c): unit, /storage/etk-fw, log removed; firmware path -> '${want}'" || fail "uninstall ($c): path='$(cat "$R/$FWP")' left: $(ls "$R/storage" "$R/storage/.config/system.d" 2>/dev/null | tr '\n' ' ')"
    done
else
    fail "uninstall.sh has no STEP 6.86 removal"
fi

summary
