# Diagnosis: Tools-menu icons missing in ES (incl. ETK Pitstop)

**Date:** 2026-05-29
**Rig:** SM8250 (Retroid Pocket Flip 2), Rocknix `nightly-20260528`, ES branch **`next`**, theme **`es-theme-art-book-next`** (default; `ThemeSet` unset).
**Closes:** the long-open R1b spike (`GameInstallFeatureDossier.md:466` — "confirm ES renders SVG as a game `<image>`").
**Verdict:** **Rocknix-side, not ETK.** Two independent Rocknix defects; ETK's SVG was a red herring.

---

## CORRECTION / CLOSEOUT (2026-06-03, official 20260601, branch `next`)

Re-verified on the official release and **the "platform-wide / clean-install" framing below was too strong.** The missing Tools icons are **gated on the `gamelist-view-artwork` subset**, which is an **operator preference**, not a Rocknix default and not anything ETK sets:

- The theme `es-theme-art-book-next` declares the subset with `image` **first** (theme.xml:113–118) → with no stored value, ES defaults to **`image`**, under which `game-artwork` reads `{game:image}` and the stock tools (which set only `<image>`) **DO render**.
- The shipped default `es_settings.cfg` carries **no** `subset.gamelist-view-artwork` line; upstream sets none either. So a true clean install = `image` = tools have icons.
- This rig stores `subset.gamelist-view-artwork = boxart` in `/storage/.config/emulationstation/es_settings.cfg` — an **operator choice** (Game Artwork → Boxart, made via the theme options UI for nicer game boxart). Under `boxart` the element reads `{game:thumbnail}`; under `logo` it reads `{game:marquee}`. Tools set neither → no icon. ETK never writes this key (grep-confirmed).

**So the real shape of the issue:** Tools is a hack — utilities shoehorned into a games-art UI — and the default theme has **no fallback** from `{game:thumbnail}`/`{game:marquee}` back to `<image>` for entries that only set `<image>`. Anyone who switches Game Artwork to **Boxart or Logo** loses *all* stock tool icons. That's a legitimate upstream rough edge, but it is **not** a default-state bug and **not** caused by ETK's `modules/` injection.

**Consequences:**
- The two underlying upstream facts are still real and still in the current tree (verified live on `ROCKNIX/distribution`, 2026-06-03): tool entries are `<image>`-only (41 entries, 0 thumbnail/marquee) and the *Start touchHLE* `<desc>` still has the unescaped `&` (line 392, fails XML parse).
- **Quick operator workaround (no edits):** set Game Artwork subset back to **Image** → every stock tool icon returns.
- **ETK's own fix is still correct and superior:** the Pitstop block emits `<image>` + `<thumbnail>` + `<marquee>` (all → the same SVG), so the Pitstop tile renders under **every** subset regardless of operator preference. Keep it.
- **Bug-report posture:** if filed, frame it as "default theme has no `<image>` fallback for Tools under boxart/logo subsets" (a fallback/UX request), **not** "no icons on a clean install" (false). See the correction banner in `RocknixToolsArtworkBugReport.md`.

---

## Method — proven on-rig, ETK uninstalled

Every test below was run with **ETK fully uninstalled**, over SSH, restarting only `essway.service` (a clean ES reload that does *not* re-run the `001-sync-modules` rsync — `rocknix-autostart` is `oneshot`+`RemainAfterExit`, so a manual gamelist edit survives an ES restart but is reverted by a full reboot). Rig was restored to byte-stock state afterward.

| Step | Action | Result | Inference |
|---|---|---|---|
| 0 | Uninstall ETK, reboot, Update Gamelists | Stock Tools icons still missing | **ETK exonerated** — it can't be the cause of a failure that persists without it. |
| 1 | `xmllint`/ElementTree parse of `/storage/.config/modules/gamelist.xml` | **Not well-formed** — invalid token line 392 | Defect #1 (below). |
| 2 | Escape the one bare `&` → well-formed; reload ES | Icons **still missing** | The malformed XML is *not* why icons are missing. Separate bug. |
| 3 | Point touchHLE `<image>` at a real **PNG**; reload | touchHLE **still blank** | **Not an SVG-vs-PNG format issue.** |
| 4 | Observe main system carousel (SNES/PS3 logos) | **Render fine** | **Not a global ES texture/render failure.** Image rendering works. |
| 5 | Set touchHLE `<image>`+`<thumbnail>`+`<marquee>` → PNG; reload | touchHLE **renders** | Delta from step 3 is only `<thumbnail>`/`<marquee>` → **the theme reads those fields, not `<image>`.** |

---

## Defect #1 — Rocknix ships a malformed `modules/gamelist.xml`

- The Tools gamelist is shipped read-only at **`/usr/config/modules/gamelist.xml`** and `rsync -a --delete`'d to `/storage/.config/modules/` every boot by `/usr/lib/autostart/common/001-sync-modules`. ETK never touches `/usr/config`.
- Its stock **"Start touchHLE"** `<desc>` contains an **unescaped `&`** ("...iOS 2 **&** 3..."), which is invalid XML. Escaping it (`&` → `&amp;`) makes the file well-formed; it is the *only* such token.
- The old ES parser tolerated it (ETK's injector even has a comment deliberately preserving it); the **`next`** ES branch is stricter. Real bug, but **not** the cause of the missing icons (step 2).

## Defect #2 — the icon problem: theme reads `<thumbnail>`/`<marquee>`, tools set only `<image>`

The default theme `es-theme-art-book-next` (detailed view, theme.xml ~L578-600):

```xml
<image name="md_image"><visible>false</visible></image>   <!-- standard gamelist <image> mapping: HIDDEN -->
<image name="game-artwork" extra="true">
   <path ifSubset="gamelist-view-artwork:boxart">{game:thumbnail}</path>   <!-- <thumbnail> -->
   <path ifSubset="gamelist-view-artwork:logo">{game:marquee}</path>      <!-- <marquee>  -->
   <path ifSubset="gamelist-view-artwork:image-cropped|image">{game:image}</path>  <!-- <image> -->
```

- The theme **hides `md_image`** (ES's standard mapping of the gamelist `<image>` tag) and renders art only through its own `game-artwork` element, **subset-gated**.
- Step 3 (PNG in `<image>` → blank) vs step 5 (added `<thumbnail>`/`<marquee>` → renders) proves the **active artwork subset is not `image`** — it resolves to `{game:thumbnail}` (boxart) or `{game:marquee}` (logo).
- **Rocknix's stock tool entries — and ETK's Pitstop entry — populate only `<image>`.** Field mismatch → no Tools artwork, regardless of format or XML validity.
- Corroborating: theme has **no `tools` system definition**; ES logs `WARNING Unknown platform for system "tools"`.

This is almost certainly the "inconclusive, undocumented known issue" — on this theme + ES-`next`, Tools icons never resolve because the shipped entries use the wrong metadata field.

---

## Fixes

### Upstream (Rocknix) — see `dossiers/RocknixToolsArtworkBugReport.md`
1. **Escape the `&`** in `/usr/config/modules/gamelist.xml` (touchHLE `<desc>`).
2. **Field mismatch** — pick one: (a) add `<thumbnail>` (and/or `<marquee>`) to each tool entry pointing at the existing `./images/*.svg`; or (b) set the `gamelist-view-artwork` subset default to `image`; or (c) un-hide `md_image` for the tools system. Option (a) is the most surgical and theme-agnostic.

### ETK-side (v0.1.2) — makes our tile render TODAY, regardless of the Rocknix bug
- `bin/etk_modules_inject.py` `GAME_BLOCK`: also emit **`<thumbnail>./etk_pitstop.svg</thumbnail>`** and **`<marquee>./etk_pitstop.svg</marquee>`** alongside the existing `<image>`. That satisfies whichever artwork subset is active, so the Pitstop icon shows under `art-book-next` without waiting on the upstream fix. (Keep `<image>` for themes that use it.) Low-risk, touches only ETK's own block.
- Update the now-obsolete injector comment that claims ES "tolerates" the raw `&` — false on `next`.
- Note: SVG vs PNG is **not** the issue (step 3) — no need to rasterize to PNG. The original SVG choice was correct; it just needs to be in a field the theme reads.

## Open / not pursued
- Which of `boxart` vs `logo` is the precise active subset (test set both). Immaterial to the fix — option (a) covers both.
- Whether other (non-tools) gamelists are affected: system logos render (system view OK); per-game gamelist art under this theme would have the same `<image>` vs `<thumbnail>` dependency, but tools are the reported surface.
