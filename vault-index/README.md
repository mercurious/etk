# Pro Tuning index

This directory is the **curated catalog** of one-command Pro Tuning bundles. It is the
through-line of the whole distribution plan ([../dossiers/ShaderDistributionFusionDossier.md](../dossiers/ShaderDistributionFusionDossier.md)):
the catalog today, the swarm's bootstrap registry tomorrow.

**Two storage planes, one repo:**
- `manifest.json` (here, in the git tree) — small, versioned, diffable, **PR-able**.
- the vault **bytes** — large binaries in **GitHub Releases** (outside git history, free, CDN-backed).

## How an install works
1. `pro-tuning/install-protune.sh` runs on the rig (via the shared one-liner).
2. It fetches this `manifest.json`, matches `chipset` + `game.id`.
3. It gates on `homologation.mesa_hash` (first 64 KB of `libvulkan_freedreno.so`, sha256) —
   exact match guarantees the shaders load; mismatch → config-only.
4. It pulls `bundle.url` from Releases, verifies `bundle.sha256`, and injects.

## Entry schema (`pro_tuning_index: 1`)
```jsonc
{
  "game":   { "id": "NPUA80075", "name": "Gran Turismo 5 Prologue" },
  "chipset": "SM8250",
  "homologation": {
    "turnip_version": "26.1.0",          // human label
    "rocknix_build":  "20260601",
    "mesa_hash": "<sha256 of head -c 65536 /usr/lib/libvulkan_freedreno.so>"  // the hard gate
  },
  "release_tag": "vault-SM8250-turnip26.1.0", // one Release per chipset:turnip epoch
  "bundle": {
    "asset":  "protune_NPUA80075_SM8250.zip",
    "url":    "https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>",
    "sha256": "<bundle hash>",            // PENDING_CLEAN_ROOM until export.sh fills it
    "size_mb": 103
  },
  "tiers":  { "config": true, "vault": true, "savedata": false, "game_pkg": false },
  "vault":  { "shader_count": 11362, "clean_room": true },
  "tuner":  { "handle": "mercurious", "note": "...", "harvest": { "sessions": 0, "hours": 0, "best_streak": 0 } }
}
```

`PENDING_CLEAN_ROOM` / `0` fields are placeholders filled by `pro-tuning/export.sh --write-index`
after the fresh single-driver compile (no nightly blend — see ProTuningExportDossier §5.2).

## Tags = free partition rotation
One Release tag per `chipset:turnip` epoch. When a new official ROCKNIX bumps Turnip,
cut a new tag (`vault-SM8250-turnip26.2.0`), recompile clean-room vaults into it; the old tag
stays frozen for anyone still on the prior build. The `mesa_hash` gate keeps a rig from loading
the wrong-driver crate.

## Contributing a tune (the "competition wants in" path)
A second tuner joins by **opening a pull request** that adds (or updates) one entry here, with the
bytes uploaded to the matching Release. No accounts, no infra — review the PR, merge, done.
