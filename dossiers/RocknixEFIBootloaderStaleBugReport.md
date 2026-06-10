# Bug report: in-place official→official update leaves `/flash/EFI/BOOT/grub.cfg` stale → black screen at boot (false "brick") on SM8250 / Retroid Pocket Flip2

> Draft for filing at https://github.com/ROCKNIX/distribution/issues — review before posting.
> Search the tracker + Discord first to avoid duplicating an existing report.

## Summary

After an **in-place official → official** update on a Retroid Pocket Flip2 (SM8250, EFI/GRUB boot), the device boots to a **black panel** after the GRUB stage. The system is otherwise fully up — joystick LEDs on, SSH reachable, ES‑DE running — but the display never lights, so users reasonably conclude the device is **bricked**. (This is generating "ROCKNIX bricked my device" reports; it is a *false* brick — the OS is running fine.)

**Root cause:** the in-place updater refreshes `/flash/boot/grub/grub.cfg` (and the dtbs under `/flash/boot/grub/`) to the new build, but does **not** sync the bootloader config the device actually boots from — `/flash/EFI/BOOT/grub.cfg` (loaded by `bootaa64.efi`). That file is left at the version from the original install. The result is the **new kernel booting with a year-old kernel cmdline and a year-old device tree**, which breaks DSI panel bring-up at cold boot.

**Fix (in place, no reflash):** copy the build's source-of-truth config over the stale EFI one:
```sh
mount -o remount,rw /flash
cp /flash/EFI/BOOT/grub.cfg /flash/EFI/BOOT/grub.cfg.bak
cp /usr/share/bootloader/boot/grub/grub.cfg /flash/EFI/BOOT/grub.cfg
sync && mount -o remount,ro /flash
reboot
```

## Affected

- **Device:** Retroid Pocket Flip2 (`HW_DEVICE=SM8250`), boots via EFI (`/flash/EFI/BOOT/bootaa64.efi` → `/flash/EFI/BOOT/grub.cfg`).
- **Build:** `OS_VERSION=20260601`, kernel `7.0.2`, `OS_BUILD=official`, `BUILD_DATE=Mon Jun 1 09:13:48 UTC 2026`.
- **Trigger:** in-place update from an older official to a newer official (long gap). Likely affects other EFI-boot Snapdragon devices defined in the same `grub.cfg` (rp5, rpmini, rpminiv2, ayn-thorlite).
- **NOT affected:** cards taken nightly→…→official, and any fresh flash — a clean install writes `/flash/EFI/BOOT/` correctly. This is why the issue is largely invisible to anyone riding nightlies.

## Symptoms

- GRUB stage shows; then the panel stays **black** through the normal ROCKNIX boot splash and into ES‑DE.
- Device is alive: controller LEDs lit, `ssh root@<host>` works, `emulationstation` running.
- A **suspend/resume cycle (power button → suspend → power button → wake)** brings the panel up — and it then stays up until the next cold boot.
- **Factory reset and `systemctl restart essway` do NOT help** (the stale config is on the boot partition, untouched by either).

## Evidence

**Booted cmdline is stale (old params, missing `video=efifb:off`):**
```
# BEFORE fix — /proc/cmdline
… console=tty0 clk_ignore_unused pd_ignore_unused fbcon=rotate:3
# AFTER copying the correct EFI grub.cfg — /proc/cmdline
… console=tty0 video=efifb:off
```

**The active EFI config diverges from the build source; `/flash/boot/grub/` matches the build:**
```
84935183dc16a6b9f0535722d217a130  /flash/EFI/BOOT/grub.cfg            # STALE (what boots)
8cefa2337b0739a6e19223d9af0b01a5  /flash/boot/grub/grub.cfg           # current (updated, but NOT the boot path)
8cefa2337b0739a6e19223d9af0b01a5  /usr/share/bootloader/boot/grub/grub.cfg   # build source-of-truth
```

**The stale EFI config also points at the stale dtb** (root of `/flash`, not the updated `/boot/grub/` copy):
```
# stale EFI grub.cfg:   devicetree /sm8250-retroidpocket-flip2.dtb        → 182392 bytes (old, May 2025)
# build grub.cfg:       devicetree /boot/grub/sm8250-retroidpocket-flip2.dtb → 183462 bytes (current)
```

**Kernel log at the failing cold boot** (DSI link clock fails to lock → panel DCS init times out):
```
WARNING: drivers/clk/qcom/clk-rcg2.c:136 at update_config+…   (RCG update timeout)
  dsi_link_clk_set_rate_6g
  msm_dsi_host_power_on
  dsi_mgr_bridge_pre_enable
  …
dsi_err_worker: status=4
panel-ch13726a-amoled ae94000.dsi.0: sending DCS EXIT_SLEEP_MODE failed: -110
panel-ch13726a-amoled ae94000.dsi.0: Failed to initialize panel: -110
```

**After the fix, on a clean cold boot:** `dsi_link_clk_set_rate_6g`, `dsi_err_worker`, and "Failed to initialize panel" counts are all **0**, the ROCKNIX splash appears normally, and no suspend is needed. Operator-confirmed on hardware.

## Why the old cmdline breaks the new kernel

The new build deliberately switched the Snapdragon cmdline to `video=efifb:off` and dropped `clk_ignore_unused pd_ignore_unused fbcon=rotate:3`. Booting the new kernel with the old params (EFI fb left enabled) appears to let the EFI framebuffer collide with the new MSM/DPU display bring-up, so the DSI link clock fails to lock on first power-on. Combined with the year-old dtb, the panel never initializes at cold boot; a suspend/resume re-runs the sequence and limps it through.

## Suggested fix in the updater

The in-place update step that writes `/flash/boot/grub/` should also sync the **active EFI boot path** `/flash/EFI/BOOT/grub.cfg` (and ensure its `devicetree` entries reference the updated `/boot/grub/*.dtb`, not the stale `/flash`-root copies). Equivalent in spirit to the existing SM8550 caveat that `LinuxLoader.cfg` isn't updated in place — but here it's cleanly fixable without forcing users into a fresh install.

## Open questions for maintainers

1. Is `/flash/EFI/BOOT/` intentionally excluded from the update sync, or an oversight? (`/flash/boot/grub/` *is* synced.)
2. Does this affect all EFI-boot SM8250 devices in this `grub.cfg`, or is the Flip2 panel just the most sensitive to the cmdline/dtb delta?
3. Is this the same underlying mechanism as the documented SM8550 `LinuxLoader.cfg` "needs a fresh install" caveat? If so, both could be fixed by syncing the active loader config in place.

## Reproduction

1. Install an older official ROCKNIX on a Flip2 SD card.
2. Update in place to a much newer official (e.g. 20260601).
3. Reboot → black panel after GRUB; device otherwise boots (SSH/LED confirm).
4. Confirm `/proc/cmdline` still shows the old params and `md5sum /flash/EFI/BOOT/grub.cfg` differs from `/usr/share/bootloader/boot/grub/grub.cfg`.
5. Apply the fix above → cold boot shows the panel normally.
