# CHECKLIST: Fastboot Verification & Clean Revert (Windows) — SM8250 Flip 2

**For:** a Claude Code session on **Windows**, driving the Retroid Pocket Flip 2 (SM8250) over USB.
**Goal (Phase 0, the gate):** prove `fastboot` enumerates with the official Retroid data cable. **Everything else in the internal-storage plan waits on this.**
**Then (Phase 1, optional, DESTRUCTIVE):** clean-revert the internal install so the rig boots the SD card natively again.
**Status:** verification not yet achieved — blocked 2026-06-01 on a suspected charge-only cable; retrying with the official Retroid cable on Windows.

> **fastboot is USB-only.** No WiFi, no `SM8250.local`, no SSH. The device must be in fastboot mode and connected by a **data** cable.

---

## 0. WHY THIS DEVICE IS SPECIAL (read first)

- The Flip 2 uses **U-Boot**, not ABL. The boot menu lists `Boot / Reset / … / mmc0 / scsi0–5` and an **"Enable fastboot mode"** entry. There is **NO "Uninstall ROCKNIX"** option (that's ABL-only). Fastboot is the *only* clean way back.
- Internal storage is **UFS = `/dev/sda`**. The SD card is `mmcblk0`.
- The internal install added two partitions: **`ROCKNIX`** (`sda24`, ~2 GB, fat32 boot) and **`STORAGE`** (`sda25`, ~6.3 GB, ext4). There is a **label collision** — both internal and SD carry `LABEL=ROCKNIX`/`STORAGE`, and internal (scsi/UFS) wins boot. Erasing the internal **ROCKNIX** boot partition is what makes the device fall back to booting the SD.
- **Do NOT touch `userdata` or any other Android partition.** Only `ROCKNIX` / `STORAGE` are in scope.

---

## 1. WINDOWS PREREQUISITES

1. Install **platform-tools** (gives `fastboot.exe`): https://developer.android.com/tools/releases/platform-tools — unzip, note the folder (e.g. `C:\platform-tools`).
2. Driver: in fastboot mode the device appears as an **Android Bootloader Interface**. If Windows shows it as an unknown device, install the **Google USB Driver** (or a universal ADB/fastboot driver) via Device Manager → the device → *Update driver* → point at the driver folder.
3. Open **PowerShell** in the platform-tools folder (or add it to PATH). Verify the binary: `fastboot --version`.

---

## 2. ENTER FASTBOOT & VERIFY (the actual Phase 0 gate — read-only, SAFE)

1. Power the rig fully off.
2. Boot into the **U-Boot menu** (hold Volume-Down while powering on — same entry used for the loader/bootloader menu).
3. Select **"Enable fastboot mode"**. Screen should indicate fastboot/waiting.
4. Connect the **official Retroid data cable** to the Windows PC.
5. Run:
   ```
   fastboot devices
   ```
   - **A serial + `fastboot` listed → SUCCESS. Phase 0 is GREEN.** The cable + drivers work.
   - **Empty output →** cable is still charge-only OR driver missing. Re-seat, try another USB port (prefer a rear USB 2.0 port), confirm the driver in Device Manager. This was the 2026-06-01 failure mode.
6. Read-only inspection (confirms 2-way comms; lists vars/partitions; changes nothing):
   ```
   fastboot getvar all
   ```
   Capture the output. Look for whether `ROCKNIX` / `STORAGE` partitions are visible to fastboot and any `is-userspace` / `is-logical` hints.

**STOP HERE and report results if the task was only "test fastboot."** Section 3 is destructive and should be a deliberate, separate decision.

---

## 3. CLEAN REVERT (DESTRUCTIVE — only on explicit go-ahead)

> Reverts the internal install so the rig boots the SD natively. Wipes the internal ROCKNIX/STORAGE *contents*. The internal `etk-internal` vault/game data is sacrificed but is re-syncable from the host via `install.sh` (host `.bak` holds nightly shaders). Confirm with the operator before proceeding.

1. Confirm the partition names are exactly as fastboot reports them (from `getvar all`). Only proceed if `ROCKNIX` and `STORAGE` are present and unambiguous.
2. Erase the internal boot partition first (this alone breaks the internal-boot precedence):
   ```
   fastboot erase ROCKNIX
   ```
3. Erase the internal storage partition:
   ```
   fastboot erase STORAGE
   ```
4. Reboot:
   ```
   fastboot reboot
   ```
5. The device should now boot **the SD card** (native boot-card layout). Verify in ROCKNIX: full game carousel, ETK present.

**Caveats / fallbacks:**
- `fastboot erase` wipes *contents*, not the GPT entry — so the ~8 GB isn't fully reclaimed to Android yet, but the internal-boot split-brain is broken and the SD boots. Full space reclamation (delete partition + grow userdata) is a separate, later step — do NOT attempt blind.
- If `erase` reports "partition not found," the names differ in fastboot's view — re-check `getvar all`; do **not** guess at partition numbers or use `fastboot delete-logical-partition` without confirming the layout first.
- If the device won't boot at all after this, it can still re-enter the U-Boot menu (Volume-Down) → boot `mmc0` to force the SD kernel. Don't panic-flash anything.

---

## 4. REPORT BACK (so the macOS/main session can continue the plan)

Record: (a) did `fastboot devices` list the rig? (b) `getvar all` output (esp. partition visibility); (c) whether the revert was performed and the post-reboot boot source; (d) carousel/ETK state after reboot.

This unblocks Phase 1→3 of `dossiers/InternalStorageManagerDossier.md` (clean revert → reinstall with a large STORAGE → ETK internal-storage manager for SD-free play).
