# ROCKNIX NIGHTLY MIGRATION CLOSEOUT
**Subject:** What actually happened migrating ETK from nightly-20260516/17 → nightly-20260520
**Target hardware:** Retroid Pocket Flip 2 (SM8250 / Snapdragon 865 / Adreno 650)
**Driver:** MESA Turnip 26.1.0 (build-ID drifted, version string unchanged)
**Migration date:** 2026-05-24
**Parent docs:** `RocknixNightlyMigrationDossier.md` (plan), `ADDENDUM_install_sh_tiered_backup.md` (deferred)

---

## §0. RESULT

Migration **successful**. Rig moved via Rocknix's in-place updater (operator protocol — no clean reflash). Landing nightly is **20260520**, not the 20260524 the planning dossier optimistically scoped to (oldest nightly still available at flash time). All ETK functionality restored. Headless mode (R3 panic) fully revalidated across suspend/resume. One workstream pinned for next session (`install.sh` Tier-B backup upgrade) and one new issue discovered (MangoHUD native BATT module reads wrong on the new nightly).

---

## §1. WHAT LANDED (REPO DIFF)

| File | Change | Why |
|---|---|---|
| `bin/gamepad_probe.py` (new) | Appendix-A probe utility | Empirical evdev code capture; auto-deploys via existing install.sh Step 3 |
| `bin/etk_pitstop.py` | `find_gamepad()` rewrite: `PAD_HINTS` + `PAD_EXCLUDE` + numeric sort | DS5 target exposes a node cluster; lexical sort + broad hint landed on Touchpad |
| `bin/etk_pitstop.py` | `BTN_CONFIRM=304` / `BTN_BACK=305` (was 305/304) | DS5 standard PS mapping (Cross=confirm, Circle=back) — pre-swap held, probe verified |
| `bin/etk_pitstop.py` | 7 UI hint strings: A↔B swapped | Retroid Pocket Flip 2 uses Nintendo-style labels (A=right, B=bottom); labels now match physical button under DS5 |
| `bin/etk_pitstop.py` | `find_gamepad()` matches logged to `etk_pitstop.log` | Silent wrong-node fallback becomes one-grep diagnosis |
| `bin/input_d.py` | Same `find_gamepad()` rewrite + stdout logging | R3 panic vector must use the same cluster-aware lookup |
| `README.md` | OS line → 20260520; `RPSC3`→`RPCS3`; `GT3P`→`GT5P` | Version sync + stale typos |
| `AI_MANIFEST.md` | OS line → 20260520 | Version sync |
| `scripts/env.sh` | Thermal comment → `nightly-20260520` | Thermal re-cal note |

---

## §2. WHAT WASN'T IN THE PLANNING DOSSIER (SURPRISES)

### 2.1 DS5 exposes a *cluster* of input nodes, not a single device
The dossier §3.1 anticipated a name change (Xbox → DualSense). The actual situation was more aggressive: InputPlumber's DS5 target presents **four sibling event nodes** sharing the "Sony Interactive Entertainment DualSense Wireless Controller" name with a sub-device suffix —
- `event8` = buttons / sticks / d-pad (the one we need)
- `event9` = Motion Sensors (gyro/accel)
- `event10` = Touchpad
- `event11` = Headset Jack

Plus a separate `event7 = InputPlumber Keyboard` for keyboard emulation. The initial `PAD_HINTS` substring match worked, but Python's `sorted(os.listdir(...))` does **lexical** order, so `event10 < event2 < event8`. We bound to event10 (Touchpad), which advertises BTN_TOOL_FINGER instead of BTN_SOUTH → gamepad actions silently dead. Fix: numeric-sort by event-number suffix + `PAD_EXCLUDE` filter on `("touchpad", "motion sensor", "headset", "battery", "keyboard")`.

### 2.2 The "no Mesa bump = vault carries forward" claim was wrong
Dossier §6 asserted: *"No mesa/turnip line appears anywhere in 20260516→20260524. Turnip shader cache is driver-version-keyed, so an unchanged Turnip = your vault carries forward, no re-harvest needed."*

False premise. Turnip's cache is **build-ID keyed**, not version-string keyed. Rocknix rebuilt Mesa between nightlies even though the version string stayed `26.1.0`, producing a different `libvulkan_freedreno.so` build ID, which shifts the SHA1 over Mesa's cache-key tuple → every old cache entry misses. Pre-existing `~49,913 shaders` for GT5P were dead weight; the first GT5P session post-migration harvested `~6,500 new` against an "empty" cache (the old files still on disk, just unreachable by Mesa's new keys).

Operator response: archived the pre-migration vault, cleared shaders on rig + host, starting a fresh post-migration harvest for a clean baseline.

**Lesson for the next migration:** treat every nightly that touches *any* Mesa-adjacent package as a partial re-harvest. The shader vault is layered, not version-locked. Worth an eventual stale-key sweep tool when vault size becomes inconvenient.

### 2.3 RPCS3 binds devices by name + index suffix, not bare name
Dossier didn't anticipate the RPCS3 input config breaking — but it did. The pre-migration `Player 1.Device: InputPlumber GameController 1` no longer resolved post-migration. SDL now finds the pad via HIDAPI as `DualSense Wireless Controller` (different VID:PID surface), and RPCS3 logs `Adding empty device` + `Pad 0: ... config=` (empty) → zero input.

False starts:
1. Renaming to `DualSense Wireless Controller` (bare) → still empty
2. Theorized "HIDAPI vs evdev conflict" (Theory B) — prepared but not needed

Actual fix: `DualSense Wireless Controller 1` (with trailing space-1). The " 1" in the pre-migration name was never an InputPlumber convention — it was **RPCS3's SDL handler's controller-index suffix**, stored verbatim in the YAML. SDL's enumeration log prints the bare name; RPCS3's lookup expects the indexed name. Survives reboot.

Edit target: `/storage/.config/rpcs3/input_configs/global/Default.yml` line 3. NOT in the ETK repo. Backup saved on the rig at `Default.yml.bak.1779645253`.

### 2.4 HOME button was a victim of §2.3, not a separate problem
Initial symptom (Rocknix overlay no longer opens on HOME press) looked like the dossier §3.5 "Rocknix DS5 config reset" prediction. Real cause: RPCS3 had no device bound at all (§2.3), so neither the `Guide` button branch nor the `Back+Start` combo could fire. Resolved automatically when the device name was corrected.

### 2.5 A latent stale symlink contaminated the vault
`/storage/.cache/rpcs3/cache/NPUA80075 → /storage/games-internal/roms/etk/vault` (mtime May 5, predates current ETK arch). Pointed RPCS3's per-game PPU/SPU cache into the **vault root**, not the per-game vault sub-path. Two consequences:
- Clearing the vault collaterally wiped GT5P's PPU/SPU cache → unexpected PPU recompile
- New PPU recompile output landed in vault root as `ppu-*-EBOOT.BIN` / `ppu-*-EMAIN.SELF` sibling dirs

Repair: removed symlink, made real `cache/NPUA80075/` directory, relocated the two PPU dirs back to the canonical path (preserved recompile work, no re-recompile needed). NOT in the ETK repo — rig-side state only. Other games (BCUS98114, NPEA00502, etc.) didn't have this symlink, so the contamination was scoped to NPUA80075. Treat as a one-off historical artifact; not worth a sweep in install.sh.

---

## §3. THE OPERATOR'S "PRODUCTIVE CRASHING" UX ARGUMENT (PROTOCOL NOTE)

The migration is over but a recurring theme worth recording: the ETK rig is **deliberately abused** to harvest shaders, and the user-facing experience needs to convey that crashing is productive. Post-migration the rig harvests aggressively into a clean vault, and the periodic vault purge is part of the ETK dev cycle for re-testing this phase of UX/UI. The HUD's `NEW` counter is the moment-to-moment signal that even a crashed session banked work.

GT5P harvest sequence the operator follows (worth a future first-class doc):
1. Track time trials → pit-crew animations harvest
2. Re-enter another level → track camera pans harvest
3. Tracks saturated → dealership for all cars
4. Back to tracks for single-race camera pans with cars driving (combinatorial shader explosion)

The session ledger captures the operation; periodic shader purge + fresh harvest is how the operator stress-tests the rig's productive-crashing UX.

---

## §4. PINNED FOR NEXT SESSION

### 4.1 `install.sh` Tier-B backup upgrade
Per `ADDENDUM_install_sh_tiered_backup.md`. Scope unchanged. Now that the migration is closed and rig state is verified working, Tier-B becomes routine insurance for the next pad-cluster surprise. Estimated 30-45 min focused. Confirmed decisions:
- Workstream order: implement against the live rig (T1/T2/T5 tests pass first, then ship)
- `./state/` host dir + `.gitignore` entry + §F privacy lock
- Stale `--update` rsync comments in install.sh cleaned up in the same diff
- Parent dossier §13 softened to point at `--restore-state`

### 4.2 MangoHUD native BATT module broken on 20260520
**Symptoms:** reads ~50% too high, plugged-in/charging icon stale (doesn't match Rocknix front-end battery indicator).

**Hypotheses to triage:**
1. MangoHUD package itself needs a nightly bump (most likely — MangoHUD 0.8.3 final landed 20260516; if upstream addressed a Linux battery-reading API change, the bundled version is stale)
2. ETK's MangoHud.conf `battery` key syntax changed and our config now reads a stale path
3. System-level change in Rocknix's power-supply sysfs paths that MangoHUD scrapes

**Operator workaround already applied locally:** edited `MangoHud.conf` to hide BATT, show GPU + frametime graph instead. Worth holding the local-edit + investigation as a unit — the operator may want the GPU/frametime instruments to stay even after BATT is fixed.

### 4.3 GT5P shader harvest sequence write-up
Operator wants to document the systematic harvest protocol (see §3). First-class doc, candidate location: `dossiers/Gt5pHarvestProtocol.md` or in `README.md` as a new section.

---

## §5. ONE-LINERS TO REMEMBER FOR THE NEXT MIGRATION

1. **Sort event nodes numerically.** Lexical sort puts `event10` before `event2`. Whenever a node is being chosen by enumeration, the index suffix is a number, not a string.
2. **Bare names lie. Index suffixes are real.** RPCS3 stores SDL device names as `<name> N`. SDL's enumeration log prints the bare name. The YAML stores the indexed name. The two are not the same string.
3. **Turnip cache validity is build-ID, not version.** A version string staying at `26.1.0` does not mean the vault carries forward. Mesa rebuilds invalidate the cache silently. Treat every Mesa-touching nightly as partial re-harvest.
4. **Probe-first, edit-second.** The "may revert to standard" caveat in dossier §3.4 saved the face-button swap from being wrong: pre-swap was confirmed by `gamepad_probe.py` on event8 before being trusted. The probe is now permanent at `bin/gamepad_probe.py`; deploy + use it every time.
5. **The headless gate is suspend/resume + R3.** Cold-start re-bind isn't enough. InputPlumber tears down and re-creates the node across power-button suspend, and the panic vector has to survive that. Verified 2026-05-24 against the cluster fix.
6. **Operator does in-place updates only.** Never recommend a clean reflash. `install.sh` is the always-run repair/sync. `--restore-state` (when Tier-B lands) is a card-death contingency, not part of normal migration flow.

---

## §6. FILE-LEVEL CHANGE SUMMARY (FOR THE COMMIT)

```
bin/gamepad_probe.py     +56  (new)
bin/etk_pitstop.py       ~70  find_gamepad cluster fix, BTN code flip, 7 UI label swaps, _log
bin/input_d.py           ~30  find_gamepad cluster fix + stdout logging
README.md                 ±3  version 20260517→20260520, typos
AI_MANIFEST.md            ±1  version 20260517→20260520
scripts/env.sh            ±2  thermal comment 20260516→20260520
dossiers/RocknixNightlyMigrationCloseout.md  (new)  this file
TO_DO.md                  +N  next-session items
```

Not in the diff (rig-side only, preserved by `Default.yml.bak.*`):
- RPCS3 `Default.yml` device rebound to `DualSense Wireless Controller 1`
- `cache/NPUA80075` stale symlink repaired to real directory

---
