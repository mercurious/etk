# CONCLUSION: Fastboot Is Not Usable on the Flip 2 — Clean Revert Requires Repartition Rebuild

## ⚠️ ADDENDUM 2026-06-08 — VERDICT OVERTURNED: the Qualcomm ABL fastboot WORKS

The "fastboot is dead" verdict below was about the **U-Boot "Enable fastboot mode"** entry *only*. The **Qualcomm ABL fastboot** — reached via **Volume-Down → firmware menu** (the same screen used to boot Android for the battery recal, see [[project_rig_boot_sequence]]) — was **never tested in the 2026-06-02 session**, and it **works**:

- From a **Mac** (`fastboot 37.0.0`, Homebrew, no driver install): `fastboot devices` → **`1bb6a94	fastboot`**. Enumerates cleanly.
- `fastboot getvar all`: `unlocked:yes`, `secure:no`, `product:kona`, `current-slot:a`, and **`ROCKNIX` (0x80000000 = 2 GB) + `STORAGE` (0x1A1D00000 ≈ 7 GB) visible by name** — exactly the §3 erase targets, non-slotted.

**Consequence:** the §3 clean revert (`fastboot erase ROCKNIX` + `erase STORAGE` + `reboot`) is **REACHABLE**. `InternalStorageManagerDossier` is no longer gated on a destructive repartition rebuild — the cheap erase path is live.

**Agreed sequence (2026-06-08):** run `/storage/etk-internal/ROLLBACK.sh` in ROCKNIX **first** (de-internalize data → SD, strip the duplicate `games.yml` lines for NPUA80075/NPEA90002), THEN VolDown → ABL fastboot → `erase ROCKNIX`+`STORAGE` to break the `LABEL=` collision so the SD boots native. The erase only breaks the split-brain; reclaiming the ~9 GB GPT space for Android is a **separate later step** (delete partition + grow `userdata`, do NOT do blind).

> The body below is the (now-superseded) U-Boot-only conclusion. Kept for the root-cause analysis, but the headline verdict is **overturned**.

---

**Date:** 2026-06-02
**Device:** Retroid Pocket Flip 2 (SM8250), ROCKNIX (kernel 7.0.2 aarch64), SoC `retroidpocket,rpflip2 / qcom,sm8250`
**Host:** Windows 11 PC, `fastboot` 37.0.0 (Android platform-tools, installed at `C:\Users\dutch\platform-tools`)
**Supersedes the open question in:** `dossiers/FastbootRevertChecklist.md` (Phase 0 gate)
**Verdict:** **Phase 0 (prove `fastboot` enumerates) is RED and cannot be made green on this firmware by any non-typing method.** The clean `fastboot erase ROCKNIX/STORAGE` revert path is **dead**. The only remaining clean route to native SD boot is a **destructive repartition / reflash rebuild — explicitly deferred (not being done now).**

---

## 1. WHAT WAS PROVEN

This session ran fastboot verification exhaustively from a Windows host (the prior 2026-06-01 attempt was on a Mac across two USB-C cables). Every easy cause was ruled out:

| Attempt | Result | Meaning |
|---|---|---|
| MTP/FILE_TRANSFER over the official Retroid cable | ✅ Enumerated as `USB\VID_1D6B&PID_0104` (Linux multifunction gadget), Status OK | **Cable + port + host data path all good — the 2026-06-01 "charge-only cable" theory is DISPROVEN.** |
| U-Boot menu → "Enable fastboot mode" → Power | ❌ Screen blanks; **zero** USB enumeration at host | Power *is* a working select button (it also selects "Drop to shell"), so the entry is being chosen — yet no fastboot USB gadget appears. |
| Unplug/replug cable while in the blank "fastboot" state | ❌ No device arrival of any class over 45 s | A live USB peripheral always enumerates *something* on attach (even driverless = yellow-bang). Nothing ⇒ no gadget is running. |
| U-Boot shell `fastboot usb 0` | ❌ Not possible | Handheld has no keyboard; the single USB-C port can't host a keyboard and be a fastboot gadget at the same time. |
| Alternate select buttons (A / Start / D-pad) | ❌ Only **Vol Up/Down + Power** do anything in the U-Boot menu | No other button is even a candidate. |
| `systemctl reboot --reboot-argument=bootloader` | ❌ Ignored — booted straight back to ROCKNIX | No qcom reboot-mode/nvmem driver present to translate a reboot reason into a bootloader cookie. |
| BCB: wrote `bootonce-bootloader` to `misc` (`sda15`), rebooted | ❌ Ignored — booted back to ROCKNIX; BCB string left unconsumed | This bootloader does not read the Android Bootloader Control Block. |

## 2. ROOT CAUSE

**U-Boot's "Enable fastboot mode" does not bring up a working USB fastboot gadget on this firmware.** The most likely mechanism is that the menu entry invokes `fastboot` without first running `usb start` (so the USB controller/PHY is never initialized in that path), but it is unfixable here regardless of cause: the fix would require typing at the U-Boot prompt, and the device cannot accept keyboard input in that state.

Both ways to *auto*-enter fastboot without typing — a reboot-reason cookie and the BCB `misc` message — are **ignored by this boot chain**, so we cannot even force U-Boot down a (possibly USB-initializing) auto-fastboot path.

## 3. CONFIRMED SYSTEM FACTS (captured this session, root SSH)

- **Partition layout (`/dev/disk/by-partlabel/`, internal UFS = `/dev/sda`):**
  `ROCKNIX → sda24`, `STORAGE → sda25`, `misc → sda15`, `frp → sda13`, `userdata → sda23`, `super → sda21`, plus standard Qualcomm partitions (`xbl_a/b` on sdb/sdc, modem on sdf, etc.). SD card = `mmcblk0`.
- **Boot chain:** `grub_portable`. Kernel cmdline: `BOOT_IMAGE=/KERNEL boot=LABEL=ROCKNIX disk=LABEL=STORAGE grub_portable quiet rootwait console=tty0 video=efifb:off`. Internal (UFS/scsi) wins the `LABEL=` resolution over the SD — the split-brain described in `InstallToInternalRecovery.md` is confirmed.
- **`reboot` → `systemctl`** (systemd 255). **No `fw_setenv`/`fw_printenv`, no `/etc/fw_env.config`** — U-Boot env cannot be poked from Linux.
- **No qcom reboot-mode / IMEM / PON reboot-reason driver** visible under `/sys/devices/platform`.

## 4. THE ONLY REMAINING CLEAN-REVERT PATHS (both destructive)

The goal behind "prove fastboot" was always the clean revert: break internal-boot precedence so the rig boots the SD natively (and, eventually, reclaim the ~8 GB). With fastboot dead, the routes are:

1. **Destructive repartition / reflash rebuild — CHOSEN, DEFERRED.** Rebuild via the internal-install tooling or a full Android reflash (EDL 9008 if needed), wiping/rewriting the internal `ROCKNIX`/`STORAGE` partitions. This is the accepted answer but is **not being executed now** (operator decision, 2026-06-02).
2. **Root-SSH revert from Linux (not pursued).** We have root on the live rig, so the internal boot partition could be broken from the OS itself — `wipefs`/relabel/`dd` on `sda24`/`sda25` to defeat the `LABEL=` collision so the SD wins. This needs no fastboot, but is **equally destructive** and was deliberately **not attempted** this session. Recorded only as a known alternative.

## 5. SIDE EFFECTS / STATE LEFT BEHIND

- **None on the rig.** `misc` was backed up (full 1 MB) to `/storage/misc.bak.20260602_143950` and to the host (`%USERPROFILE%\etk_misc_backups\`). The BCB write was fully reverted — post-restore md5 of `misc` matches the pre-write backup (`d9f7f705c134690d91e98fd7daa1b8b2`). Command field is back to all-zeros.
- **Host is now fastboot-capable** (platform-tools installed). If a future ROCKNIX/U-Boot build fixes the fastboot gadget, the host side is already done — re-run `FastbootRevertChecklist.md` §2 only.

## 6. CROSS-REFERENCES

- `dossiers/FastbootRevertChecklist.md` — the procedure this conclusion closes out (Phase 0 + the now-unreachable §3 erase steps).
- `dossiers/InternalStorageManagerDossier.md` — Phase 0 was its **blocking** prerequisite; that plan is now gated on the repartition rebuild rather than fastboot.
- `dossiers/InstallToInternalRecovery.md` — the label-collision / internal-wins-boot background, confirmed here.
