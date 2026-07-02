#!/bin/bash
# ==========================================================
# ETK PANIC BLACK BOX — ARMER (host-run, operator-invoked, v1.2.0)
# ==========================================================
# DEFAULT arming (certified on SM8250, 2026-07-02): appends `panic=10` to
# EVERY `linux` line in BOTH grub twins so kernel panics AUTO-REBOOT in 10s
# (kernel default is to hang frozen forever) — the rig self-recovers, the
# Sentry synthesizes the PANIC ledger row, and blackbox_d's kmsg flight
# recorder preserves the lead-up evidence.
#
# --ramoops additionally arms RAM-backed panic-dump capture (reserve_mem +
# ramoops module). *** FALSIFIED ON SM8250 (2026-07-02, 3 live panic tests):
# Snapdragon DDR scrambling re-keys on EVERY reset, so plain-DDR contents are
# garbage across any reboot ("ramoops: uncorrectable error in header" on all
# boots, same address). efi_pstore (U-Boot EFI vars) also tested: registers
# as backend but persists nothing at panic time. NO kernel-native persistent
# dump backend exists on this SoC without firmware cooperation. --ramoops is
# kept ONLY for future non-scrambling devices — do not re-test on SM8250. ***
#
# `--verify` read-only state check; `--revert` strips all tokens + confs
# (backups kept). After any arm/revert the OPERATOR reboots on-device (never
# remotely — Law #6). NOT part of install.sh's always-run flow: boot-path
# edits stay one-shot operator actions; install.sh STEP 6.65 only WARNS if a
# ROCKNIX update reverted the token.
set -u

ETK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ETK_DIR/etk.conf" ] && . "$ETK_DIR/etk.conf"
RIG_SSH="${RIG_SSH:-root@SM8250.local}"

# Default token: panic auto-reboot only (the SM8250-certified configuration).
TOKEN='panic=10'
# --ramoops token: adds RAM-backed dump capture. efi_pstore.pstore_disable=1
# stops the built-in efi_pstore racing ramoops for pstore's single backend
# slot (observed EBUSY -16). FALSIFIED on SM8250 (DDR scrambling) — future
# non-scrambling devices only.
TOKEN_RAMOOPS='reserve_mem=1M:4096:etk_ramoops ramoops.mem_name=etk_ramoops ramoops.record_size=0x20000 ramoops.max_reason=2 ramoops.ecc=1 efi_pstore.pstore_disable=1 panic=10'
# Strip pattern removing ALL blackbox-owned cmdline params (any older token
# version) — makes arming idempotent across token upgrades and powers revert.
STRIP='s/ reserve_mem=[^ ]*//g; s/ ramoops\.[^ ]*//g; s/ efi_pstore\.pstore_disable=[^ ]*//g; s/ panic=10//g'
TWINS='/flash/EFI/BOOT/grub.cfg /flash/boot/grub/grub.cfg'

verify() {
    ssh "$RIG_SSH" "
        echo '--- live cmdline ---'
        grep -q 'panic=10' /proc/cmdline && echo 'panic=10: ARMED (auto-reboot on panic)' || echo 'panic=10: NOT in live cmdline (reboot pending, or unarmed)'
        grep -o 'reserve_mem=[^ ]*' /proc/cmdline || echo 'reserve_mem: absent (expected — RAM capture falsified on SM8250)'
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
            awk -v tok='$ARM_TOKEN' '
                /^[[:space:]]*linux / && index(\$0, tok) == 0 { print \$0 \" \" tok; next }
                { print }
            ' \"\$f.etkstrip\" > \"\$f.etknew\" && mv \"\$f.etknew\" \"\$f\"
            rm -f \"\$f.etkstrip\"
            armed=\$(grep -c '$ARM_TOKEN' \"\$f\")
            total=\$(grep -c '^[[:space:]]*linux ' \"\$f\")
            echo \"armed: \$f (\$armed/\$total linux lines, backup \$f.etkbak-\$stamp)\"
        done
        sync
        mount -o remount,ro /flash || true
        if [ '$WITH_RAMOOPS' = '1' ]; then
            mkdir -p /storage/.config/modules-load.d /storage/.config/modprobe.d
            printf 'ramoops\n' > /storage/.config/modules-load.d/etk-ramoops.conf
            printf 'options ramoops mem_name=etk_ramoops record_size=0x20000 max_reason=2 ecc=1\n' \
                > /storage/.config/modprobe.d/etk-ramoops.conf
            sync
            echo 'ramoops module confs staged (NOTE: falsified on SM8250 — non-scrambling devices only)'
        else
            rm -f /storage/.config/modules-load.d/etk-ramoops.conf /storage/.config/modprobe.d/etk-ramoops.conf
        fi
    " || { echo "[ETK] ARMING FAILED — nothing depends on a partial arm; re-run after fixing."; exit 1; }
    cat <<'EOF'
[ETK] Armed. NEXT STEPS (operator, on-device):
  1. Reboot the rig on-device (never remotely).
  2. Run: scripts/arm_blackbox.sh --verify   (expect panic=10 in live cmdline)
  With the certified config, a kernel panic now: auto-reboots in 10s ->
  Sentry synthesizes the PANIC ledger row -> blackbox_d's kmsg flight log
  holds the lead-up evidence from the dying boot.
EOF
}

case "${1:-}" in
    --verify) verify ;;
    --revert) revert ;;
    --ramoops) ARM_TOKEN="$TOKEN_RAMOOPS"; WITH_RAMOOPS=1; arm ;;
    *) ARM_TOKEN="$TOKEN"; WITH_RAMOOPS=0; arm ;;
esac
