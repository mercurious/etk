# Bug report (draft for ROCKNIX) — Tools menu shows no icons

> **⚠️ CORRECTION 2026-06-03 — DO NOT FILE AS-IS.** Re-verified on official 20260601:
> the missing icons are **subset-gated, not a clean-install/default state.** Rocknix's
> default `gamelist-view-artwork` subset is **`image`**, under which the `<image>`-only tool
> entries render fine. The icons only vanish when the operator sets Game Artwork to **Boxart**
> or **Logo** (theme then reads `{game:thumbnail}`/`{game:marquee}`, which tools don't set).
> This rig is on `boxart` **by operator preference** — not a Rocknix default, not ETK.
> The "on a clean install, every entry is text-only" claims below are therefore **wrong** and
> must be reworded before any submission: reframe as *"the default theme has no `<image>`
> fallback for Tools entries under the boxart/logo subsets."* The malformed-`&` defect (#1) is
> still valid and unrelated. Full reasoning: `ToolsMenuArtworkDiagnosis.md` → CORRECTION/CLOSEOUT.

> **HOW TO FILE (verified 2026-05-29):** ROCKNIX has GitHub Issues **disabled** on both
> `distribution` and `distribution-nightly`. Their policy (`.github/ISSUE_TEMPLATE/config.yml`
> + `bug-report.md`): **discuss on Discord FIRST** — https://discord.gg/seTxckZjJy — or any
> GitHub issue is auto-closed. So: post the "Discord-ready" block below in the ROCKNIX Discord
> (#support). If a maintainer asks for a tracked issue, it goes on `ROCKNIX/distribution` using
> their Bug Report template (reproduced below, pre-filled).

---

## Discord-ready post (paste this)

**Tools menu shows no app icons on `next` (nightly-20260528, SM8250 / RP Flip 2, default theme `art-book-next`).** On a clean install, every entry in the Tools system is text-only — no stock icons render — even though `/storage/.config/modules/images/*.svg` exist and are referenced in `gamelist.xml`. System-carousel logos render fine, so it's not a general render failure. I traced two causes (details + repro below): (1) the shipped `modules/gamelist.xml` is malformed XML — unescaped `&` in the *Start touchHLE* `<desc>`; and (2) the bigger one: the default `es-theme-art-book-next` theme hides `md_image` and draws Tools art from `<thumbnail>`/`<marquee>`, but the tool entries only set `<image>`, so nothing shows. Adding `<thumbnail>`/`<marquee>` to an entry makes its icon appear. Has this been seen before / is there a preferred fix?

---

## GitHub Bug Report template (pre-filled, if requested)

**Title:** `[BUG] Tools menu shows no app icons on next branch (theme reads thumbnail/marquee, entries set only image)`

### Have you first reported the issue on the rocknix discord and checked rocknix.org for a solution?
Yes — posted in Discord (link/date: __________).

### Describe the bug
On the `next` branch, the **Tools** system renders every entry as text only — no app icons — on a clean device with no third-party software. ROCKNIX ships per-tool SVGs in `/storage/.config/modules/images/` and references them in `gamelist.xml`, and system-carousel logos render normally, so image rendering works. Two underlying defects:
1. **`modules/gamelist.xml` is not well-formed** — an unescaped `&` in the stock *Start touchHLE* `<desc>` ("iOS 2 & 3"). Should be `&amp;`. (Older ES tolerated it; `next` is stricter.)
2. **Field mismatch (the actual icon cause):** the default theme `es-theme-art-book-next` sets `md_image` invisible and renders Tools art via a subset-gated element bound to `{game:thumbnail}` (boxart) / `{game:marquee}` (logo) / `{game:image}` (image). The shipped tool entries set only `<image>`, so under the active subset nothing renders. Fixing the XML alone does NOT restore icons.

### How to reproduce
1. Boot a clean `next` nightly (default `art-book-next` theme) and open **Tools** → all entries are text-only.
2. `python3 -c 'import xml.etree.ElementTree as ET; ET.parse("/storage/.config/modules/gamelist.xml")'` → `ParseError: not well-formed ... line 392` (the `&`).
3. Escape that `&`, reload ES (`systemctl restart essway`) → still no icons (proves #1 isn't the icon cause).
4. Point one entry's `<image>` at a PNG → still blank (proves it's not SVG-vs-format).
5. Add `<thumbnail>` + `<marquee>` to that entry (same image) → **icon now renders** (proves the theme reads those fields, not `<image>`).

### Information
 - ROCKNIX Version: 20260528 (nightly, branch `next`, BUILD_ID 1a7c008…)
 - Hardware Platform: SM8250 (Retroid Pocket Flip 2)

### Log file
`/var/log/es_log.txt`: `WARNING  Unknown platform for system "tools" (platform "tools" from list "tools")`. No image/texture errors logged.

### Context
Suggested fixes (any one): (a) add `<thumbnail>` (and/or `<marquee>`) to each tool entry in `modules/gamelist.xml` pointing at the existing `./images/<tool>.svg` — most surgical; (b) set the theme's `gamelist-view-artwork` subset default to `image`; (c) un-hide `md_image` for the tools system. Separately, escape the `&` in #1.

---

## Technical appendix (full diagnosis)

> Two related defects; #1 is a trivial data fix, #2 is the actual icon regression.

## Environment
- **Build:** ROCKNIX `nightly-20260528` (`OS_VERSION="20260528"`, `BUILD_ID=1a7c008d…`, `BUILD_BRANCH="next"`)
- **Device:** SM8250 (Retroid Pocket Flip 2)
- **EmulationStation:** `next` branch
- **Theme:** `es-theme-art-book-next` (default; no `ThemeSet` override)
- Reproduced on a **clean device with no third-party software installed.**

## Summary
In the **Tools** system, no game/app icons render — every entry is text-only — even though ROCKNIX ships per-tool SVG artwork in `/storage/.config/modules/images/` and references it in `gamelist.xml`. System-carousel logos render normally, so image rendering itself works.

---

## Defect 1 — `modules/gamelist.xml` is not well-formed XML

`/usr/config/modules/gamelist.xml` (rsync'd to `/storage/.config/modules/gamelist.xml` each boot by `autostart/common/001-sync-modules`) contains an **unescaped `&`** in the *Start touchHLE* entry:

```xml
<desc>... compatible with iOS 2 & 3, enabling ...</desc>
```

A `&` not starting a character entity is invalid XML. The legacy ES parser tolerated it; the `next` parser is stricter.

**Repro:**
```sh
python3 -c 'import xml.etree.ElementTree as ET; ET.parse("/storage/.config/modules/gamelist.xml")'
# xml.etree.ElementTree.ParseError: not well-formed (invalid token): line 392, column 277
```

**Fix:** escape it — `iOS 2 &amp; 3` — in the source `modules/gamelist.xml`. It is the only such token in the file.

---

## Defect 2 — Tools entries set `<image>`, but the default theme reads `<thumbnail>`/`<marquee>`

This is the actual cause of the missing icons. Fixing Defect 1 alone does **not** restore them.

`es-theme-art-book-next` (detailed view) **hides the standard `md_image`** and renders art via a subset-gated element:

```xml
<image name="md_image"><visible>false</visible></image>
<image name="game-artwork" extra="true">
   <path ifSubset="gamelist-view-artwork:boxart">{game:thumbnail}</path>
   <path ifSubset="gamelist-view-artwork:logo">{game:marquee}</path>
   <path ifSubset="gamelist-view-artwork:image-cropped|image">{game:image}</path>
```

The shipped tool entries provide only `<image>./images/<tool>.svg</image>`. With the active artwork subset resolving to `boxart`/`logo`, the theme looks at `{game:thumbnail}`/`{game:marquee}` — which the entries don't set — so nothing renders.

**Repro (proves it's the field, not the format or the XML):**
1. Make `gamelist.xml` well-formed (Defect 1) and point one entry's `<image>` at a PNG → **still blank**.
2. Add `<thumbnail>` and `<marquee>` to that same entry pointing at the same image → **icon renders.**
   (ES is reloaded between steps via `systemctl restart essway.service`; system logos render throughout, so rendering works.)

**Fix — any one of:**
- **(preferred, surgical)** add `<thumbnail>` (and/or `<marquee>`) to each tool entry in `modules/gamelist.xml`, pointing at the existing `./images/<tool>.svg`; or
- set the theme's `gamelist-view-artwork` subset **default to `image`**; or
- un-hide `md_image` for the tools system in the theme.

## Notes
- `ES log: WARNING Unknown platform for system "tools" (platform "tools" from list "tools")` — cosmetic, but the theme has no `tools` system definition either.
- All `./images/*.svg` files exist; SVG is fine — the issue is purely which metadata field the theme consumes.
