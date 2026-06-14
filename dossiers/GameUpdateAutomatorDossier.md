# Feature Dossier — Game Update Automator (ETK Pitstop TOOLS › Update Games)

**Status:** Feasibility + design. Drafted 2026-06-13 with live on-rig validation.
**Asks answered:** (1) Can we fork/reuse `rusty-psn`? (2) Does *current* ROCKNIX
support this, or is it gated on a future custom ROCKNIX image?

---

## 0. VERDICT (read this first)

**Build it on the CURRENT ROCKNIX install.** Split by capability:

| Capability | Current ROCKNIX? | Notes |
|---|---|---|
| Discover updates (query Sony API) | ✅ **NOW** | Proven live on the rig today (see §3). |
| Download official update PKGs | ✅ **NOW** | Plain `curl`; sole hurdle = Sony's CA (ship it with ETK). |
| SHA1-verify downloads | ✅ **NOW** | `sha1sum`; hashes come from the same manifest. |
| Sequential multi-update ordering | ✅ **NOW** | Manifest lists versions in order; pure logic. |
| **Install the update PKG into RPCS3** | ⚠️ **NOW, with a fix** | The existing installer breaks on updates for a *specific, identified* reason (§5). Fixable in-tree. |
| *Bulletproof headless install* | 🔮 **Custom image** | A baked-in headless PKG installer trivializes the install step — the one part where a custom image genuinely helps. |

**So it is NOT postponed.** A v1 ships on current ROCKNIX. The future custom
image only *trivializes the install step* (replacing the GUI-confirm dance with
a headless installer) — a robustness upgrade, not a prerequisite.

**On `rusty-psn`: don't fork it — reimplement its protocol.** It's a Rust/egui
app (MIT, has a CLI). Cross-compiling it to aarch64 + shipping a binary blob is
heavier than the problem warrants: we proved below that the entire protocol is
one `curl` + an XML parse + `sha1sum`. Reimplement in ~150 lines of
python/shell (ETK-native), and cite `rusty-psn` as the MIT reference for the
cert/URL handling. Vendoring its CLI binary is the fallback if reimplementation
snags.

---

## 1. THE PROBLEM

PS3 retail games ship **sequential incremental update PKGs** (1.00 → 1.0x → …).
Applying them by hand is brutal: download each from Sony, drop into the staging
folder, install, repeat — for *every* version, in order. The operator's GT6 sits
"one version up from 1.0" precisely because the manual path stalls.

**Worse: ETK's current PKG installer BREAKS on update PKGs** (the operator hit
this). Root cause is precise — `_run_install()` in `bin/etk_pitstop.py` detects
completion by watching for a **new game folder** to appear under
`dev_hdd0/game/` (`set(listdir) - before`). An update merges into the *existing*
`<TITLE_ID>` folder, so **no new folder ever appears** → `new_id` stays `None` →
the installer reports *"Install did not complete – no game folder appeared."*
even when RPCS3 installed the update fine. It's a base-install-specific heuristic,
not an OS limitation. (Related: [project_two_packaging_models] flagged the
"update-PKG on a disc base" path as untested — relevant to GT5, see §6.)

The operator's intuition was *"if we had an update automator, the broken install
tool is moot."* Mostly right, with one correction: the automator must still
*perform* the install, so it either **fixes** the completion-detection (§5,
Strategy A) or **bypasses** RPCS3 with a headless extractor (Strategy B). Either
way the break is solvable; it isn't automatically moot.

---

## 2. WHAT `rusty-psn` IS (assessment)

- **What:** "grab updates for PS3 & PS4 games, directly from Sony's servers
  using their updates API." Download-only (does **not** install).
- **Language/license:** Rust, **MIT**. ~99% Rust.
- **Interfaces:** GUI (egui) **and a CLI** (+ Docker for headless Linux/macOS).
- **GUI deps** (xcb/xkbcommon/speech-dispatcher/openssl) are irrelevant — only
  the CLI matters to us, and even that we don't need to ship.
- **aarch64:** not documented, but Rust cross-compiles to
  `aarch64-unknown-linux-musl` (static) cleanly. Feasible — just unnecessary.

**Fork/vendor vs reimplement:** the protocol (§3) is trivial and we proved it
works with stock `curl`. Reimplementing keeps ETK pure shell+python (no Rust
toolchain, no ~5–10 MB binary blob, BusyBox-friendly), and respects MIT by
attribution. **Recommendation: reimplement; reference rusty-psn's source for the
cert handling.** Keep "vendor the CLI binary" as a documented fallback.

---

## 3. THE SONY UPDATE PROTOCOL — PROVEN LIVE ON THE RIG (2026-06-13)

Endpoint: `https://a0.ww.np.dl.playstation.net/tpl/np/<TITLE_ID>/<TITLE_ID>-ver.xml`

Live test from the rig (`curl 8.14.1 / OpenSSL 3.5.1`):

```
GT6 (NPEA00502):  curl ... -ver.xml   → rc=60  SSL: self-signed cert in chain
GT6 (NPEA00502):  curl -k ... -ver.xml → rc=0, 5901 bytes — FULL update chain:
  version="01.02" size=1315385824 sha1sum=8a83fd29…
  version="01.03" size= 102856304 sha1sum=e18a09ba…
  version="01.04" size= 223697808 …
  version="01.05" … 01.06 … 01.07 (1.0 GB) … 01.08 (1.5 GB) … (more follow)
```

Findings:
1. **The API is reachable from ROCKNIX today.** Network + HTTPS + the XML all
   work. The manifest gives, per package: `version`, `size`, `sha1sum`, and a
   direct `url` to the `.pkg`.
2. **The ONLY blocker is the CA chain.** Sony's endpoint presents a chain that
   ROCKNIX's bundle (`/etc/ssl/certs/ca-certificates.crt → /run/rocknix/cacert.pem`)
   doesn't trust → `curl (60)`. This is the exact quirk `rusty-psn` handles
   internally. **Fix on current ROCKNIX:** ship Sony's CA/intermediate PEM with
   ETK and call `curl --cacert $ETK_ROOT/config/sony_update_ca.pem`. Proper
   verification, no `-k`, no system changes. (A custom image could instead bake
   the CA into the system store — marginally cleaner, **not** required.)
   - Avoid `curl -k` + "trust the manifest's SHA1": that's TOFU — a MITM that
     controls the XML controls the hashes too. Verify the cert; THEN SHA1 each
     download against the (now-trusted) manifest.
3. **GT6 updates are sequential, not one cumulative blob** — 01.02, 01.03, …
   each a distinct package. This is the "1.0 through each successive update"
   pain made literal: ~7+ packages, hundreds of MB to 1.5 GB each, in order.

---

## 4. UI/UX

`ROCKNIX ES › TOOLS › ETK PITSTOP › TOOLS › Update Games`

```
UPDATE GAMES
------------------------------------------------
  GAME                 INSTALLED   LATEST   STATUS
> Gran Turismo 6       01.01       01.22    7 updates  [UPDATE]
  Gran Turismo 5       (disc)      02.17    available  [UPDATE]
  LittleBigPlanet      01.00       01.00    up to date
------------------------------------------------
CONFIRM: update    BACK: menu        (checking Sony…)
```

- **List**: installed games × their installed version (from `PARAM.SFO`
  `APP_VER`/`VERSION`) vs the latest in the manifest. "up to date" rows are
  dimmed; only out-of-date rows expose `[UPDATE]`.
- **Select → confirm** → sequential download+install of every missing version in
  order, **all surfaced through mako**: `Update GT6: 3/7 — downloading 01.05
  (102 MB)…`, `verifying…`, `installing…`, per package.
- Mirrors the **Manage Shaders** sub-mode state machine exactly
  (`tools_mode = "updates" | "updates_confirm" | "result"`, lazy scan with a
  busy frame, queued long-op in the main loop).

---

## 5. INSTALL STRATEGY — the crux

### Strategy A — reuse RPCS3 `--installpkg`, fix the completion detection (ship NOW)
Keep the proven launch + sway-focus + uinput-Enter flow, but replace the
new-folder heuristic (which can't work for updates) with an **update-aware
completion signal**:
- read `dev_hdd0/game/<TID>/PARAM.SFO` `APP_VER` before/after and wait for the
  bump, **or**
- tail `RPCS3.log` for the "Successfully installed" line, **or**
- folder-size-stable (already implemented as the fallback) keyed on the
  *existing* `<TID>` dir instead of a new one.

Then loop per package in version order. Pros: works on current ROCKNIX, reuses
everything. Cons: the GUI/uinput dance runs once per package (7× for GT6) — slow
and the most fragile link. **This is the v1.**

### Strategy B — headless PKG extraction, no RPCS3 (the endgame)
Retail *update* PKGs are finalized with publicly-known keys (unlike `.rap`-gated
base games), so they can be decrypted + unpacked straight into
`dev_hdd0/game/<TID>/` without launching RPCS3 at all. Eliminates the GUI dance
and the completion-detection fragility entirely; sequential installs become a
clean loop. Needs a headless PS3-PKG extractor (port RPCS3's `PKG.cpp`
algorithm, or a small standalone tool).

**This is exactly where the future custom ROCKNIX image earns its keep:** bake a
headless PKG installer (a standalone tool, or a patched RPCS3
`--installpkg --headless`) into the image, and the automator just calls it N
times — *trivial*. Strategy B is also possible on current ROCKNIX if ETK ports
the extractor itself (more effort).

---

## 6. GT5 vs GT6 — two install classes

- **GT6 = `NPEA00502` (digital / pkg base):** standard "update PKG onto a pkg
  base" path. The reference case.
- **GT5 = `BCUS98114` (disc / `.iso` base):** updates install into
  `dev_hdd0/game/<TID>` *on top of a disc image* — the **untested**
  disc-base-update path flagged in [project_two_packaging_models]. Same Sony API
  (verified reachable), but validate the disc+update install separately; failure
  smells differ (`CELL_ENOTMOUNTED /dev_bdvd` vs a pkg path).

Per the IMMUTABLE LAW in AI_MANIFEST: validate the updater on **both** packaging
models before calling it done.

---

## 7. REUSE MAP (almost nothing is new)

| Need | Reuse |
|---|---|
| List installed titles + names | `_list_psn_games()`, `games.yml`, `gamelist.xml`, `_resolve_game_names()` |
| Installed version | extend `_sfo_title()` → also read `APP_VER`/`VERSION` from PARAM.SFO |
| Download w/ progress | `_curl_with_progress()` + `_curl_total_bytes()` (from the PADDOCK known_repo path) |
| mako toasts | `_Notifier` |
| Install one PKG | `_run_install()` (with the §5-A completion fix) |
| Idle/Sentry lock during install | `ETK_INSTALL_LOCK` |
| Sub-screen state machine + busy frame + result | the **Manage Shaders** scaffolding just landed |
| **New** | `bin/psn_update.sh` (or python): query ver.xml (`--cacert`), parse, version-compare, download+SHA1, drive sequential install |

---

## 8. LEGAL / DEFENSIBILITY POSTURE

Strongest of any ETK acquisition feature: updates come from **Sony's own
servers**, are **official**, and patch a game the user already owns. Cleaner than
the known_repo GET hatch (which fetches *base* games from third-party sources).
ETK provides **no bytes** — the base game stays operator-supplied; updates flow
direct from Sony. Fits the mature, conservative posture
([feedback_audience_strategy], [project_private_paddock]). Ship the Sony CA PEM
as a trust anchor, not as redistribution of anything proprietary.

---

## 9. SPACE / TIME / UX

- GT6's chain is multiple GB total. **Stream each package to a temp/staging dir,
  install, then delete it before the next** — never hold the whole chain at once
  (SD space is already contended; cf. vault/space memories).
- Long op (downloads + 7 installs). mako must show **sub-progress** (which
  package, %, verify, install) so it never looks hung.
- Refuse if RPCS3 is running (reuse the gate). Hold the Sentry in IDLE via the
  install lock so no phantom telemetry fires.
- Resumability: if interrupted mid-chain, the next run re-reads the installed
  `APP_VER` and continues from there — naturally idempotent.

---

## 10. BUILD & TEST SEQUENCE (validate-before-integrate)

1. ✅ **DONE** — confirm the Sony API is reachable from ROCKNIX (cert is the only
   blocker). Real GT6 manifest retrieved.
2. Obtain Sony's CA/intermediate PEM; prove `curl --cacert` verifies cleanly (no
   `-k`) against `NPEA00502-ver.xml` and a `BCUS98114-ver.xml`.
3. Parser + version-compare unit test (manifest → ordered list of missing
   versions, given an installed `APP_VER`).
4. Download + SHA1-verify ONE GT6 package end-to-end on the rig.
5. **Strategy A install of one update** with the fixed completion detection;
   confirm `APP_VER` bumps and the game still boots.
6. Sequential chain (2–3 updates) on GT6 (pkg base).
7. Repeat the single + chain on GT5 (`BCUS98114`, disc base) — the untested
   class.
8. Only then wire the TOOLS › Update Games UI over the proven engine.

---

## 11. SCOPE CUTS (v1)

- PS3 only (rusty-psn also does PS4; out of scope).
- Strategy A install (RPCS3 wrapper). Strategy B / headless installer deferred to
  the custom-image track.
- No background/auto-update; explicit operator action per game.
- No delta/patch cleverness — install whatever the manifest lists, in order.

---

## 12. OPEN QUESTIONS / RISKS

- **Sony CA sourcing & longevity:** which exact PEM, and does Sony rotate it?
  (rusty-psn's repo is the reference; pin + document.)
- **`.pkg` download host:** may differ from the XML host (CDN vs
  np.dl) — confirm whether the CDN also needs the Sony CA or a public one.
- **Cumulative vs sequential per title:** GT6 is sequential; some titles publish
  a single cumulative latest. Follow the manifest; don't assume.
- **Strategy A fragility at 7× GUI installs** — the strongest argument for
  prioritizing the headless installer (Strategy B / custom image) sooner.
- **Disc-base updates (GT5)** — unproven; treat as a separate gate.

---

## RELATED
- [project_two_packaging_models] — pkg vs iso; the untested disc+update path.
- [project_install_feature_spike] / `_run_install` — the installer being reused.
- [project_manage_shaders] — the TOOLS sub-screen pattern to mirror.
- [project_private_paddock] §known_repo — sibling "operator-supplied source"
  feature; reuse `_curl_with_progress`.
- Reference: https://github.com/RainbowCookie32/rusty-psn (MIT).

---

## ADDENDUM A — PPU recompilation across the update chain (open question, 2026-06-13)

**Q (operator):** in the automated install of a sequential chain (01.02 → … →
final), do the PPU modules need recompiling after **each** successive `.pkg`, or
**only after the final** one?

**Likely answer: only the final one — and even then, not as an automator step.**

Reasoning:
- RPCS3's PPU (and SPU) recompiler is **content-addressed**: compiled modules
  cache by a hash of the executable/SPRX (`ppu-<hash>` dirs under
  `/storage/.cache/rpcs3/cache/`). Compilation happens **lazily at game launch**,
  never at install time. Installing a PKG only extracts files.
- The automator installs pkg-after-pkg **without launching the game between
  them**. Intermediate versions' modules are therefore never executed → never
  compiled. Only the *final* version's changed modules compile, on the first
  launch after the chain finishes. So there is nothing to recompile "per update,"
  and the automator need not trigger any recompile — RPCS3 does it on next boot.
- Net cost: **one** slower first launch after the final update (changed modules
  compile once, then cache).

**Why it's still worth a quick test:**
- Confirm no package in the chain forces a mid-chain launch/build step (PS3
  update PKGs generally don't, but verify).
- **Orphaned-cache hygiene:** every version bump changes module hashes, so the
  prior version's `ppu-<hash>` entries become dead weight — the same pattern as
  stale shaders after a Mesa bump (cf. [project_manage_shaders]). Over a long
  chain these accumulate. The TOOLS › Manage Shaders **"Clear RPCS3 cache"**
  action already targets exactly `/storage/.cache/rpcs3/cache`, so the cleanup
  path exists; the open item is whether the automator should *offer* a one-tap
  "clear stale PPU/SPU cache" after a chain or just defer to Manage Shaders.

**Concrete test (cheap; fold into §10 validation):**
1. Snapshot the `ppu-*` dir count in `/storage/.cache/rpcs3/cache/` and the size
   of `dev_hdd1/caches/<TID>_<TID>`.
2. Install the GT6 chain **with no launches between packages**.
3. Confirm the snapshot is **unchanged** after the installs → proves no mid-chain
   compile.
4. Launch once → expect a single up-front recompile of the final version's
   modules; time it; confirm subsequent launches are cached (fast).
5. Count how many orphaned `ppu-<hash>` dirs the chain left → informs whether a
   post-chain cache-clear prompt is worth adding.

**Expectation:** steps 1–3 identical, step 4 a one-time compile. If so the
automator stays simple — no per-update recompile, just an honest *"first launch
will be slow while it compiles the update"* line in the result screen.
