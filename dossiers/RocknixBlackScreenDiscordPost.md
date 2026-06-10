# New bug-report post (paste-ready, ≤2000 chars for Discord) — black screen after in-place update on SM8250

> The prior thread "Boot to black screen with sound" was closed Unreproducible. This leads with a
> deterministic on-device check so it can't be dismissed as user mods. Tested on Flip2 (SM8250);
> same root cause applies to RP5 (same `grub.cfg`). Fuller version (for a GitHub issue) lives in
> dossiers/RocknixEFIBootloaderStaleBugReport.md. Body below is 1911 chars.

---

**Black screen after in-place 2025→2026 update on SM8250 — root cause + in-place fix (no reinstall)**

Reproduced/root-caused on a Flip2 (SM8250). Same as the closed "black screen with sound" thread (RP5 is also SM8250, same `grub.cfg`).

**Symptom:** after an in-place official→official update the panel stays black after GRUB. System is fully up (LEDs, SSH, audio, ES-DE) — only the display is dark. Standby off/on lights it. Factory reset / compositor restart don't help.

**Cause:** the updater refreshes `/flash/boot/grub/grub.cfg` but NOT the config actually booted — `/flash/EFI/BOOT/grub.cfg` (via `bootaa64.efi`). So the new kernel runs the *old* cmdline+dtb (`clk_ignore_unused … fbcon=rotate:3` instead of `video=efifb:off`). With EFI fb still on, the DSI link clock fails to lock on first power-on → black panel; suspend/resume re-runs it → the standby workaround.

**Why "unreproducible":** a fresh install or nightly card writes `/flash/EFI/BOOT/` correctly, so devs never see it — only in-place upgrades from an old official hit it. That's also why "fresh install fixes it" and why it happens "with any theme." Not the theme/mods.

**Verify (objective, no reinstall):**
```
cat /proc/cmdline
md5sum /flash/EFI/BOOT/grub.cfg /usr/share/bootloader/boot/grub/grub.cfg
```
Bug = no `video=efifb:off` and two different hashes.

**Fix in place:**
```
mount -o remount,rw /flash
cp /flash/EFI/BOOT/grub.cfg /flash/EFI/BOOT/grub.cfg.bak
cp /usr/share/bootloader/boot/grub/grub.cfg /flash/EFI/BOOT/grub.cfg
sync && mount -o remount,ro /flash && reboot
```
After: `/proc/cmdline` shows `video=efifb:off`, panel inits at cold boot, no standby trick. Confirmed on hardware.

**Suggested fix:** updater should also sync `/flash/EFI/BOOT/grub.cfg` (and point its `devicetree` lines at `/boot/grub/*.dtb`). Likely same class as the SM8550 `LinuxLoader.cfg` caveat. Full diff/dmesg on request.
