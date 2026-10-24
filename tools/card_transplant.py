#!/usr/bin/env python3
"""ETK Card Transplant — move a big game card into another car without losing either car's newer data.

WHY IT EXISTS (2026-10-23): car8 went out of commission with the full library on
its 1 TB card, and car12 (the new main driver) ran on a bare-bones 256 GB card.
The two cards hold DIFFERENT storage models:

  RECIPIENT card (car12's, the SOURCE here): a ROCKNIX boot card. p1 vfat
    labelled ROCKNIX = /flash (KERNEL boot.img + SYSTEM squashfs + rocknix_abl),
    p2 ext4 labelled STORAGE = all of /storage. The ABL loads /flash/KERNEL and
    the kernel's BAKED-IN cmdline names the two labels (boot=LABEL=… disk=LABEL=…).
  DONOR card (car8's, the DESTINATION here): a crash card (manual §A.2, STEP 6.85).
    car8 booted from its UFS; the card (p1 vfat SDDATA, p2 ext4 SDGAMES) was
    mounted as /storage/games-external, so its ROOT holds car8's games-external
    projects, and its games-internal/ was bound over /storage/games-internal.
    Its root also still carries a whole June-era /storage from when it was a
    boot card (stale .config etc.).

The transplant turns the donor card into the recipient car's boot card in place
— the library never moves, nothing is reformatted:

  boot   donor p1 gets the recipient's /flash payload (every top-level file +
         rocknix_abl/); whatever was in the way (incl. Android's LOST.DIR, where
         car8's card held an orphaned 1.5 GB June SYSTEM) is parked on p2.
  root   the donor's stale storage state is PARKED; its project dirs (car8's
         games-external content) are relocated to games-external/ where the
         car's tools look for them; the recipient's /storage root is copied in.
  games-external   per entry: one side only → it lands; identical subset → the
         donor's copy stays (models: car12's were copies of car8's); otherwise
         the side with the newer newest-file wins WHOLE, the other is parked.
  games-internal   etk/ = the recipient's kit tree (the donor's is parked whole:
         car8's ledger/telemetry/vault stay intact); RPCS3 save dirs are judged
         as UNITS (newest file wins the whole dir — files of two saves never
         mix); gamelist.xml = the donor's (the library's metadata), the
         recipient's are stashed; everything else merges by content (rsync -c),
         and where content differs the newer file wins, the replaced one is
         kept under the park dir.
  labels last: p1 → the KERNEL cmdline's boot label, p2 → its disk label.

NOTHING IS DELETED. Losers go to /storage/parked-<donor>-<date>/ on the same
card (renames: instant, zero bytes). The source card is mounted READ-ONLY
(ext4 `ro,noload`) and refused if it was not shut down cleanly. The plan is
persisted in the destination's marker (/.etk_transplant.json) at the first
apply, so an interrupted apply resumes the SAME plan, skipping finished steps.

COMMANDS (each writes state/card_transplant/<runid>/{plan.json,plan.md[,apply.log]}):

  plan  --src DEV --dst DEV --car NAME --donor NAME   (no root) read both cards
        through their desktop (udisks) mounts and print what apply will do,
        with the space check. Root-only dirs read as "unreadable" (sizes are
        then under-estimated; apply re-plans as root).
  apply --src DEV --dst DEV --car NAME --donor NAME   (ROOT — the operator runs
        it) unmount, fsck the destination, mount src ro / dst rw, execute,
        verify, unmount, relabel, read the labels back. VERDICT line at the end.

  DEV is the whole disk (/dev/sdX), never a partition. One card reader with two
  slots is the expected rig: car12 powered off from ES, its card in slot 2.

THE SURFACE is not this tool's verdict: it is the recipient car cold-booting the
transplanted card — the logo, ES with the full library, the newer saves in game,
and `tools/abl_slot.sh verify <car> --car <car>`.

Harness: tools/test_card_transplant.py (temp-dir cards, no root).
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KEEP_ROOT = {"games-internal", "games-external", "lost+found"}
# A donor root entry that is ROCKNIX's / Android's own storage furniture is
# storage STATE (parked); any other non-hidden dir is a games-external project.
STORAGE_FURNITURE = {"assets", "backup", "cores", "database", "joypads", "overlays",
                     "remappings", "shaders", "roms"}
ANDROID_DIRS = {"Alarms", "Android", "Audiobooks", "DCIM", "Documents", "Download",
                "LOST.DIR", "Movies", "Music", "Notifications", "Pictures", "Podcasts",
                "Recordings", "Ringtones"}
BOOT_PAYLOAD_DIRS = {"rocknix_abl", "EFI", "boot", "overlays"}
MARKER = ".etk_transplant.json"
GI, GE = "games-internal", "games-external"
SAVEDATA_PARENT = re.compile(r"^games-internal/roms/bios/rpcs3/dev_hdd0/home/[^/]+/savedata$")
ETK_REL = "games-internal/roms/etk"
DEFAULT_LABELS = ("ROCKNIX", "STORAGE")
SPACE_MARGIN = 2 << 30          # 2 GiB headroom on the data partition
BOOT_MARGIN = 16 << 20          # 16 MiB headroom on the boot partition
RSYNC = ["rsync", "-aH", "--numeric-ids"]
ROOTS = ("sb", "ss", "db", "ds")   # src boot, src storage, dst boot, dst storage
MOUNT_BASE = "/run/etk-transplant"

LOG = None


def say(msg=""):
    print(msg, flush=True)
    if LOG:
        LOG.write(msg + "\n")
        LOG.flush()


def repo_root():
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- tree helpers

def walk_files(top):
    """{relpath: (size, mtime)} for every non-dir entry under top (lstat; symlinks count 0)."""
    out, unreadable = {}, [0]
    top = Path(top)
    if top.is_file() or top.is_symlink():
        st = top.lstat()
        return {"": (st.st_size if top.is_file() else 0, int(st.st_mtime))}, 0

    def err(_e):
        unreadable[0] += 1
    for d, dirs, files in os.walk(top, onerror=err):
        for f in files:
            p = os.path.join(d, f)
            try:
                st = os.lstat(p)
            except OSError:
                unreadable[0] += 1
                continue
            size = st.st_size if not os.path.islink(p) else 0
            out[os.path.relpath(p, top)] = (size, int(st.st_mtime))
    return out, unreadable[0]


def tree_bytes(top):
    files, bad = walk_files(top)
    return sum(s for s, _ in files.values()), bad


def newest(top):
    files, _ = walk_files(top)
    if files:
        return max(t for _, t in files.values())
    return int(Path(top).lstat().st_mtime)


def sha256(path, bufsize=4 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def subset_of(src, dst, verify):
    """Every file under src exists under dst with the same size (and, with verify, the same sha256)."""
    sf, _ = walk_files(src)
    df, _ = walk_files(dst)
    for rel, (size, _) in sf.items():
        if rel not in df or df[rel][0] != size:
            return False
    if verify:
        for rel in sf:
            a, b = Path(src, rel), Path(dst, rel)
            if a.is_symlink() or b.is_symlink():
                if not (a.is_symlink() and b.is_symlink() and os.readlink(a) == os.readlink(b)):
                    return False
            elif sha256(a) != sha256(b):
                return False
    return True


def same_tree(src, dst, verify):
    return subset_of(src, dst, verify) and subset_of(dst, src, False)


def kernel_labels(kernel):
    """(boot_label, disk_label) from a boot.img's baked cmdline, or the ROCKNIX defaults."""
    try:
        head = Path(kernel).read_bytes()[:8192]
    except OSError:
        return DEFAULT_LABELS, False
    b = re.search(rb"boot=LABEL=([A-Za-z0-9_-]+)", head)
    d = re.search(rb"disk=LABEL=([A-Za-z0-9_-]+)", head)
    if b and d:
        return (b.group(1).decode(), d.group(1).decode()), True
    return DEFAULT_LABELS, False


def hfmt(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


# ---------------------------------------------------------------- the plan

def A(kind, why, a=None, b=None, nbytes=0, **kw):
    act = {"kind": kind, "why": why, "a": a, "b": b, "bytes": nbytes}
    act.update(kw)
    return act


def build_plan(roots, car, donor, park, verify):
    """The ordered action list. Paths are [root-tag, relpath]; roots maps tag → mount dir."""
    sb, ss, db, ds = (Path(roots[t]) for t in ROOTS)
    acts, notes = [], []
    P = park

    # --- boot partition
    payload = []
    for e in sorted(os.listdir(sb)):
        p = sb / e
        if (p.is_file() and not e.startswith(".")) or (p.is_dir() and e in BOOT_PAYLOAD_DIRS):
            payload.append(e)
    if "KERNEL" not in payload or "SYSTEM" not in payload:
        raise SystemExit(f"REFUSED: the source boot partition has no KERNEL/SYSTEM ({sb}) — not a ROCKNIX boot card")
    lost = db / "LOST.DIR"
    if lost.is_dir():
        for e in sorted(os.listdir(lost)):
            n, _ = tree_bytes(lost / e)
            acts.append(A("xpark", "Android LOST.DIR orphan on the boot partition (frees room for SYSTEM)",
                          ["db", f"LOST.DIR/{e}"], ["ds", f"{P}/boot/LOST.DIR/{e}"], n))
    for e in payload:
        if (db / e).exists():
            n, _ = tree_bytes(db / e)
            acts.append(A("xpark", "donor boot entry in the way of the payload",
                          ["db", e], ["ds", f"{P}/boot/{e}"], n, guard_payload=e))
    for e in payload:
        n, _ = tree_bytes(sb / e)
        acts.append(A("bootcopy", f"{car} /flash payload", ["sb", e], ["db", e], n, fs="boot"))

    # --- storage root: park the donor's state, relocate its projects
    projects = []
    for e in sorted(os.listdir(ds)):
        if e in KEEP_ROOT or e == P or e == MARKER:
            continue
        p = ds / e
        if not e.startswith(".") and p.is_dir() and not p.is_symlink() \
                and e not in STORAGE_FURNITURE and e not in ANDROID_DIRS:
            projects.append(e)
    proj_files = set()
    for e in sorted(os.listdir(ds)):
        if (ds / e).is_file() and not e.startswith(".") and any(
                e.startswith(pr + "-") or e.startswith(pr + ".") for pr in projects):
            proj_files.add(e)
    relocated = set()
    for e in sorted(os.listdir(ds)):
        if e in KEEP_ROOT or e == P or e == MARKER:
            continue
        if e in projects or e in proj_files:
            if (ds / GE / e).exists():
                acts.append(A("park", f"{donor} project, games-external/{e} already taken",
                              ["ds", e], ["ds", f"{P}/root/{e}"]))
            else:
                acts.append(A("relocate", f"{donor} games-external project → its games-external home",
                              ["ds", e], ["ds", f"{GE}/{e}"]))
                relocated.add(e)
        else:
            acts.append(A("park", f"{donor}'s stale storage state (the {car} copy takes over)",
                          ["ds", e], ["ds", f"{P}/root/{e}"]))

    # --- games-external: per top-level entry
    sge = ss / GE
    if sge.is_dir():
        for e in sorted(os.listdir(sge)):
            src = sge / e
            if (ds / GE / e).exists():
                cur = ds / GE / e
            elif e in relocated:
                cur = ds / e
            else:
                cur = None
            n, _ = tree_bytes(src)
            if cur is None:
                acts.append(A("copy", f"{car} only", ["ss", f"{GE}/{e}"], ["ds", f"{GE}/{e}"], n))
            elif subset_of(src, cur, verify):
                acts.append(A("keep", f"{car}'s copy is an identical subset of {donor}'s ({'sha256' if verify else 'by size'})",
                              ["ss", f"{GE}/{e}"], ["ds", f"{GE}/{e}"], 0))
            elif newest(src) >= newest(cur):
                acts.append(A("park", f"{car}'s {e} is newer (whole entry)", ["ds", f"{GE}/{e}"],
                              ["ds", f"{P}/{GE}/{e}"]))
                acts.append(A("copy", f"{car}'s {e} is newer", ["ss", f"{GE}/{e}"], ["ds", f"{GE}/{e}"], n))
            else:
                acts.append(A("copy", f"{donor}'s {e} is newer — {car}'s stashed", ["ss", f"{GE}/{e}"],
                              ["ds", f"{P}/from-{car}/{GE}/{e}"], n))

    # --- the recipient's /storage root
    for e in sorted(os.listdir(ss)):
        if e in KEEP_ROOT or e == MARKER:
            continue
        n, bad = tree_bytes(ss / e)
        acts.append(A("copy", f"{car} /storage root", ["ss", e], ["ds", e], n, unreadable=bad))

    # --- games-internal
    excludes = []
    if (ss / ETK_REL).is_dir():
        if (ds / ETK_REL).exists():
            acts.append(A("park", f"{donor}'s kit tree + ledger/telemetry/vault, kept whole",
                          ["ds", ETK_REL], ["ds", f"{P}/{ETK_REL}"]))
        n, _ = tree_bytes(ss / ETK_REL)
        acts.append(A("copy", f"{car}'s kit tree (ETK_ROOT)", ["ss", ETK_REL], ["ds", ETK_REL], n))
        excludes.append("/roms/etk")
    for d, dirs, _files in os.walk(ss / GI):
        rel = os.path.relpath(d, ss)
        if not SAVEDATA_PARENT.match(rel):
            continue
        for s in sorted(dirs):
            srel = f"{rel}/{s}"
            if not (ds / srel).is_dir():
                continue
            if same_tree(ss / srel, ds / srel, verify):
                continue
            ns, nd = newest(ss / srel), newest(ds / srel)
            n, _ = tree_bytes(ss / srel)
            if ns >= nd:
                acts.append(A("park", f"save {s}: {car} newer ({_ts(ns)} vs {_ts(nd)}), whole dir",
                              ["ds", srel], ["ds", f"{P}/{srel}"]))
            else:
                acts.append(A("copy", f"save {s}: {donor} newer ({_ts(nd)} vs {_ts(ns)}) — {car}'s stashed",
                              ["ss", srel], ["ds", f"{P}/from-{car}/{srel}"], n))
                excludes.append("/" + _rsync_escape(os.path.relpath(srel, GI)))
        dirs[:] = []
    for d, _dirs, files in os.walk(ss / GI):
        if "gamelist.xml" in files:
            rel = os.path.relpath(os.path.join(d, "gamelist.xml"), ss)
            n = (ss / rel).stat().st_size
            acts.append(A("copy", "gamelist stash", ["ss", rel], ["ds", f"{P}/from-{car}/{rel}"], n))
            if not (ds / rel).exists():
                acts.append(A("copy", f"no {donor} gamelist here", ["ss", rel], ["ds", rel], n))
    excludes.append("gamelist.xml")
    est, bad = _merge_estimate(ss / GI, ds / GI, excludes)
    acts.append(A("merge", f"{car} games-internal by content; newer wins, replaced files kept",
                  ["ss", GI], ["ds", GI], est, excludes=excludes,
                  backup=f"{P}/replaced/{GI}", unreadable=bad))
    return acts, notes


def _ts(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def _rsync_escape(rel):
    return re.sub(r"([\[\]*?\\])", r"\\\1", rel)


def _merge_estimate(src, dst, excludes):
    """Bytes rsync will write: files absent on dst or differing in size (content check happens in apply)."""
    sf, bad = walk_files(src)
    total = 0
    skip = [e.lstrip("/") for e in excludes if e.startswith("/")]
    for rel, (size, _) in sf.items():
        if os.path.basename(rel) == "gamelist.xml" or any(rel == s or rel.startswith(s.replace("\\", "") + "/") for s in skip):
            continue
        p = dst / rel
        try:
            if p.lstat().st_size == size:
                continue
        except OSError:
            pass
        total += size
    return total, bad


# ---------------------------------------------------------------- execution

def rp(roots, ref):
    tag, rel = ref
    return Path(roots[tag]) / rel if rel else Path(roots[tag])


def run(cmd):
    say("    $ " + " ".join(cmd))
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in r.stdout.splitlines()[-20:]:
        say("      " + line)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} exited {r.returncode}")


def do_action(roots, act):
    k = act["kind"]
    a = rp(roots, act["a"]) if act["a"] else None
    b = rp(roots, act["b"]) if act["b"] else None
    if k == "keep":
        return
    if k in ("park", "relocate"):                        # renames on the data partition: instant
        if not a.exists() and not a.is_symlink():
            if b.exists() or b.is_symlink():
                return                                  # resumed: already moved
            raise RuntimeError(f"park: {a} is gone and {b} does not exist")
        if b.exists() or b.is_symlink():
            raise RuntimeError(f"park: {b} already exists — refusing to merge two parked copies")
        b.parent.mkdir(parents=True, exist_ok=True)
        os.rename(a, b)
        return
    if k == "xpark":                                    # boot partition → data partition: copy, verify, remove
        if act.get("guard_payload") and a.exists() and same_tree(rp(roots, ["sb", act["guard_payload"]]), a, True):
            return                                      # resumed: this IS the payload already copied
        if not a.exists():
            if b.exists():
                return
            raise RuntimeError(f"xpark: {a} is gone and {b} does not exist")
        b.parent.mkdir(parents=True, exist_ok=True)
        if a.is_dir():
            run(RSYNC[:1] + ["-rt", str(a) + "/", str(b) + "/"])
        else:
            run(RSYNC[:1] + ["-t", str(a), str(b)])
        if not subset_of(a, b, True):
            raise RuntimeError(f"xpark: copy of {a} did not verify")
        shutil.rmtree(a) if a.is_dir() else a.unlink()
        return
    if k == "bootcopy":                                 # vfat: no owners; sha-verified
        if a.is_dir():
            run(RSYNC[:1] + ["-rt", "--modify-window=2", str(a) + "/", str(b) + "/"])
        else:
            run(RSYNC[:1] + ["-t", "--modify-window=2", str(a), str(b)])
        os.sync()
        if not subset_of(a, b, True):
            raise RuntimeError(f"bootcopy: {b} does not match {a} (sha256)")
        return
    if k == "copy":
        b.parent.mkdir(parents=True, exist_ok=True)
        if a.is_dir() and not a.is_symlink():
            run(RSYNC + [str(a) + "/", str(b) + "/"])
        else:
            run(RSYNC + [str(a), str(b)])
        return
    if k == "merge":
        backup = Path(roots["ds"]) / act["backup"]
        cmd = RSYNC + ["-c", "--update", "--backup", f"--backup-dir={backup}"]
        cmd += [f"--exclude={e}" for e in act["excludes"]]
        run(cmd + [str(a) + "/", str(b) + "/"])
        return
    raise RuntimeError(f"unknown action {k}")


def check_space(roots, acts):
    need = {"boot": 0, "data": 0}
    freed_boot = 0
    for act in acts:
        if act.get("done"):
            continue
        if act["kind"] == "bootcopy":
            need["boot"] += act["bytes"]
        elif act["kind"] in ("copy", "merge", "xpark"):
            need["data"] += act["bytes"]
        if act["kind"] == "xpark":
            freed_boot += act["bytes"]
    fb = _free(roots["db"]) + freed_boot
    fd = _free(roots["ds"])
    ok = need["boot"] + BOOT_MARGIN <= fb and need["data"] + SPACE_MARGIN <= fd
    return ok, need, fb, fd


def _free(p):
    st = os.statvfs(p)
    return st.f_bavail * st.f_frsize


def render(acts, car, donor, park, space, labels, labels_ok, out):
    ok, need, fb, fd = space
    lines = [f"# Card transplant plan — {donor}'s card becomes {car}'s boot card", "",
             f"park dir: /storage/{park}/ (nothing is deleted)",
             f"labels after: p1 → {labels[0]}, p2 → {labels[1]} "
             f"({'from the KERNEL baked cmdline' if labels_ok else 'DEFAULT — no LABEL= in KERNEL'})", ""]
    cur = None
    for act in acts:
        sect = _section(act)
        if sect != cur:
            lines += ["", f"## {sect}"]
            cur = sect
        a = "/".join(x for x in act["a"] if x) if act["a"] else ""
        b = "/".join(x for x in act["b"] if x) if act["b"] else ""
        extra = f"  [{hfmt(act['bytes'])}]" if act["bytes"] else ""
        if act.get("unreadable"):
            extra += f"  ({act['unreadable']} unreadable without root)"
        done = "  ✓done" if act.get("done") else ""
        lines.append(f"- {act['kind']:8} {a}  →  {b}{extra}{done}\n    {act['why']}")
    lines += ["", "## Space",
              f"- boot partition: need {hfmt(need['boot'])}, free after parking {hfmt(fb)}",
              f"- data partition: need ≈{hfmt(need['data'])}, free {hfmt(fd)} (margin {hfmt(SPACE_MARGIN)})",
              f"- {'OK' if ok else 'INSUFFICIENT'}"]
    text = "\n".join(lines) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.md").write_text(text)
    (out / "plan.json").write_text(json.dumps({"car": car, "donor": donor, "park": park,
                                                "labels": labels, "actions": acts}, indent=1))
    return text


def _section(act):
    if act["kind"] in ("park", "relocate") and act["a"][0] == "ds" and "/" not in act["a"][1]:
        return "/storage root — the donor's entries"
    if act["kind"] == "copy" and act["a"][0] == "ss" and "/" not in act["a"][1]:
        return "/storage root — the recipient's copy"
    for ref in (act["b"], act["a"]):
        if not ref:
            continue
        tag, rel = ref
        if tag in ("db", "sb") or rel.startswith("parked-") and "/boot/" in rel:
            return "Boot partition (/flash)"
    rel = act["b"][1] if act["b"] else ""
    src = act["a"][1] if act["a"] else ""
    if src.startswith(GI) or rel.startswith(GI) or "/" + GI in rel:
        return "games-internal (library, saves, kit)"
    if src.startswith(GE) or rel.startswith(GE) or "/" + GE + "/" in rel:
        return "games-external (projects)"
    return "/storage root"


# ---------------------------------------------------------------- devices (root)

def lsblk(dev):
    r = subprocess.run(["lsblk", "-J", "-b", "-o", "PATH,TYPE,SIZE,FSTYPE,LABEL,PARTLABEL,MOUNTPOINTS", dev],
                       stdout=subprocess.PIPE, text=True, check=True)
    devs = json.loads(r.stdout)["blockdevices"]
    disk = devs[0]
    if disk["type"] != "disk":
        raise SystemExit(f"REFUSED: {dev} is a {disk['type']}, pass the whole card (/dev/sdX)")
    # Without a NAME column lsblk prints a FLAT list (partitions beside the disk), with it a tree.
    parts = disk.get("children") or [d for d in devs[1:] if d["type"] == "part"]
    parts = sorted(parts, key=lambda p: p["path"])
    if len(parts) != 2 or parts[0]["fstype"] != "vfat" or parts[1]["fstype"] != "ext4":
        desc = ", ".join(f"{p['path']}={p['fstype']}" for p in parts) or "no partitions"
        raise SystemExit(f"REFUSED: {dev} is not a vfat+ext4 ROCKNIX-layout card ({desc})")
    return parts


def mounts_of(part):
    return [m for m in (part.get("mountpoints") or []) if m]


def needs_recovery(dev):
    r = subprocess.run(["dumpe2fs", "-h", dev], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    m = re.search(r"^Filesystem features:\s*(.*)$", r.stdout, re.M)
    return bool(m and "needs_recovery" in m.group(1).split())


def device_roots(args, mount):
    """Map the two cards to the four roots. mount=False: use the desktop mounts as they are."""
    if os.path.realpath(args.src) == os.path.realpath(args.dst):
        raise SystemExit("REFUSED: --src and --dst are the same device")
    sp, dp = lsblk(args.src), lsblk(args.dst)
    if not mount:
        roots = {}
        for tag, part in zip(ROOTS, sp + dp):
            m = mounts_of(part)
            if not m:
                raise SystemExit(f"{part['path']} is not mounted — open it in the file manager (udisks), or run apply as root")
            roots[tag] = m[0]
        return roots, None, (sp, dp)
    for part in sp + dp:
        for m in mounts_of(part):
            say(f"  unmount {part['path']} ({m})")
            subprocess.run(["umount", m], check=True)
    # Only AFTER the unmount: a mounted-rw ext4 always carries needs_recovery (2026-10-23: the
    # desktop's own mount of a cleanly shut-down card read as "not shut down cleanly").
    if needs_recovery(sp[1]["path"]):
        raise SystemExit(f"REFUSED: {sp[1]['path']} was not shut down cleanly (ext4 needs_recovery). "
                         f"Put it back in the car, boot it, and power off from ES.")
    say("  fsck the destination (preen: safe automatic fixes only)")
    r = subprocess.run(["fsck.vfat", "-a", "-w", dp[0]["path"]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    say("    " + r.stdout.strip().replace("\n", "\n    "))
    if r.returncode not in (0, 1):
        raise SystemExit(f"REFUSED: fsck.vfat {dp[0]['path']} exited {r.returncode}")
    r = subprocess.run(["e2fsck", "-f", "-p", dp[1]["path"]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    say("    " + r.stdout.strip().replace("\n", "\n    "))
    if r.returncode not in (0, 1):
        raise SystemExit(f"REFUSED: e2fsck -p {dp[1]['path']} exited {r.returncode} — needs a manual fsck first")
    base = Path(MOUNT_BASE)
    ctx = []
    if Path("/sys/fs/selinux/enforce").exists():
        ctx = ["-o", "context=system_u:object_r:removable_t:s0"]   # no security.* xattrs written onto the card
    plan = [("sb", sp[0]["path"], ["-o", "ro"]), ("ss", sp[1]["path"], ["-o", "ro,noload"]),
            ("db", dp[0]["path"], ["-o", "utf8,shortname=mixed"]), ("ds", dp[1]["path"], ctx)]
    roots, mounted = {}, []
    for tag, part, opts in plan:
        d = base / tag
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run(["mount"] + opts + [part, str(d)], check=True)
        mounted.append(str(d))
        roots[tag] = str(d)
    return roots, mounted, (sp, dp)


def unmount_all(mounted):
    os.sync()
    for d in reversed(mounted or []):
        subprocess.run(["umount", d])


def relabel(dp, labels):
    subprocess.run(["fatlabel", dp[0]["path"], labels[0]], check=True)
    subprocess.run(["e2label", dp[1]["path"], labels[1]], check=True)
    got = []
    for part in dp:
        r = subprocess.run(["blkid", "-p", "-o", "value", "-s", "LABEL", part["path"]],
                           stdout=subprocess.PIPE, text=True)
        got.append(r.stdout.strip())
    return got


# ---------------------------------------------------------------- verify

def verify(roots, acts, car):
    fails = []
    for act in acts:
        if act["kind"] == "bootcopy" and not subset_of(rp(roots, act["a"]), rp(roots, act["b"]), True):
            fails.append(f"boot payload {act['a'][1]} differs")
    db = Path(roots["db"])
    md5 = db / "SYSTEM.md5"
    if md5.exists():
        want = md5.read_text().split()[0]
        h = hashlib.md5()
        with open(db / "SYSTEM", "rb") as f:
            for blk in iter(lambda: f.read(4 << 20), b""):
                h.update(blk)
        if h.hexdigest() != want:
            fails.append("SYSTEM does not match SYSTEM.md5")
    ss, ds = Path(roots["ss"]), Path(roots["ds"])
    for act in acts:
        if act["kind"] == "park" and act["a"][1].startswith(GI) and "/savedata/" in act["a"][1]:
            rel = act["a"][1]
            if not same_tree(ss / rel, ds / rel, True):
                fails.append(f"save {rel.rsplit('/', 1)[-1]}: the {car} copy is not in place")
    if (ss / ETK_REL).is_dir() and not same_tree(ss / ETK_REL, ds / ETK_REL, False):
        fails.append("kit tree (etk) incomplete")
    if (ss / ".config").is_dir() and not (ds / ".config").is_dir():
        fails.append(".config missing at the new /storage root")
    return fails


# ---------------------------------------------------------------- commands

def main(argv=None):
    global LOG
    ap = argparse.ArgumentParser(prog="card_transplant.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--src", help="the RECIPIENT car's boot card (whole disk, e.g. /dev/sdb)")
        p.add_argument("--dst", help="the DONOR card that becomes its boot card (whole disk)")
        p.add_argument("--car", required=True, help="the recipient car (e.g. car12)")
        p.add_argument("--donor", required=True, help="the car whose card this was (e.g. car8)")
        p.add_argument("--out", default=str(repo_root() / "state" / "card_transplant"))
        p.add_argument("--dirs", nargs=4, metavar=("SRC_BOOT", "SRC_STORAGE", "DST_BOOT", "DST_STORAGE"),
                       help=argparse.SUPPRESS)      # harness: four plain directories, no devices
        p.add_argument("--date", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if not args.dirs and not (args.src and args.dst):
        ap.error("--src and --dst are required")

    runid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / runid
    out.mkdir(parents=True, exist_ok=True)
    apply = args.cmd == "apply"
    if apply and not args.dirs and os.geteuid() != 0:
        raise SystemExit("apply needs root: sudo tools/card_transplant.py apply …")
    LOG = open(out / ("apply.log" if apply else "plan.log"), "w")
    mounted, parts = None, None
    try:
        if args.dirs:
            roots = dict(zip(ROOTS, args.dirs))
        else:
            roots, mounted, parts = device_roots(args, mount=apply)
        say(f"roots: " + "  ".join(f"{t}={roots[t]}" for t in ROOTS))
        labels, labels_ok = kernel_labels(Path(roots["sb"]) / "KERNEL")
        marker = Path(roots["ds"]) / MARKER
        state = json.loads(marker.read_text()) if marker.exists() else None
        if state and state.get("state") == "done":
            raise SystemExit(f"REFUSED: this card was already transplanted ({marker}, {state.get('finished')})")
        if not state and parts and parts[1][0]["label"] == labels[0] and parts[1][1]["label"] == labels[1]:
            raise SystemExit("REFUSED: the destination already carries the boot labels and no transplant marker — wrong card?")
        if state:
            acts, park = state["actions"], state["park"]
            say(f"RESUMING the plan from {state['started']} ({sum(1 for a in acts if a.get('done'))}/{len(acts)} done)")
        else:
            date = args.date or datetime.date.today().strftime("%Y%m%d")
            park = f"parked-{args.donor}-{date}"
            acts, _ = build_plan(roots, args.car, args.donor, park, verify=apply)
        space = check_space(roots, acts)
        text = render(acts, args.car, args.donor, park, space, labels, labels_ok, out)
        say(text)
        if not apply:
            say(f"plan written: {out / 'plan.md'}")
            return 0 if space[0] else 2
        if not space[0]:
            raise SystemExit("REFUSED: not enough space (see the Space section)")
        if not state:
            state = {"state": "in-progress", "car": args.car, "donor": args.donor, "park": park,
                     "labels": labels, "started": runid, "actions": acts}
            marker.write_text(json.dumps(state, indent=1))
        for i, act in enumerate(acts):
            if act.get("done"):
                continue
            a = "/".join(x for x in act["a"] if x) if act["a"] else ""
            say(f"[{i + 1}/{len(acts)}] {act['kind']} {a} → {'/'.join(act['b']) if act['b'] else ''}")
            do_action(roots, act)
            act["done"] = True
            marker.write_text(json.dumps(state, indent=1))
        say("verify …")
        fails = verify(roots, acts, args.car)
        state["state"] = "done" if not fails else "verify-failed"
        state["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
        state["verify"] = fails
        marker.write_text(json.dumps(state, indent=1))
        if fails:
            for f in fails:
                say(f"  FAIL {f}")
            say("VERDICT: VERIFY FAILED — labels NOT changed; the card is still the donor's. Fix and re-run apply.")
            return 1
        if mounted is not None:
            unmount_all(mounted)
            mounted = None
            got = relabel(parts[1], labels)
            if got != list(labels):
                say(f"VERDICT: RELABEL FAILED — read back {got}, wanted {list(labels)}")
                return 1
            say(f"  labels read back: p1={got[0]} p2={got[1]}")
        say(f"VERDICT: TRANSPLANT OK — {args.donor}'s card is now {args.car}'s boot card "
            f"(parked: /storage/{park}/). Next: card into {args.car}, cold boot.")
        return 0
    finally:
        if mounted:
            unmount_all(mounted)
        if LOG:
            LOG.close()
            LOG = None
        uid = os.environ.get("SUDO_UID")
        if uid:
            for p in [out.parent, out] + list(out.iterdir()):
                try:
                    os.chown(p, int(uid), int(os.environ.get("SUDO_GID", uid)))
                except OSError:
                    pass


if __name__ == "__main__":
    sys.exit(main())
