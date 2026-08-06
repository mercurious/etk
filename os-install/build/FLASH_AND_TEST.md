# ROCKNIX-GTK Edition — Tier-I image: flash & cold-boot test

## CURRENT: v3 distributable (standard labels)
**Artifact:** `os-install/ROCKNIX-GTK-SM8250.aarch64-20260707.img.gz` — sha `a14b4df3fd11348112704fd85263c6f0c44e2c3f261bf6af57b548a0dcaccc28`
**Build:** `docker exec etk-imgtool bash /work/build/build_gtk_image_v2.sh` (defaults now = standard labels)

v3 = v2 with **standard `ROCKNIX`/`STORAGE` labels** so `install.sh` STEP 6.4 (which hardcodes
those) doesn't clobber it. **For single-card users this cures the split-brain** (only one card).
The `+x` (203/EXEC) fix is baked. Everything else = v2 below.

**v2-on-UFS-rig test result (2026-07-07):** RESIZE VALIDATED — the card grew 256 MiB → **53.8 GB**
on first boot; `install.sh` deployed ETK cleanly onto it. Then `install.sh` STEP 6.4 rewrote the
card's unique-label grub → the post-install reboot cross-wired to the UFS rig. That's why v3 uses
standard labels. **Do NOT run host `install.sh` on a v3 card while a second ROCKNIX (your UFS) is
present** — standard labels collide; test v3's full boot on a rig without a competing ROCKNIX, or
just trust the v2 resize + install.sh-deploy proof. Single-card end users have neither problem.

**Next (hostless):** `dossiers/HostlessInstallVision_20260707.md` — retire host `install.sh`;
on-device Pitstop self-fetch via the `/flash/mount-storage.sh` init hook.

## v4 — HOSTLESS ACTIVATION image — `…-v4.img.gz` (sha `509d2a06…`)
The payoff: the `/flash/mount-storage.sh` hook now **installs ETK on boot 2** — no host, no
install.sh. Unique labels (`ROCKNIX-GTK`/`GTKSTOR`) isolate it on the UFS rig; **do NOT run
install.sh on it.**
Build: `HOOK_SCRIPT=/work/build/mount-storage.sh SEED_CONFIG=/work/build/seed_config BOOT_LABEL=ROCKNIX-GTK STOR_LABEL=GTKSTOR OUT_IMG=/work/ROCKNIX-GTK-SM8250.aarch64-20260707-v4.img docker exec … build_gtk_image_v2.sh`.

How it works: `$ETK_ROOT/.seed_config` (the ETK `.config` — services + `.wants` + Sentry +
`02-etk-coredump.sh` + bind scripts + profile.d) lives *outside* `/storage/.config`, so fs-resize
still runs. On **boot 1** the hook stays out of the way → fs-resize grows STORAGE + reboots. On
**boot 2** (marker gone, ETK not yet active) the hook `cp -a`s `.seed_config` → `/storage/.config`
*before* systemd starts → the ETK services come up → Sentry injects Pitstop.

**Flash → boot → expect the one resize auto-reboot → boot 2 should come up with ETK LIVE.** Verify:
- **ETK Pitstop in the Tools carousel** (the headline — this is what was missing on v3).
- `ssh root@<card-ip>` (pw `rocknix`): `df -h /storage` full card; `systemctl is-active etk.service
  etk-rpcs3.service etk-turnip.service` → active; `cat /storage/.etk_hook_log` → boot 1
  `resize=PRESENT`, boot 2 `ACTIVATED ETK .config`; `cat /storage/rpcs3/loaded` → custom.
- If ETK isn't up, send `/storage/.etk_hook_log` + `systemctl --failed` — the hook is fail-silent
  so the log is the trail.

### v3 (P0 marker probe) — VALIDATED 2026-07-07 (superseded by v4)
v3's minimal hook proved the mechanism GREEN: booted twice, resized to 53.8 GB, `.etk_hook_log`
showed boot1 `resize=PRESENT` / boot2 `resize=GONE`, `.etk_hook_boot2` present. That cleared the
one real risk (the mount replica) so v4 layers the real activation on top.

**Safety:** the hook replaces the storage mount for EVERY grub entry (it's init-level, not
per-kernel), so there's no hook-free boot on this card — but the mount line is byte-identical to
stock, so the only failure mode is a stock mount failure. If the card doesn't reach ES, re-flash
(disposable). Report `.etk_hook_log` either way — it's the forensic trail.

---
## v2 detail (resize mechanics, still current)
v2/v3 fix the three v1 first-boot defects
(`dossiers/ImageLaneFirstBootForensics_20260707.md`):

- **Resizes to fill ANY card.** v1 baked `/storage/.config`, which tripped ROCKNIX
  `fs-resize`'s "already initialised" guard (STORAGE stuck at 512 MB). v2 bakes **no
  `.config`** → fs-resize runs and grows STORAGE to the whole card.
- **Unique labels** — boot=`ROCKNIX-GTK`, storage=`GTKSTOR` — so it's split-brain-safe on
  your UFS rig (no `ROCKNIX`/`STORAGE` collision) **and** survives fs-resize's UUID/serial
  randomization (v1's UUID-pin would have broken after the resize).
- **`chmod +x` on the seeded scripts** (v1's `audio_watchdog.sh` was non-`+x` → 203/EXEC).
- **ETK is STAGED, activated by `install.sh`.** Framework + the GTK RPCS3/Turnip forks are on
  the card (in `$ETK_ROOT` + `/storage/rpcs3` + `/storage/turnip`), but **not active out of the
  box** — there's no ROCKNIX first-boot hook outside `.config`, and `.config` blocks the resize.
  You activate ETK by running `./install.sh` from the host (matches the "config ETK with
  install.sh" plan). Out of the box you get: **GTK kernel default + branded grub + staged ETK**.
- SYSTEM squashfs still byte-identical to stock (update-friendly).

### Flash (host)
Balena Etcher (reads `.img.gz`) or ImageBurner → a **spare** SD card. Verify sha first:
`shasum -a 256 os-install/ROCKNIX-GTK-SM8250.aarch64-20260707-v2.img.gz` → `07b4c68b…`.

### Cold-boot test (you drive — I never reboot the rig)
1. **Insert the spare card**, power into **Loader**. U-Boot scans mmc first → the card's GRUB →
   `ROCKNIX-GTK for Flip 2` (default, 2 s). If UFS boots instead, use the ABL/U-Boot device
   pick — the card can't cross-wire either way (unique labels).
2. **Expect ONE automatic reboot on first boot.** fs-resize shows a "Resizing partition… /
   Rebooting in 5s" spinner, then reboots — this is normal ROCKNIX first-boot behavior, now that
   the resize works. The second boot comes up with STORAGE grown to the full card.
3. **Validate (kill-or-green for v2):**
   - Second boot reaches EmulationStation from the card.
   - `ssh root@<card-ip>` (default pw `rocknix` until paired): `df -h /storage` → **grew to full
     card** (was 512 MB → now ~59 GB); `uname -a` (GTK kernel); `cat /proc/cmdline` →
     `disk=LABEL=GTKSTOR … msm.context_keepalive=1 panic=30`; `blkid | grep GTKSTOR` (label kept,
     UUID randomized by resize — expected).
   - ETK staged: `ls /storage/games-internal/roms/etk` (framework), `/storage/rpcs3/rpcs3-sa.custom`
     (78 MB), `/storage/turnip/drivers/…gtk_0.4-defaulton.so`. **Pitstop is NOT in Tools yet — expected.**
4. **Activate ETK:** enable SSH in ROCKNIX settings, then from the repo: `./install.sh`. This
   wires the services, binds the forks, injects Pitstop, applies mako, etc. — the full GTK stack.
5. **Return to normal:** power off, remove the card, boot → back to your UFS rig, untouched.

### What v2 validates vs v1
v1 proved: repack pipeline + GTK boot + UUID split-brain guard + ETK-active. v2 proves: **the
resize** + label-based split-brain guard + the staged-ETK/install.sh flow. Together they cover the
whole lane.

## Follow-ups
- **Full auto-active + resize** needs a rig-native first-boot self-installer (deferred
  `project_rig_native_installer`) — the only way to install `.config` *after* fs-resize.
- `install.sh` STEP 6.8 (`etk-stage3`) references the hand-pushed `02-etk-coredump.sh` (not in
  repo) — promote it to the repo, or the same 127 will hit users who run install.sh.
- Distributable can drop the "spare/UFS" caveats; the unique-label + resize design is already the
  portable one.
