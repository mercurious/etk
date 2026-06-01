# ROCKNIX NIGHTLY MIGRATION DOSSIER
**Subject:** Migrating the ETK from nightly-20260516/17 → nightly-20260524
**Target hardware:** Retroid Pocket Flip 2 (SM8250 / Snapdragon 865 / Adreno 650, a6xx)
**Driver of record:** MESA Turnip 26.1.0
**Author note:** This dossier cross-references the attached Rocknix changelog (20260516 → 20260524) against the live ETK tree. Treat every "VERIFY" step as load-bearing — guesses about the new gamepad layout will brick the controls.

---

## §0. VERDICT (READ THIS FIRST)

1. **Migrate now, to nightly-20260524.** You are on 20260516 (labeled 20260517 by the build quirk), and it is currently the *oldest* of the five downloadable nightlies. The next nightly to land drops 20260516 off the list, leaving you with no re-flashable known-good image. Migrate before that window closes.
2. **The headline break is unavoidable and already predicted in your own code:** Rocknix switched the InputPlumber virtual target from **Xbox → DualSense (DS5)** in **20260520** (`inputplumber: use target ds5`). Every available target build (20–24) carries this change. There is no DS5-free build left in the window.
3. **Good news up front:** there is **no Mesa/Turnip bump** anywhere in 20260516→20260524. Your shader vault is expected to remain valid — no forced re-harvest from a driver-version standpoint. (Confirm the adapter string post-flash; see §6.)
4. **Most ETK code is insulated.** The break surface is narrow and concentrated in the gamepad layer, plus a short list of "verify, don't assume" items below.

---

## §1. CHANGELOG TRIAGE (what matters vs. what to ignore)

### 1.1 CRITICAL to the ETK
| Build | Change | Impact |
|---|---|---|
| 20260520 | `inputplumber: use target ds5` | **Virtual pad is now DS5, not Xbox.** Breaks `find_gamepad()` name match + swaps face-button codes. See §3. |
| 20260520 | `config: reset configs for DS5 changes` | Native Rocknix hotkey/button profiles reset. Affects README-documented binds (HOME, SELECT, force-quit combo). See §3.5. |
| 20260520 | `remove inputplumber refresh service` / `sm8650: restart inputplumber when resuming` | InputPlumber lifecycle changed; device node re-created on resume. `input_d.py` self-heal loop already tolerates this *if* it can re-find the DS5 node. See §3.2. |
| 20260522 | `linux (drm/msm): fix hw resource deallocation on mode-(disable/set/enable)` | Touches the MSM display/DPU path — the exact subsystem your Adreno/DPU **crash signatures** scrape from dmesg. Risk of signature drift. See §4. |
| 20260524 | `emulationstation: bump package` | ES is what scrapes `/storage/.config/modules/` and renders the Pitstop Tools entry. Risk to app registration. See §5. |
| 20260520 | `sm8250: install rocknix abl` | SM8250 **boot chain** touched. Flashing/recovery caution. See §7. |

### 1.2 RELEVANT to SM8250, verify in passing
| Build | Change | Impact |
|---|---|---|
| 20260520 (×4) | `sm8250: fix thor lite audio`, `restore audio patch`, `fix headphones`, `drop thor lite wireplumber quirk`; 20260516 `SM8250: fix RP5 audio` | SM8250-family audio stack churned repeatedly. RPCS3 Cubeb backend output should be re-verified. See §8. |
| 20260516 | `mangohud bump v0.8.3 final` (already on your build) | No further mango version bump 20→24. Your HUD config keys are stable across this jump. Spot-check only. See §9. |
| 20260524 | `libXft: install libs into sysroot (needed for xterm)` | Font-lib plumbing. Verify the LiberationMono path the HUD pins and `foot` monospace still resolve. See §9. |
| 20260522 | `RTW88 - bump to git HEAD`; 20260520 `Update VPN services to wait for network connectivity` | WiFi driver bump. Your whole dev loop (SSH, `install.sh`) rides WiFi. Re-confirm `RIG_IP`/SSH. See §10. |

### 1.3 NOT relevant — explicitly safe to ignore
- **fex-emu / armv9 / cortex-x3-x4** (`fix crash on armv9`, `override TUNE_CPU for cortex-x3/4`, `build without SVE`, `use armv9a`). SM8250 is **ARMv8.2-A (Cortex-A77/A55)**, *not* ARMv9, and has no X3/X4 cores. fex-emu is x86→ARM for Steam/Proton; RPCS3 is native ARM64. **Zero impact.**
- **Steam / Proton-CachyOS / Heroic** (`Disable shaders in Steam launch`, gamescope refactors, `SM6115 remove steam/heroic`). Not the RPCS3 path. The "disable shaders in Steam launch" is Steam's own fossilize cache, unrelated to your Turnip/Mesa vault. **Ignore.**
- **SM8650 / SM8750 suspend-to-RAM churn**, **RK3576 USB charging**, **RGDS panel**, **Mangmi Air X regulator**, **S922X mangohud/kernel**, **RG DS LEDs**, **Venus node patch** (virtio-GPU, not bare-metal Turnip), **dual-screen HDMI/DP UI** (only matters if you dock the Flip 2 to an external display). **Not SM8250 / not RPCS3.**

---

## §2. THE PAD STATUS FIELD IS YOUR FIRST DIAGNOSTIC

The Pitstop already surfaces a gamepad debug field — the meta line renders `PAD: OK (/dev/input/eventN)` or `PAD: NO PAD (...)` from `find_gamepad()` + the open result in `etk_pitstop.py::main()`. **This field is the canary.** On first boot of the migrated build, open Pitstop and read it:

- `NO PAD` → `find_gamepad()` failed *and* the `/dev/input/event9` fallback didn't open. Name match is broken (expected — see §3.1).
- `OK (/dev/input/event9)` → it fell back. It may be pointing at the **wrong** node. Treat as suspect until the probe (§3.3) confirms event9 is actually the DS5.
- `OK (/dev/input/eventX)` where X is found by name → only possible if the DS5 name happens to contain one of the matched substrings, which today it does **not**.

The status field tells you *whether* a device opened; it does **not** tell you *which codes it emits*. That requires the probe in §3.3 — this is the "additional probing" the migration demands.

---

## §3. [CRITICAL] INPUTPLUMBER DS5 MIGRATION

Your own code called this shot. `etk_pitstop.py` H1 block: *"The upcoming PS-pad Rocknix nightly (Xbox → PlayStation virtual gamepad via InputPlumber)…"* and the constants carry an explicit *"may become 304/305 post-nightly… Swap these two constants when that nightly is installed."* **That nightly is now installed.**

### 3.1 Break #1 — device discovery (TWO files, NOT one-place)
Both `bin/etk_pitstop.py::find_gamepad()` and `bin/input_d.py::find_gamepad()` match `if "xbox" in name`. The DS5 virtual target reports a Sony/DualSense name — **not** "xbox" — so both functions silently fall through to the hardcoded `/dev/input/event9` fallback.

The H1 comment claims gamepad changes are "a one-place update." **That is only true for the button *codes*.** The *name match* lives in the function body of **two** files and is **not** in the hoisted constants block. Both must be fixed.

### 3.2 Break #2 — resume re-binding
`input_d.py`'s self-heal `while True: find_gamepad()` loop is the only persistence vector for the **R3 panic button** on the headless rig. With InputPlumber now restarting/recreating the node on resume (20260520), the loop *will* re-run — but it re-runs `find_gamepad()`, which still looks for "xbox." **Until §3.1 is fixed, R3 recovery is dead after the first resume.** This is load-bearing; prioritize it.

### 3.3 PROBE PROCEDURE (do this before editing anything)
From a `foot` terminal on the rig (or over SSH):

```sh
# 1. What is the DS5 virtual target actually called now?
for n in /sys/class/input/event*/device/name; do printf '%s: ' "$n"; cat "$n"; done

# 2. Capture the real button codes empirically (BusyBox has no evtest):
python3 /storage/gamepad_probe.py /dev/input/eventN   # eventN from step 1
```
(`gamepad_probe.py` is in Appendix A — scp it to `/storage/` first.)

Press, in order, and record `(type, code, val)`:
- **Confirm (Cross ✕)** and **Back (Circle ○)** → confirms the 304/305 swap.
- **L1 / R1** → should stay `EV_KEY 310 / 311` (tab switch). Verify.
- **L3 / R3** → should stay `EV_KEY 317 / 318`. Verify (R3 is the panic button).
- **Select/Create** → should stay `EV_KEY 314` (the `input_d.py` D-pad clutch). Verify.
- **D-pad** → should stay `EV_ABS 16 / 17`, val `±1`. Verify.

### 3.4 EXPECTED EDITS (apply only after the probe confirms each value)

**A. Harden `find_gamepad()` (apply to BOTH `etk_pitstop.py` and `input_d.py`).** Don't just swap "xbox"→"dualsense" — Rocknix has now flipped the target twice and will do it again. Match a set, future-proof it:

```python
def find_gamepad():
    """Locate the InputPlumber virtual controller, pad-model-agnostic.
    Rocknix flips the virtual target between models across nightlies
    (Xbox -> DS5 in 20260520); match any known virtual-pad signature so a
    future target swap never strands us on the event9 fallback again.
    UPDATE THE HINT LIST with the EXACT name from `cat .../device/name`."""
    PAD_HINTS = ("xbox", "dualsense", "dual sense", "playstation",
                 "sony", "ds5", "wireless controller", "inputplumber")
    input_dir = '/sys/class/input/'
    try:
        for entry in sorted(os.listdir(input_dir)):
            if entry.startswith('event'):
                name_path = os.path.join(input_dir, entry, 'device/name')
                if os.path.exists(name_path):
                    with open(name_path, 'r') as f:
                        name = f.read().strip().lower()
                    if any(h in name for h in PAD_HINTS):
                        return f"/dev/input/{entry}"
    except Exception:
        pass
    return '/dev/input/event9'
```

**B. Swap the face-button constants in `etk_pitstop.py` (GAMEPAD CODES block).** The DS5 mapping is standard PlayStation: Cross = confirm = `BTN_SOUTH` (304), Circle = back = `BTN_EAST` (305). This *reverts* the on-device Xbox-target swap your code was carrying:

```python
# Pre-20260520 (Xbox virtual target, on-device verified): SWAPPED
#   BTN_CONFIRM = 305   # BTN_EAST
#   BTN_BACK    = 304   # BTN_SOUTH
# 20260520+ (DS5 virtual target): standard PlayStation mapping
BTN_CONFIRM = 304     # BTN_SOUTH = Cross (X)  = confirm
BTN_BACK    = 305     # BTN_EAST  = Circle (O) = back
```
`_wait_for_dismiss`, `handle_tuning_pad`, `handle_telemetry_pad`, and `handle_tools_pad` all read these constants, so this single edit fixes A/B everywhere in Pitstop — *that* part of the "one-place" promise holds.

> **CAVEAT — do not commit the swap on faith.** InputPlumber virtual targets do not always follow the physical controller's convention (that is *exactly why* your Xbox target was non-standard). If the §3.3 probe shows confirm still on 305, **leave the constants alone.** Probe first, edit second.

**C. `input_d.py` inline codes (R3=318, L3=317, SELECT=314, D-pad=16).** These are *non-face* buttons; their evdev codes are stable across Xbox and DS5, so **no change is expected** — but the probe must confirm, because if any moved, the panic button moves with it. (Long-term: hoist these into a shared constants module imported by both files so the next pad swap really is one place. See §11.)

### 3.5 Native Rocknix binds + README docs
`config: reset configs for DS5 changes` resets Rocknix's own controller profile. Re-verify the README-documented native binds, which the ETK does **not** own but the user relies on:
- `HOME` = RPCS3 menu
- `SELECT` = GT5P camera toggle
- `START + SELECT + R1` = native force-quit

If Rocknix remapped the hotkey button under DS5, update the **Custom ETK Gamepad Specifications** section of `README.md` to match. Also reconcile the two stale version strings while you're in there (README says OS `20260518`, manifest says `20260517`, you call it `20260516`) — standardize on `20260524`.

---

## §4. CRASH-SIGNATURE DRIFT (drm/msm patch, 20260522)

`session_postmortem.sh` + `config/crash_signatures.json` classify GPU faults by dmesg string match:
- `a6xx_irq.*gpu fault`
- `msm_dpu.*hangcheck recover`
- `drm:recover_worker.*offending task.*rpcs3`

The 20260522 `drm/msm: hw resource deallocation on mode-(disable/set/enable)` patch lives in precisely this subsystem. A reworded fault/hangcheck line would cause **Adreno/DPU crashes to misclassify** (silent RECOVERY:Silent instead of RECOVERY:Adreno, eroding your tuning signal). The userspace twin (`^F .*VK_ERROR_DEVICE_LOST` from RPCS3.log) is unaffected — RPCS3's own log strings don't change with a kernel patch.

**VERIFY:** after migration, provoke or wait for one known GPU fault, then on the rig:
```sh
dmesg | strings | grep -iE "adreno|a6xx|msm_dpu|fence|hangcheck|recover_worker"
```
Confirm the live strings still match the three patterns. If they drifted, update **both** `config/crash_signatures.json` *and* the inline-mirrored patterns in `session_postmortem.sh` (the file warns: BusyBox can't parse JSON, so the patterns are duplicated — keep them in sync).

---

## §5. EMULATIONSTATION BUMP (20260524) — Tools-menu app registration

`emulationstation: bump package` touches the component that (a) scrapes `/storage/.config/modules/`, (b) regenerates `gamelist.xml` on boot, and (c) renders your `<game>` entry + SVG icon and spawns it via `foot %ROM%`. Your registration path (`etk_modules_inject.py`, the Sentry tripwire, the byte-mode regex that tolerates non-UTF-8 / a raw `&` in touchHLE's desc) is built around the *current* ES's exact behavior.

**Risks:** ES could change the gamelist regeneration format, the modules-wipe timing the Sentry tripwire depends on, or how `<image>` paths are rewritten.

**VERIFY (post-flash, post-reboot):**
```sh
grep -c '>ETK Pitstop<' /storage/.config/modules/gamelist.xml   # expect >= 1
ls -l /storage/.config/modules/etk_pitstop.sh /storage/.config/modules/etk_pitstop.svg
cat /storage/etk_tripwire.log                                   # empty == clean boot
```
Then confirm the polished entry (name + icon) actually renders in the Tools carousel and launches into `foot`. If ES changed the wipe timing, the Sentry tripwire is your safety net — but confirm it's firing (the tripwire log will show re-injection lines if it had to).

---

## §6. SHADER VAULT VALIDITY (the reassuring part)

No `mesa`/`turnip` line appears anywhere in 20260516→20260524. Turnip shader cache is driver-version-keyed, so an unchanged Turnip = **your vault carries forward, no re-harvest needed.**

**VERIFY** the adapter/driver didn't move under you regardless (a kernel/package bump could pull a new Mesa without a headline line):
```sh
# Confirm Turnip version + adapter still match the template's pin:
#   Video: Vulkan: Adapter: Turnip Adreno (TM) 650   (config/etk_template.yml)
strings /storage/.cache/rpcs3/RPCS3.log | grep -iE "turnip|adreno|vulkan" | head
```
If Turnip moved off 26.1.0, expect a one-time recompile per game — which the ETK absorbs gracefully (vault_d just banks the new shaders). Update the pinned version string in `README.md` / `AI_MANIFEST.md` if so.

---

## §7. SM8250 BOOT CHAIN — `sm8250: install rocknix abl` (20260520)

This range modifies the SM8250 Android Boot Loader. Implication for *how* you migrate:

- **Prefer a clean image flash to the dev SD card** over an in-place updater pass for this jump, because a boot-chain change is the category most likely to misbehave on an in-place update. Your project philosophy already treats the dev card as disposable (README: "use a dev card you don't mind reflashing").
- After flashing, expect the **first boot to be slow** (ABL + first-run init + any PPU/SPU recompile). The README's ice-pack advice applies — keep it cool through the first heavy recompile.
- Keep the original 20260516 image archived locally as your rollback, since it's about to vanish from the download mirror.

---

## §8. SM8250 AUDIO CHURN

Five SM8250-family audio commits across 20260516–20260520 (`fix RP5 audio`, `fix thor lite audio`, `restore audio patch`, `fix headphones`, `drop thor lite wireplumber quirk`). The Flip 2 shares the SM8250 audio stack and your template runs `Audio: Renderer: Cubeb`. RPCS3 audio could regress (silence, crackle, wrong device) under the reshuffled WirePlumber/audio routing.

**VERIFY:** launch a known-good game, confirm Cubeb output. If broken, the Pitstop **TUNING** tab already exposes `Audio Renderer Backend` (Cubeb/ALSA/FAudio) — try ALSA as a fallback without leaving the device. (Note: README Phase 8 marks audio support "experimental" already; treat any audio regression as low-severity for the harvest mission.)

---

## §9. MANGOHUD / FONTS / libXft

- **MangoHud:** already at v0.8.3 (20260516); no further bump 20→24. Your `config/MangoHud.conf` keys (`custom_text`, `exec`, `exec_interval`, `font_file`) are stable across this jump. Spot-check the HUD renders the `live_stat.txt` string post-migration; no change expected.
- **Fonts (libXft, 20260524):** the change targets xterm, but it's a reminder to confirm your two pinned font paths survive the flash:
  - HUD: `font_file=/usr/share/fonts/liberation/LiberationMono-Regular.ttf`
  - Pitstop terminal: `foot -F -o font="monospace:size=28"` (from `config/etk_pitstop.sh`)
  ```sh
  ls -l /usr/share/fonts/liberation/LiberationMono-Regular.ttf   # HUD font still present?
  fc-match monospace                                             # foot's monospace still resolves?
  ```
  If LiberationMono moved/vanished, MangoHud falls back to a default and the DDU may misalign; repin the path.

---

## §10. WiFi / SSH DEV LOOP (RTW88 bump, 20260522)

WiFi driver bumped to git HEAD. Your entire host-side toolchain (`install.sh`, `commander.sh`) depends on SSH to `RIG_SSH=root@<SOC>.local` (mDNS) or a literal LAN IP. Rare but possible: DHCP lease/MAC behavior shifts, IP changes.

**VERIFY:** `START > Network Settings > IP ADDRESS` on the rig, reconcile against `RIG_SSH` in `etk.conf` (mDNS hostname is preferred since it survives network changes). If you use a VPN, note `Update VPN services to wait for network connectivity` (20260520) may change first-boot timing.

---

## §11. ENHANCEMENTS / ADJUSTMENTS (beyond fixing the break)

1. **Truly centralize the pad layer.** The H1 "one-place" claim is half-true. Hoist `find_gamepad()` *and* all evdev codes (face buttons, L1/R1, L3/R3, SELECT, D-pad axes) into one tiny shared module (e.g. `bin/etk_pad.py`) imported by both `etk_pitstop.py` and `input_d.py`. Next pad swap becomes a genuine one-file edit. Rocknix has now flipped the target twice — assume a third.
2. **Promote `gamepad_probe.py` to a first-class TOOLS-tab action** (or a Pitstop debug mode toggled by a key) so future pad migrations are diagnosable on-device with no scp. This is the "gamepad debug field" upgrade your prompt gestures at: status → *raw event* visibility.
3. **Make `find_gamepad()` log the chosen device + name** to `etk_pitstop.log` / stdout so the meta-line "OK (eventN)" is backed by the actual device name in the log — turns a silent wrong-node fallback into a one-line diagnosis.
4. **Re-validate thermal calibration** (see §12) — kernel/DT churn in this range warrants a fresh `etk_probe.sh` pass rather than trusting the 20260516 numbers.
5. **Close the backup gap** (see §13) — `install.sh` safeguards *only the vault*. Telemetry ledgers, custom_configs, and installed PS3 games are unprotected against the reflash.

---

## §12. THERMAL RE-VALIDATION

`env.sh` carries `# Recalibrating to Rocknix nightly-20260516 changed thermals` with `ALARM_TEMP=83 / PIT_THRESHOLD=65 / RACE_THRESHOLD=86`, and `thermal_d.sh` + `commander.sh` read `thermal_zone14` as the governing core sensor. Thermal zones are device-tree defined and unlikely to renumber from a drm patch, but the project's own practice is to recalibrate per-nightly. The 20260516 commit `sm8550/sm8650: fix thermal zone names` is a standing reminder that Rocknix *does* renumber zones — just not for SM8250 this round.

**VERIFY:** run a probe pass on the migrated build before trusting the thresholds:
```sh
/storage/games-internal/roms/etk/scripts/etk_probe.sh start migrate_20260524
# ... run a heavy harvest session ...
/storage/games-internal/roms/etk/scripts/etk_probe.sh stop
/storage/games-internal/roms/etk/scripts/etk_probe.sh report
```
Confirm `thermal_zone14` still tracks the prime-core/CPU hot spot (probe labels it `cpu7_bot`) and that zones 1/5/6/10/14/15/19/24/25 still map as labeled in `etk_probe.sh`. Re-tune `ALARM_TEMP`/`RACE_THRESHOLD` if the new build's idle/load curve shifted, and bump the recalibration comment to `20260524`.

---

## §13. PRE-FLIGHT CHECKLIST

**SUPERSEDED 2026-05-26 by `ADDENDUM_install_sh_tiered_backup.md`.** `install.sh`
now captures Tier-B state (telemetry ledgers, tuned RPCS3 configs, RPCS3 user
profile — saves, trophies, `.rap` licenses, last-played ID) into `./state/` on
**every** run, alongside the existing `./vault/` shader pull. The old manual
rsync block here is no longer needed.

Game blobs (`bios/rpcs3/dev_hdd0/game/`) and `.psn` launchers remain Tier-C
(operator decision): re-install from `.pkg`/`.rap` via the TOOLS tab is the
accepted recovery path. **Precondition:** keep `.pkg`/`.rap` originals in your
off-device library — the TOOLS installer deletes staged files from
`$PKG_STAGING_DIR` on success.

**Before flashing:**
1. `cd ~/etk && ./install.sh` once against the *current* rig — pulls the
   freshest shaders into `./vault/` AND the freshest Tier-B state into
   `./state/`.
2. Archive the outgoing OS image locally (e.g. the **20260516** image before it
   leaves the Rocknix mirror — it's your only rollback).
3. Confirm host has the incoming image verified against its checksum.

**After flashing a fresh rig:**
- `./install.sh --restore-state` once — overwrite-pushes `./state/` back to the
  rig so telemetry / configs / saves return to the tuned-up baseline. This is
  the ONLY supported way to consume the Tier-B backup; never run it as part of
  a routine update (it overwrites live rig-side state). See `§7` for the
  scenarios that actually force a reflash and `ADDENDUM_install_sh_tiered_backup.md`
  for the full restore semantics.

---

## §14. POST-MIGRATION VALIDATION CHECKLIST (in order)

1. **Boot + ABL:** device boots clean off the dev card; first boot may be slow (§7).
2. **`./install.sh`** from host → re-pushes ETK + vault, re-registers the Tools entry, re-arms the Sentry. Watch for `SENTRY_OK` and `[OK] Launcher verified`.
3. **Tools menu:** ETK Pitstop appears with icon + name (§5). Reboot first — ES reads the Tools gamelist at startup, not on "Update Gamelists."
4. **Pad status field:** open Pitstop, read the `PAD:` line (§2). If `NO PAD` or suspect node → run the probe (§3.3).
5. **Probe the DS5:** capture real codes, apply §3.4 edits A/B/C *only as the probe dictates*, re-`install.sh`.
6. **R3 panic button:** launch a game, press R3, confirm `recovery.sh` fires and the Sentry tears down workers + reseeds HUD. Then **suspend/resume and press R3 again** (§3.2) — this is the resume-rebind test.
7. **L3 shift, L1/R1 tabs, A/B/D-pad** in Pitstop — all respond correctly.
8. **HUD:** `live_stat.txt` renders in MangoHud; font aligned (§9).
9. **Crash signatures:** provoke/observe one GPU fault, confirm dmesg strings still match (§4); confirm a TELEMETRY row classifies correctly.
10. **Audio:** Cubeb output works, or fall back via TUNING tab (§8).
11. **Thermal:** run `etk_probe.sh`, re-validate zone 14 + thresholds (§12).
12. **Vault:** confirm "NEW" counter increments during harvest (cache→vault symlink intact); adapter string still Turnip 26.1.0 (§6).
13. **Re-install games** from your off-device `.pkg`/`.rap` library if you did a clean flash; restore Tier-B state (custom_configs + telemetry + RPCS3 user profile) with `./install.sh --restore-state` (see §13 and `ADDENDUM_install_sh_tiered_backup.md`).
14. **Docs:** bump version strings to 20260524 across `README.md` / `AI_MANIFEST.md` / `env.sh` comment; update gamepad spec if native binds moved.

---

## Appendix A — `gamepad_probe.py`

Drop on the rig (`/storage/gamepad_probe.py`), run from a `foot` terminal or SSH. Reuses the ETK's `'llHHi'` evdev wire format. Ctrl-C to quit.

```python
#!/usr/bin/env python3
# gamepad_probe.py - empirical evdev code capture for pad migrations.
#   python3 /storage/gamepad_probe.py [/dev/input/eventN]
# Lists input device names, then prints (type, code, val) for each press.
import os, struct, sys

FMT = 'llHHi'
SZ = struct.calcsize(FMT)
TYPES = {1: 'EV_KEY', 3: 'EV_ABS', 0: 'EV_SYN'}

def list_names():
    base = '/sys/class/input/'
    for e in sorted(os.listdir(base)):
        if e.startswith('event'):
            p = os.path.join(base, e, 'device/name')
            try:
                with open(p) as f:
                    print(f"  {e}: {f.read().strip()}")
            except Exception:
                pass

print("=== input devices ===")
list_names()
dev = sys.argv[1] if len(sys.argv) > 1 else '/dev/input/event9'
print(f"\nReading {dev} - press each button. Ctrl-C to quit.\n")
try:
    with open(dev, 'rb') as f:
        while True:
            d = f.read(SZ)
            if len(d) != SZ:
                continue
            _, _, et, code, val = struct.unpack(FMT, d)
            if et in (1, 3):  # skip EV_SYN noise
                print(f"{TYPES.get(et, et):7} code={code:<4} val={val}")
except KeyboardInterrupt:
    print("\nbye")
except (FileNotFoundError, PermissionError) as e:
    print(f"open failed: {e}  (try a different eventN from the list above)")
```

**Reference (Linux input-event-codes.h — what you're hoping to confirm):**

| Button | Const | Code | ETK use |
|---|---|---|---|
| Cross / A | `BTN_SOUTH` | 304 | confirm (expected on DS5) |
| Circle / B | `BTN_EAST` | 305 | back (expected on DS5) |
| L1 / LB | `BTN_TL` | 310 | tab prev |
| R1 / RB | `BTN_TR` | 311 | tab next |
| Select / Create | `BTN_SELECT` | 314 | `input_d.py` D-pad clutch |
| L3 | `BTN_THUMBL` | 317 | SHIFT (PIT/RACE) |
| R3 | `BTN_THUMBR` | 318 | **PANIC / recovery** |
| D-pad X | `ABS_HAT0X` | 16 | horizontal (val ±1) |
| D-pad Y | `ABS_HAT0Y` | 17 | vertical (val ±1) |

Face buttons (304/305) are the migration risk; everything ≥310 is expected stable. **Trust the probe, not the table.**

---

## Appendix B — One-line summary for the commit message

> Migrate ETK to Rocknix nightly-20260524. Headline: InputPlumber Xbox→DS5 target (20260520) breaks `find_gamepad()` name match in pitstop+shifter and likely reverts the on-device face-button swap to standard (confirm=BTN_SOUTH 304). Verify-and-patch per probe. Secondary watch items: drm/msm crash-signature drift, ES-bump Tools registration, SM8250 ABL/audio churn, thermal re-cal. Vault carries forward (no Turnip bump).
