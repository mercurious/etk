#!/usr/bin/env python3
"""test_patch_seed.py — pins bin/etk_patch_seed.py (the GT6 bundled-patch seed, 0.10.0).

WHY: the seed must enable the kit's GT6 patches ONCE and then keep its hands
off — a user who switches "Disable MLAA" off in TUNING > PATCH must not find
it back on after the next install — and --remove must take out exactly what
the seed wrote, nothing a user enabled.

HOW: temp dirs stand in for the rig's patches dir, patch_config.yml, the
pins TSV and the marker dir; the shipped config/patches/BCUS98296_patch.yml
is the input. No rig, no network.

DISCRIMINATION: `--against <rev>` takes the tool from that revision; at
456b078 the tool does not exist and every case FAILS.

    python3 tools/test_patch_seed.py
    python3 tools/test_patch_seed.py --against 456b078
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
import etk_patchlib as pl  # noqa: E402

PASS = FAIL = 0


def ok(m):
    global PASS
    PASS += 1
    print(f"  PASS  {m}")


def bad(m):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {m}")


def check(m, cond):
    ok(m) if cond else bad(m)


def main():
    rev = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--against" else ""
    td = tempfile.mkdtemp()
    bindir = os.path.join(td, "bin")
    os.makedirs(bindir)
    shutil.copy(os.path.join(ROOT, "bin", "etk_patchlib.py"), bindir)
    tool = os.path.join(bindir, "etk_patch_seed.py")
    if rev:
        r = subprocess.run(["git", "-C", ROOT, "show", f"{rev}:bin/etk_patch_seed.py"],
                           capture_output=True, text=True)
        open(tool, "w").write(r.stdout if r.returncode == 0 else "")
    else:
        shutil.copy(os.path.join(ROOT, "bin", "etk_patch_seed.py"), tool)
    kit = os.path.join(ROOT, "config", "patches", "BCUS98296_patch.yml")
    pdir = os.path.join(td, "patches")
    cfg = os.path.join(td, "patch_config.yml")
    pins = os.path.join(td, "patch_pins.tsv")
    mdir = os.path.join(td, "marker")

    def run(*extra):
        r = subprocess.run([sys.executable, "-I", tool, "--serial", "BCUS98296",
                            "--file", kit, "--patches-dir", pdir, "--config", cfg,
                            "--pins", pins, "--marker-dir", mdir, *extra],
                           capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    dest = os.path.join(pdir, "BCUS98296_patch.yml")
    marker = os.path.join(mdir, "etk_patch_seed.BCUS98296")

    # 1. first run: deploy + enable + pins + marker
    rc, out = run()
    check("first run exits 0", rc == 0)
    check("file deployed under the title name", os.path.isfile(dest))
    check("verdict names both patches",
          "Disable MLAA" in out and "Disable Motion Blur" in out)
    tree, _ = pl.read_patch_config(cfg) if os.path.isfile(cfg) else ({}, 0)
    found = pl.parse_patch_yml(dest, "BCUS98296") if os.path.isfile(dest) else []
    check("the kit file declares patches for BCUS98296 (two executables x two patches)",
          len(found) == 4)
    check("every declared patch is enabled for BCUS98296",
          bool(found) and all(pl.patch_is_enabled(tree, p, "BCUS98296") for p in found))
    check("no other serial was touched",
          not any(s != "BCUS98296" for h in tree.values() for d in h.values()
                  for t in d.values() for s in t))
    pins_row = ""
    if os.path.isfile(pins):
        for ln in open(pins):
            if ln.startswith("BCUS98296\t"):
                pins_row = ln.split("\t")[1].strip()
    check("ledger pins carry both slugs", set(pins_row.split(",")) == {"disable_mlaa", "disable_motion_blur"})
    check("marker written", os.path.isfile(marker))

    # 2. the user switches MLAA off; a second run must respect it
    if found:
        mlaa = [p for p in found if p["description"] == "Disable MLAA"]
        for p in mlaa:
            pl.set_patch_enabled(tree, p, "BCUS98296", False)
        pl.write_patch_config(cfg, tree)
    rc, out = run()
    tree2, _ = pl.read_patch_config(cfg)
    check("second run exits 0 and says the seed stands", rc == 0 and "already seeded" in out)
    check("a patch the user switched off STAYS off",
          bool(found) and not any(pl.patch_is_enabled(tree2, p, "BCUS98296")
                                  for p in found if p["description"] == "Disable MLAA"))
    check("the other patch stays on",
          bool(found) and all(pl.patch_is_enabled(tree2, p, "BCUS98296")
                              for p in found if p["description"] == "Disable Motion Blur"))

    # 3. a user-enabled unrelated patch survives --remove
    if found:
        pl.set_patch_enabled(tree2, {"hash": "PPU-deadbeef", "description": "User Patch",
                                     "title": "Other", "versions": ["01.00"]}, "BCUS98296", True)
        pl.write_patch_config(cfg, tree2)
    rc, out = run("--remove")
    tree3, _ = pl.read_patch_config(cfg)
    check("--remove exits 0", rc == 0)
    check("--remove deletes the file and the marker",
          not os.path.exists(dest) and not os.path.exists(marker))
    check("--remove clears the seeded enablement",
          not any(pl.patch_is_enabled(tree3, p, "BCUS98296") for p in found) if found else False)
    check("--remove leaves a user's own patch enabled",
          pl.patch_is_enabled(tree3, {"hash": "PPU-deadbeef", "description": "User Patch",
                                      "title": "Other"}, "BCUS98296"))

    # 4. re-seed after remove works again (marker gone)
    rc, out = run()
    check("re-seed after --remove enables again", rc == 0 and "enabled" in out)

    # 5. missing kit file is a named failure, not a crash
    r = subprocess.run([sys.executable, "-I", tool, "--serial", "X", "--file",
                        os.path.join(td, "nope.yml"), "--patches-dir", pdir,
                        "--config", cfg, "--pins", pins, "--marker-dir", mdir],
                       capture_output=True, text=True)
    check("missing kit file -> exit 1 with a reason",
          r.returncode == 1 and "missing" in r.stdout)

    shutil.rmtree(td, ignore_errors=True)
    print(f"test_patch_seed: {PASS} passed, {FAIL} failed{f' (against {rev})' if rev else ''}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
