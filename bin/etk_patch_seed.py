#!/usr/bin/env python3
"""etk_patch_seed.py — deploy a kit per-title patch file and enable its patches ONCE.

WHY (0.10.0): ARMSX3 1.0.8 ships Gran Turismo 6's "Disable MLAA" + "Disable
Motion Blur" (illusion) for every v01.22 executable — with MLAA on, GT6 falls
to 1 fps a few minutes in and wedges the RSX on a label nothing clears. Our
core (ARMSX3 1.0.4 base) loads <TITLE_ID>_patch.yml from the patches dir, so
the kit can ship the file; what it must NOT do is re-enable a patch the user
switched off in Pitstop TUNING > PATCH on every install.

HOW: copy the kit file into the patches dir under its title name (the kit owns
that file: always refreshed). If the seed marker for this serial is absent,
enable every patch the file declares for the serial in RPCS3's own
patch_config.yml (etk_patchlib, the same writer Pitstop uses), add their slugs
to the ledger's patch_pins.tsv, and write the marker. With the marker present
nothing in the enablement is touched. --remove deletes the file, the marker,
and the enablement rows the seed wrote (listed in the marker), leaving any
other patch alone. Fail-soft: never raises for content; exit 1 only when the
file cannot be placed. Prints ONE verdict line for the install log.

    etk_patch_seed.py --serial BCUS98296 --file <kit yml> [--patches-dir D]
                      [--config patch_config.yml] [--pins patch_pins.tsv]
                      [--marker-dir /storage/.config] [--remove]
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import etk_patchlib as pl  # noqa: E402


def _default(env, fallback):
    v = os.environ.get(env, "")
    return v if v else fallback


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--file", required=True, help="the kit's <SERIAL>_patch.yml")
    ap.add_argument("--patches-dir",
                    default=_default("RPCS3_PATCHES_DIR", "/storage/.config/rpcs3/patches"))
    ap.add_argument("--config",
                    default=_default("PATCH_CONFIG_YML", "/storage/.config/rpcs3/patch_config.yml"))
    ap.add_argument("--pins",
                    default=_default("PATCH_PINS_FILE",
                                     os.path.join(_default("TELEMETRY_DIR",
                                                           "/storage/games-internal/roms/etk/etk_telemetry"),
                                                  "patch_pins.tsv")))
    ap.add_argument("--marker-dir", default=_default("ETK_PATCH_SEED_DIR", "/storage/.config"))
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args(argv)

    dest = os.path.join(a.patches_dir, f"{a.serial}_patch.yml")
    marker = os.path.join(a.marker_dir, f"etk_patch_seed.{a.serial}")

    if a.remove:
        removed = []
        if os.path.exists(dest):
            os.remove(dest)
            removed.append("file")
        seeded = []
        if os.path.exists(marker):
            with open(marker) as f:
                seeded = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            os.remove(marker)
            removed.append("marker")
        if seeded and os.path.isfile(a.config):
            try:
                tree, _ = pl.read_patch_config(a.config)
                for row in seeded:
                    parts = row.split("\t")
                    h, d = parts[0], parts[1] if len(parts) > 1 else ""
                    t = parts[2] if len(parts) > 2 else None
                    pl.set_patch_enabled(tree, {"hash": h, "description": d,
                                                "title": t, "versions": []},
                                         a.serial, False)
                pl.write_patch_config(a.config, tree)
                removed.append(f"{len(seeded)} enablement row(s)")
            except Exception as e:  # noqa: BLE001 — fail-soft, say what happened
                removed.append(f"enablement left (read/write failed: {e.__class__.__name__})")
        print(f"PATCH_SEED {a.serial}: removed {', '.join(removed) or 'nothing (not deployed)'}")
        return 0

    if not os.path.isfile(a.file):
        print(f"PATCH_SEED {a.serial}: kit file missing ({a.file}) — nothing deployed")
        return 1
    try:
        os.makedirs(a.patches_dir, exist_ok=True)
        tmp = dest + ".tmp"
        shutil.copyfile(a.file, tmp)
        os.replace(tmp, dest)
    except OSError as e:
        print(f"PATCH_SEED {a.serial}: cannot place {dest} ({e.__class__.__name__}) — nothing deployed")
        return 1

    if os.path.exists(marker):
        print(f"PATCH_SEED {a.serial}: file refreshed; enablement already seeded (your TUNING > PATCH choices stand)")
        return 0

    try:
        found = pl.parse_patch_yml(dest, a.serial)
    except Exception as e:  # noqa: BLE001
        print(f"PATCH_SEED {a.serial}: file deployed but unreadable ({e.__class__.__name__}); nothing enabled")
        return 0
    if not found:
        print(f"PATCH_SEED {a.serial}: file deployed; it declares no patch for {a.serial}; nothing enabled")
        return 0
    try:
        tree, _ = pl.read_patch_config(a.config)
        for p in found:
            pl.set_patch_enabled(tree, p, a.serial, True)
        pl.write_patch_config(a.config, tree)
    except Exception as e:  # noqa: BLE001
        print(f"PATCH_SEED {a.serial}: file deployed; enabling failed ({e.__class__.__name__}) — TUNING > PATCH still works")
        return 0
    # ledger attribution: the launch wrapper stamps patches=<slugs> from the pins
    slugs = sorted({pl.patch_slug(p["description"]) for p in found})
    try:
        existing = ""
        if os.path.isfile(a.pins):
            with open(a.pins) as f:
                for ln in f:
                    parts = ln.rstrip("\n").split("\t")
                    if len(parts) >= 2 and parts[0] == a.serial:
                        existing = parts[1]
        merged = sorted(set([s for s in existing.split(",") if s] + slugs))
        pl.write_patch_pins(a.pins, a.serial, merged)
    except Exception as e:  # noqa: BLE001
        print(f"PATCH_SEED {a.serial}: enabled but pins not written ({e.__class__.__name__})")
    os.makedirs(a.marker_dir, exist_ok=True)
    with open(marker, "w") as f:
        f.write("# hash<TAB>description<TAB>title enabled by etk_patch_seed.py "
                "(one-time); remove this file to re-seed\n")
        for p in found:
            f.write(f"{p['hash']}\t{p['description']}\t{p['title'] or ''}\n")
    names = sorted({p["description"] for p in found})
    print(f"PATCH_SEED {a.serial}: deployed + enabled {', '.join(names)} "
          f"({len(found)} executable-patch pairs); change them in TUNING > PATCH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
