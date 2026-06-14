# Workstream Dossier — Syncthing-Fork (Android) Config Recovery over adb

**Goal:** re-establish Android ⇄ Mac-hub ⇄ Rocknix save-sync **without using the
Syncthing-Fork Android UI** (or with the absolute minimum taps), by injecting
config over `adb`. Triggered by: UFS redesign → Android boot/userdata destroyed →
Syncthing-Fork setup lost.

**Status:** Mac-hub + Rocknix nodes captured (this doc). Android node pending an
`adb` probe (operator to boot Android + connect USB). Drafted 2026-06-13.

---

## 0. RECOVERED TOPOLOGY (source of truth — captured live 2026-06-13)

Star topology — the **always-on Mac hub** is the center; Rocknix and Android each
peer only with the hub (they do **not** peer with each other).

| Node | Device ID | Folder `x6hh6-yt9u5` path | Status |
|---|---|---|---|
| **Mac hub** "Daves-MacBook-Air" | `4IC6HMJ-EITZYEI-CVYXD66-GIZX4SF-M77AA5M-WIQC26E-R75MKHG-WRTVIAT` | `/Users/dave/etk-saves` | ✅ intact, knows all 3 |
| **Rocknix** "ROCKNIX" | `7G37QUB-W4RWECV-MYKQFF3-XYDFIMA-JR7EFRQ-FS4WYJQ-7K7D3JD-G737PQJ` | `/storage/.config/rpcs3/dev_hdd0/home/00000001/savedata` | ✅ intact, syncthing running |
| **Android (aPS3e)** "aPS3e-Flip2" | `QVMDKH7-OFDCZRP-72A3KTX-L32M5TN-VOBJT7J-IIMWPWH-ERBRVZZ-BF6TKQZ` ← **LOST** | `/sdcard/Android/data/aenu.aps3e/files/aps3e/config/dev_hdd0/home/00000001/savedata` | ❌ wiped; new ID incoming |

- Folder: id `x6hh6-yt9u5`, label **"PS3 Saves"**, type **sendreceive**,
  `ignorePerms="true"`, shared with all three on the hub.
- Mac-hub GUI/API: `127.0.0.1:8384` (apikey in the hub's `config.xml`).
- Rocknix syncthing config lives at
  `/storage/games-external/.config/syncthing/config.xml` (started by
  `start_syncthing.sh`).
- **Cruft aside:** both the Mac and Rocknix `config.xml` carry an empty
  `<folder id="" path="">` + empty `<device id="">` stub — harmless leftover from
  a past botched share; optional cleanup (§8), not part of this recovery.

---

## 0a. SOURCE-OF-TRUTH ORDERING (NON-NEGOTIABLE)

**ROCKNIX is the current source of truth for savedata.** Android must be brought
up **Receive Only**, pull the Rocknix-sourced state down through the hub, be
**verified identical to Rocknix**, and only *then* flipped to **Send & Receive**.
Never bring Android up as Send & Receive first — a fresh or stale Android folder
(e.g. the 2026-06-12 restored saves, now older than Rocknix) could push old/empty
data to the hub and clobber the good Rocknix saves.

Sequence (gates every branch in §4):
1. **Confirm the Mac hub holds Rocknix's truth** — Rocknix → hub shows *Up to
   Date* (Rocknix syncthing is running; the hub is the relay and must mirror it
   before Android touches it).
2. Bring up the Android folder `x6hh6-yt9u5` as **`type="receiveonly"`**.
3. Android pulls the full savedata from the hub. Any pre-existing/stale Android
   saves become **"receive-only changed"** items → **revert to the hub version**
   (Rocknix wins). Receive-Only never pushes local changes up, so the hub/Rocknix
   truth is protected throughout.
4. **Verify** Android savedata == Rocknix savedata (file list + sizes, §6).
5. **Only now flip Android → `sendreceive`.**

This ordering also directly serves the operator's goal: identical savedata on both
nodes = a clean A/B of the ETK **aPS3e (Android)** tune vs the **Rocknix** rig on
the *same* game state. The flip is the last action, after the A/B baseline is set.

---

## 1. THE HARD CONSTRAINT (why this isn't just "push the old file back")

The 2026-06-12 backup (`~/aps3e_android_backup_20260612/`) deliberately **did not
capture the Syncthing app config or cert** ("NOT BACKED UP … re-pair manually").
`installtointernal` wiped userdata, so:
- Syncthing-Fork's `cert.pem`/`key.pem` are gone → **the old device ID
  `QVMDKH7…` cannot be recreated** (the ID is the cert's fingerprint).
- The Android node will have a **brand-new device ID** whichever way we restore it.
- Therefore the **Mac hub must be updated** to trust the new Android ID (replace
  the `QVMDKH7…` device entry). Rocknix needs **no change** (star topology — it
  never peered with Android directly).

The whole game is: mint/learn the new Android identity, wire it to the hub, and
point its folder at the aPS3e savedata path — with as little app UI as possible.
**Whether we can do it 100% headlessly hinges on ROOT** (the syncthing config
lives in the app-private `/data/data/…` dir). The probe (Phase B) decides.

---

## 2. PHASE A — get the rig on adb (operator)

1. Boot into Android (the wiped/fresh install).
2. Settings → About → tap **Build number** 7× to unlock Developer options (a
   wipe resets this).
3. Settings → System → Developer options → enable **USB debugging**.
4. Connect the Flip 2 to the Mac by USB; tap **Allow** on the "Allow USB
   debugging?" prompt (check "always").
5. Tell me when ready — I confirm with `adb devices` (should list one device,
   not `unauthorized`).

---

## 3. PHASE B — probe Android (I run these once adb is up)

```bash
adb devices                                   # authorized?
adb shell getprop ro.build.version.release     # Android version (Android 11+ = /Android/data restricted)
# Root?  (decides headless vs minimal-UI)
adb shell 'su -c id' 2>&1 ; adb root 2>&1      # su present? userdebug?
# Is Syncthing-Fork installed, and did any config survive?
adb shell pm list packages | grep -iE 'catfriend|syncthing'
adb shell 'su -c "ls -la /data/data/com.github.catfriend1.syncthingandroid/files/" ' 2>&1   # config.xml/cert.pem?
# aPS3e present + savedata path reachable (the folder target)?
adb shell ls -la /sdcard/Android/data/aenu.aps3e/files/aps3e/config/dev_hdd0/home/00000001/savedata 2>&1
# App uid (for chown if we inject) + SELinux
adb shell 'su -c "stat -c %u /data/data/com.github.catfriend1.syncthingandroid"' 2>&1
adb shell getenforce
```

Decision inputs: **(a)** app + config survived? **(b)** rooted? **(c)** savedata
path present? **(d)** can Syncthing-Fork even reach `/Android/data/aenu.aps3e`
(MANAGE_EXTERNAL_STORAGE) — see §7 risk.

---

## 4. PHASE C — recovery, by branch

### C1 — app data SURVIVED (config.xml + cert present, ID still `QVMDKH7…`)
Best case (boot was broken but userdata intact). Then nothing on the hub changes.
- If rooted: edit the surviving `config.xml` only if the folder path moved
  (post-UFS) — set the `x6hh6-yt9u5` `path` to the current aPS3e savedata dir;
  ensure the Mac-hub `<device>` + folder share are present; restart the app.
- Verify (§6). Likely a 30-second fix.

### C2 — ROOTED + fresh identity → **fully headless, zero app UI** (the dream)
Pre-mint the new Android identity on the Mac, transplant it, register on the hub.

1. **Generate a fresh identity on the Mac** (doesn't touch the hub yet):
   ```bash
   mkdir -p /tmp/aps3e-st && syncthing generate --home /tmp/aps3e-st
   NEWID=$(syncthing --device-id --home /tmp/aps3e-st); echo "$NEWID"
   ```
2. **Write the Android `config.xml`** (template in §5) into `/tmp/aps3e-st/`,
   filled with: this new device as self, the Mac hub (`4IC6…`) as a peer, folder
   `x6hh6-yt9u5` → the aPS3e savedata path, **`type="receiveonly"`** (per §0a —
   NOT sendreceive yet), `ignorePerms=true`.
3. **Transplant over adb** (app stopped, correct owner + SELinux context):
   ```bash
   PKG=com.github.catfriend1.syncthingandroid
   adb shell am force-stop $PKG
   adb push /tmp/aps3e-st/config.xml /data/local/tmp/
   adb push /tmp/aps3e-st/cert.pem   /data/local/tmp/
   adb push /tmp/aps3e-st/key.pem    /data/local/tmp/
   UID=$(adb shell su -c "stat -c %u /data/data/$PKG")
   adb shell su -c "cp /data/local/tmp/{config.xml,cert.pem,key.pem} /data/data/$PKG/files/ && \
       chown $UID:$UID /data/data/$PKG/files/{config.xml,cert.pem,key.pem} && \
       restorecon /data/data/$PKG/files/{config.xml,cert.pem,key.pem} && \
       rm /data/local/tmp/{config.xml,cert.pem,key.pem}"
   ```
4. **Register the new ID on the Mac hub** (replace the dead `QVMDKH7…`):
   - via REST (apikey from the hub `config.xml`): rename/replace the device, or
   - edit the hub `config.xml` (stop hub first): change the `aPS3e-Flip2`
     `<device id="QVMDKH7…">` to `id="$NEWID"` in BOTH the top-level device def
     **and** inside the `x6hh6-yt9u5` folder's share list; restart the hub.
5. Start Syncthing-Fork once via adb (no UI): `adb shell monkey -p $PKG 1` or
   `am start-foreground-service` for its core service; it reads the injected
   config and connects **Receive Only** → pulls Rocknix's saves from the hub.
6. **Verify** (§6), reverting any receive-only-changed stale items to the hub
   version. **Then flip to Send & Receive headlessly:** force-stop the app, change
   the folder's `type="receiveonly"` → `type="sendreceive"` in
   `/data/data/$PKG/files/config.xml` (`su -c "sed -i …"` or re-push an edited
   config with chown+restorecon), restart. Re-verify it stays *Up to Date*.

### C3 — NOT rooted + fresh identity → **minimal-UI pairing** (few taps, hub-assisted)
Without root we cannot write `/data/data/…`, so the app must generate its own
identity. Minimize taps by doing all the heavy lifting from the Mac hub:
1. Open Syncthing-Fork **once**; from the device's own screen copy its new ID
   (or screenshot via `adb exec-out screencap`). This is the only unavoidable
   read.
2. On the **Mac hub** (REST or config edit): replace `QVMDKH7…` with the new ID,
   keep it in the `x6hh6-yt9u5` share, and set the hub as **introducer** for it.
3. Back in the app: a single **"Add device? / Accept folder?"** prompt appears
   (hub-initiated); accept, set the folder path to the aPS3e savedata dir, and
   choose **Receive Only** (per §0a — NOT Send & Receive yet). ~3 taps.
4. Let it pull Rocknix's saves; **verify** (§6); then one more tap: folder →
   **flip to Send & Receive**. (Total ~4 taps, vs a full from-scratch reconfigure.)
- If the app exposes the underlying syncthing GUI on `:8384`, an alternative is
  `adb forward tcp:8384 tcp:8384` + REST with the app's apikey — but the apikey
  lives in the unreadable private config, so this usually needs root anyway.

**Recommendation:** push for **C2** — confirm root in Phase B. If unrooted, C3 is
the floor (and far better than a from-scratch reconfigure in that UI).

---

## 5. Android `config.xml` template (for C2 injection)

```xml
<configuration version="52">
    <!-- type=receiveonly per §0a: Rocknix is truth; flip to sendreceive AFTER verify -->
    <folder id="x6hh6-yt9u5" label="PS3 Saves"
            path="/storage/emulated/0/Android/data/aenu.aps3e/files/aps3e/config/dev_hdd0/home/00000001/savedata"
            type="receiveonly" rescanIntervalS="3600" fsWatcherEnabled="true"
            fsWatcherDelayS="10" ignorePerms="true" autoNormalize="true">
        <device id="__NEW_ANDROID_ID__" introducedBy=""></device>
        <device id="4IC6HMJ-EITZYEI-CVYXD66-GIZX4SF-M77AA5M-WIQC26E-R75MKHG-WRTVIAT" introducedBy=""></device>
    </folder>
    <device id="__NEW_ANDROID_ID__" name="aPS3e-Flip2" compression="metadata" introducer="false">
        <address>dynamic</address>
    </device>
    <device id="4IC6HMJ-EITZYEI-CVYXD66-GIZX4SF-M77AA5M-WIQC26E-R75MKHG-WRTVIAT" name="Mac-Hub" compression="metadata" introducer="false">
        <address>dynamic</address>
    </device>
    <gui enabled="true" tls="false">
        <address>127.0.0.1:8384</address>
        <apikey>__KEEP_FROM_GENERATE__</apikey>
    </gui>
    <options>
        <localAnnounceEnabled>true</localAnnounceEnabled>
        <globalAnnounceEnabled>true</globalAnnounceEnabled>
    </options>
</configuration>
```
- `__NEW_ANDROID_ID__` = the `syncthing --device-id` from §C2.1.
- Confirm the real savedata path in Phase B (the manifest shows
  `/sdcard/Android/data/aenu.aps3e/...`; `/sdcard` == `/storage/emulated/0`).
- Keep `<apikey>` that `syncthing generate` produced (or set your own).

---

## 6. VALIDATION (gates the §0a flip; no app UI needed)
**Before flipping Android to Send & Receive — prove it equals Rocknix:**
1. Confirm the hub mirrors Rocknix first: hub GUI shows **ROCKNIX → Up to Date**.
2. Android (still **Receive Only**) → hub GUI shows it **Connected** and folder
   **Up to Date** (0 receive-only-changed items; revert any stale leftovers).
3. **Diff the savedata** — compare the Android folder against Rocknix truth by
   file list + sizes (Rocknix via SSH `find …/savedata -type f -printf` is
   GNU-only; use `find … -type f | sort` + `du`); Android via
   `adb shell find …/savedata -type f | sort`. They must match.
4. **Only after 1–3 pass: flip Android → Send & Receive** (§4 step). Re-confirm
   all three stay *Up to Date*.

**Then the A/B baseline is live:** same savedata on Rocknix (RPCS3) and Android
(aPS3e) → run the same game/race on each to compare the ETK tunes head-to-head.

5. Round-trip test (post-flip): drop a sentinel into `/Users/dave/etk-saves/`,
   confirm it reaches the Android savedata dir, then delete.
6. Discipline ([project_save_sync_syncthing]): play ONE OS at a time, close the
   emulator before letting sync settle (the conflict-free rule).

---

## 7. RISKS / OPEN ITEMS
- **MANAGE_EXTERNAL_STORAGE:** on Android 11+, Syncthing-Fork needs all-files
  access to read another app's `/Android/data/aenu.aps3e/…`. How it worked before
  is unknown (cert/config lost). If the injected config can't see the path,
  either grant all-files access (one Settings toggle) or sync a Syncthing-owned
  dir and have aPS3e point there. **Confirm in Phase B.**
- **Root availability** is the pivotal unknown — Retroid stock Android is often
  un-rooted. Drives C2 vs C3.
- **SELinux context** on injected files — `restorecon` is mandatory or the app
  won't read them (C2).
- **Config schema version** — match the running Syncthing-Fork's `version="…"`;
  let `syncthing generate` set it, then merge our folder/device blocks.
- **Don't run two writers:** stop the app before editing its config; stop the
  hub before editing the hub config.

---

## 8. CLEANUP ASIDE (optional, not blocking)
Both Mac (`~/Library/Application Support/Syncthing/config.xml`) and Rocknix
(`/storage/games-external/.config/syncthing/config.xml`) carry an empty
`<folder id="" path="">` + `<device id="">` stub. Remove via REST or a
stop-edit-start while you're in there. Harmless but messy.

---

## RELATED
- [project_save_sync_syncthing] — the working 3-node setup this restores.
- `~/aps3e_android_backup_20260612/RESTORE_MANIFEST.md` — savedata + aPS3e
  restore (step 5 "re-pair Syncthing" is exactly this dossier).
- Syncthing-Fork (Catfriend1) pkg `com.github.catfriend1.syncthingandroid`.
