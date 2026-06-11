# Pro Tuning index

> **Status (2026-06-11): distribution withdrawn pending legal review.** The index below is
> intentionally empty and the prior vault Release tag has been unpublished. The catalog
> design, tooling, and homologation gate are unchanged and documented here for when the
> review concludes. Nothing in the ETK install depends on this index being populated.

This directory is the **curated catalog** of one-command Pro Tuning bundles. 

**Two storage planes, one repo:**
- `manifest.json` (here, in the git tree) — small, versioned, diffable, **PR-able**.
- the vault **bytes** — large binaries in **GitHub Releases** (outside git history, free, CDN-backed).

## How an install works
1. `pro-tuning/install-protune.sh` runs on the rig (via the shared one-liner).
2. It fetches this `manifest.json`, matches `chipset` + `game.id`.
3. It gates on `homologation.mesa_hash` (first 64 KB of `libvulkan_freedreno.so`, sha256) —
   exact match guarantees the shaders load; mismatch → config-only.
4. It pulls `bundle.url` from Releases, verifies `bundle.sha256`, and injects.

## Tags = free partition rotation
One Release tag per `chipset:turnip` epoch. When a new official ROCKNIX bumps Turnip,
cut a new tag (`vault-SM8250-turnip26.2.0`), recompile clean-room vaults into it; the old tag
stays frozen for anyone still on the prior build. The `mesa_hash` gate keeps a rig from loading
the wrong-driver crate.

## Contributing a tune (the "competition wants in" path)
A second tuner joins by **opening a pull request** that adds (or updates) one entry here, with the
bytes uploaded to the matching Release. No accounts, no infra — review the PR, merge, done.
