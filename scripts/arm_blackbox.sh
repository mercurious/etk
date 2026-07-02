#!/bin/bash
# ==========================================================
# ETK PANIC BLACK BOX — ARMER (host-run, operator-invoked, v1.0.0)
# ==========================================================
# Arms the WRITE side of the Panic Black Box on the rig:
#   1. Appends `reserve_mem=1M:4096:etk_ramoops` to EVERY `linux` line in
#      BOTH grub twins (/flash/EFI/BOOT/grub.cfg AND /flash/boot/grub/grub.cfg
#      — grubenv can select any of the 12 menuentries, so all lines get it;
#      a partial edit risks a boot path without ramoops).
#   2. Stages persistent module autoload + params from /storage (no rootfs
#      surgery): modules-load.d/etk-ramoops.conf + modprobe.d/etk-ramoops.conf
#      (mem_name=etk_ramoops pairs the module with the reserved region).
# After arming, the OPERATOR cold-boots on-device (never remotely — Law #6).
# Then `arm_blackbox.sh --verify` confirms the armed state read-only.
# `arm_blackbox.sh --revert` removes the cmdline token + confs (backups kept).
#
# DELIBERATELY NOT part of install.sh's always-run flow: a bad grub edit can
# brick boot, so this is a one-shot operator action, validated on a cold boot
# before anything depends on it (validate-before-integrate). install.sh's
# STEP 6.65 only WARNS if a ROCKNIX update reverted the cmdline token.
#
# The reservation costs 1 MiB of the rig's ~7.4 GiB RAM. ramoops captures
# Oops+Panic kmsg dumps (max_reason=2) — it will NOT record GPU soft-freezes
# (those never panic the kernel; the existing spotter/hangrd path covers them).
set -u

ETK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ETK_DIR/etk.conf" ] && . "$ETK_DIR/etk.conf"
RIG_SSH="${RIG_SSH:-root@SM8250.local}"

# The full token rides the kernel cmdline: the reservation itself PLUS the
# ramoops module parameters (kernel hands ramoops.* to the module at load
# time, so this works even if /storage/.config/modprobe.d turns out not to be
# honored by ROCKNIX — the modprobe.d conf below is redundancy, not the
# load-bearing path). Two hard-won additions (field-tested 2026-07-02):
#  - efi_pstore.pstore_disable=1: pstore allows ONE backend and the built-in
#    efi_pstore RACES ramoops for the slot (won the lottery on one boot:
#    "backend 'efi_pstore' already in use: ignoring 'ramoops'", EBUSY -16).
#    U-Boot EFI-var persistence is useless on this device; disable it so
#    ramoops always wins.
#  - panic=10: the kernel's default after a panic is to HANG forever (frozen
#    rig, dump written but no reboot). 10s auto-reboot brings the rig back so
#    blackbox_d harvests and the Sentry synthesizes the PANIC row unattended.
TOKEN='reserve_mem=1M:4096:etk_ramoops ramoops.mem_name=etk_ramoops ramoops.record_size=0x20000 ramoops.max_reason=2 ramoops.ecc=1 efi_pstore.pstore_disable=1 panic=10'
# Strip pattern removing ALL blackbox-owned cmdline params (any older token
# version) — makes arming idempotent across token upgrades and powers revert.
STRIP='s/ reserve_mem=[^ ]*//g; s/ ramoops\.[^ ]*//g; s/ efi_pstore\.pstore_disable=[^ ]*//g; s/ panic=10//g'
TWINS='/flash/EFI/BOOT/grub.cfg /flash/boot/grub/grub.cfg'

verify() {
    ssh "$RIG_SSH" "
        echo '--- live cmdline ---'
        grep -o 'reserve_mem=[^ ]*' /proc/cmdline || echo 'reserve_mem: NOT in live cmdline (cold boot pending, or unarmed)'
        echo '--- ramoops module ---'
        if [ -d /sys/module/ramoops ]; then
            echo \"ramoops loaded: mem_name=\$(cat /sys/module/ramoops/parameters/mem_name 2>/dev/null)\"
        else
            echo 'ramoops: NOT loaded'
        fi
        echo '--- grub twins ---'
        for f in $TWINS; do
            [ -f \"\$f\" ] || { echo \"\$f: MISSING\"; continue; }
            total=\$(grep -c '^[[:space:]]*linux ' \"\$f\")
            armed=\$(grep -c '$TOKEN' \"\$f\")
            echo \"\$f: \$armed/\$total linux lines carry the token\"
        done
        echo '--- module confs ---'
        ls -la /storage/.config/modules-load.d/etk-ramoops.conf /storage/.config/modprobe.d/etk-ramoops.conf 2>/dev/null || echo 'module confs absent'
        echo '--- pstore ---'
        ls /sys/fs/pstore/ 2>/dev/null | head -5
    "
}

revert() {
    echo "[ETK] Reverting Panic Black Box arming on $RIG_SSH ..."
    ssh "$RIG_SSH" "
        set -e
        mount -o remount,rw /flash
        stamp=\$(date +%Y%m%d_%H%M%S)
        for f in $TWINS; do
            [ -f \"\$f\" ] || continue
            cp \"\$f\" \"\$f.etkbak-\$stamp\"
            sed '$STRIP' \"\$f\" > \"\$f.etknew\" && mv \"\$f.etknew\" \"\$f\"
            echo \"reverted: \$f (backup \$f.etkbak-\$stamp)\"
        done
        sync
        mount -o remount,ro /flash || true
        rm -f /storage/.config/modules-load.d/etk-ramoops.conf /storage/.config/modprobe.d/etk-ramoops.conf
        echo 'module confs removed'
    " && echo "[ETK] Reverted. Reboot on-device to unload the reservation."
}

arm() {
    echo "[ETK] Arming Panic Black Box on $RIG_SSH ..."
    ssh "$RIG_SSH" "
        set -e
        mount -o remount,rw /flash
        stamp=\$(date +%Y%m%d_%H%M%S)
        for f in $TWINS; do
            if [ ! -f \"\$f\" ]; then echo \"WARN: \$f missing, skipped\"; continue; fi
            cp \"\$f\" \"\$f.etkbak-\$stamp\"
            # Strip any prior token version, then append the current one to
            # every kernel line (menuentry + recovery variants) — idempotent
            # across upgrades. awk rewrite + rename: no in-place sed on vfat.
            sed '$STRIP' \"\$f\" > \"\$f.etkstrip\"
            awk -v tok='$TOKEN' '
                /^[[:space:]]*linux / && index(\$0, tok) == 0 { print \$0 \" \" tok; next }
                { print }
            ' \"\$f.etkstrip\" > \"\$f.etknew\" && mv \"\$f.etknew\" \"\$f\"
            rm -f \"\$f.etkstrip\"
            armed=\$(grep -c '$TOKEN' \"\$f\")
            total=\$(grep -c '^[[:space:]]*linux ' \"\$f\")
            echo \"armed: \$f (\$armed/\$total linux lines, backup \$f.etkbak-\$stamp)\"
        done
        sync
        mount -o remount,ro /flash || true
        mkdir -p /storage/.config/modules-load.d /storage/.config/modprobe.d
        printf 'ramoops\n' > /storage/.config/modules-load.d/etk-ramoops.conf
        printf 'options ramoops mem_name=etk_ramoops record_size=0x20000 max_reason=2 ecc=1\n' \
            > /storage/.config/modprobe.d/etk-ramoops.conf
        sync
        echo 'module confs staged (autoload + params from /storage, persistent)'
    " || { echo "[ETK] ARMING FAILED — nothing depends on a partial arm; re-run after fixing."; exit 1; }
    cat <<'EOF'
[ETK] Armed. NEXT STEPS (operator, on-device):
  1. Cold-boot the rig (power off/on — never remotely).
  2. Run: scripts/arm_blackbox.sh --verify
     Expect: reserve_mem in live cmdline, ramoops loaded with mem_name=etk_ramoops.
  3. OPTIONAL definitive test (only when you accept a controlled crash):
     on the rig:  echo 1 > /proc/sys/kernel/sysrq && echo c > /proc/sysrq-trigger
     -> rig panics + reboots; blackbox_d harvests the dump on the way up;
     check etk_telemetry/blackbox/pstore/<stamp>/ for dmesg-ramoops-* records.
EOF
}

case "${1:-}" in
    --verify) verify ;;
    --revert) revert ;;
    *) arm ;;
esac
