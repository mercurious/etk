#!/usr/bin/env python3
"""ETK — host-side regression tests for the Private Paddock sync engine.

Run from the repo root:   python3 tools/test_paddock.py

Covers bin/paddock_sync.sh's restore path, sourced in PADDOCK_LIB=1 mode so
no credential, network or rig is involved.

The two bugs these exist to prevent (both found live 2026-07-27, on a fresh
rig pulling GT HD Concept):

  1. SHADERS. The merge used `tar -xkf`. BusyBox's -k does NOT skip existing
     files and carry on — it ABORTS the whole extraction at the FIRST one.
     The vault held shaders compiled minutes earlier during a pad test, so a
     7,201-shader bundle landed 67 files and stopped. `2>/dev/null` hid the
     error and the summary reported the vault total as if it were a success.

  2. SAVES. The restore was a no-clobber skip. Launching the game even once
     makes RPCS3 write a fresh <ID>-GAME- stub, and that stub then blocked
     the real backed-up career save from ever landing — while the 10 REPLAY
     dirs, absent locally, restored fine. The one save that mattered was the
     one silently skipped.

These run against the REAL functions. NOTE the host's tar is NOT BusyBox —
GNU/bsdtar treat -k as "skip existing and carry on", so the broken code PASSES
a naive host test. Section [1d] therefore puts a BusyBox-semantics `tar` shim
on PATH; that is the section that actually discriminates (verified: the old
`tar -xkf` engine fails exactly those checks, the shipped one passes).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.environ.get("PADDOCK_SYNC_SH",
                        os.path.join(HERE, os.pardir, "bin", "paddock_sync.sh"))
FAILS = []



# --- BusyBox tar emulator -------------------------------------------------
# The host's tar is NOT BusyBox. GNU/bsdtar honour -k as "skip existing and
# carry on"; BusyBox ABORTS at the first one. That difference is the whole
# bug, so a host test using the host's tar silently passes against the broken
# code. This shim reproduces BusyBox's semantics so the check is real.
_BUSYBOX_TAR = r"""#!/usr/bin/env python3
import os, sys, tarfile
args = sys.argv[1:]
flags = "".join(a.lstrip("-") for a in args if a.startswith("-"))
if "c" in flags:
    out = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")
    for root, dirs, files in os.walk("."):
        for n in sorted(dirs) + sorted(files):
            out.add(os.path.join(root, n), recursive=False)
    out.close()
    sys.exit(0)
tf = tarfile.open(fileobj=sys.stdin.buffer, mode="r|")
for m in tf:
    if m.isdir():
        os.makedirs(m.name, exist_ok=True)
        continue
    if "k" in flags and os.path.exists(m.name):
        # BusyBox: this is fatal, not a skip.
        sys.stderr.write("tar: can't open '%s': File exists\n" % m.name)
        sys.exit(1)
    tf.extract(m, ".")
sys.exit(0)
"""


def with_busybox_tar():
    """Return a PATH prefix dir holding the BusyBox-semantics `tar`."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "tar")
    with open(p, "w") as f:
        f.write(_BUSYBOX_TAR)
    os.chmod(p, 0o755)
    return d


def check(name, got, want):
    if got == want:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}\n          got  {got!r}\n          want {want!r}")
        FAILS.append(name)


def call(func, *args, path_prefix=None):
    """Invoke one engine function in lib mode. Returns (rc, stdout)."""
    script = f'PADDOCK_LIB=1 . "{ENGINE}"\n{func} ' + " ".join(f'"{a}"' for a in args)
    env = dict(os.environ)
    if path_prefix:
        env["PATH"] = path_prefix + os.pathsep + env["PATH"]
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip()


def mkfiles(root, names, body="x"):
    for n in names:
        p = os.path.join(root, n)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(body)


def nfiles(root):
    return sum(len(f) for _, _, f in os.walk(root))


# ---------------------------------------------------------------- shaders
print("\n[1] shader merge — a NON-EMPTY vault must still receive every file")
tmp = tempfile.mkdtemp()
try:
    src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
    # 300 content-addressed names across hash-bucket dirs, like a real vault.
    names = [f"{i % 256:02x}/{i:062x}" for i in range(300)]
    mkfiles(src, names, "shader-bytes")
    # The vault already holds some of them (compiled locally before the pull) —
    # this is the exact condition that aborted the live merge.
    mkfiles(dst, names[40:60], "shader-bytes")
    mkfiles(dst, ["ff/locally-compiled-only"], "local")

    rc, out = call("merge_shaders", src, dst)
    check("merge succeeds", rc, 0)
    check("EVERY bundle file present (not truncated at first collision)",
          nfiles(dst), 300 + 1)
    check("pre-existing local-only shader kept",
          os.path.isfile(os.path.join(dst, "ff/locally-compiled-only")), True)
    check("no INCOMPLETE warning", "INCOMPLETE" in out, False)
    check("reports the bundle size", "300 in bundle" in out, True)
    check("reports only the NEW ones as added", "+280 new" in out, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[1b] shader merge — empty vault (the case that always worked)")
tmp = tempfile.mkdtemp()
try:
    src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
    mkfiles(src, [f"{i % 256:02x}/{i:062x}" for i in range(120)])
    rc, out = call("merge_shaders", src, dst)
    check("all files land", nfiles(dst), 120)
    check("rc 0", rc, 0)
    check("reports +120", "+120 new" in out, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[1c] shader merge — a short merge is REPORTED, not passed off as success")
tmp = tempfile.mkdtemp()
try:
    src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
    mkfiles(src, [f"{i:02x}/f" for i in range(10)])
    os.makedirs(dst)
    # Make the destination unwritable so the merge cannot complete.
    os.chmod(dst, 0o500)
    rc, out = call("merge_shaders", src, dst)
    os.chmod(dst, 0o700)
    check("non-zero rc on a short merge", rc, 1)
    check("says INCOMPLETE", "INCOMPLETE" in out, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[1d] shader merge under BUSYBOX tar semantics (the actual rig)")
# This is the section that discriminates. With BusyBox's tar on PATH, the old
# `tar -xkf` implementation truncates at the first collision exactly as it did
# on the rig; the shipped implementation must still deliver every file.
shim = with_busybox_tar()
tmp = tempfile.mkdtemp()
try:
    # Sanity: the shim really does abort where BusyBox aborts.
    a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
    mkfiles(a, [f"{i:03d}" for i in range(50)])
    mkfiles(b, ["020"])
    r = subprocess.run(["bash", "-c",
                        f'(cd "{a}" && tar -cf - .) | (cd "{b}" && tar -xkf -)'],
                       capture_output=True, text=True,
                       env={**os.environ, "PATH": shim + os.pathsep + os.environ["PATH"]})
    check("shim reproduces BusyBox -k abort", nfiles(b) < 50, True)
    check("shim reports the BusyBox error", "File exists" in r.stderr, True)

    # Now the real function, same hostile conditions.
    src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
    names = [f"{i % 256:02x}/{i:062x}" for i in range(300)]
    mkfiles(src, names, "shader-bytes")
    mkfiles(dst, names[40:60], "shader-bytes")
    rc, out = call("merge_shaders", src, dst, path_prefix=shim)
    check("merge_shaders delivers ALL files under BusyBox tar", nfiles(dst), 300)
    check("rc 0", rc, 0)
    check("no INCOMPLETE warning", "INCOMPLETE" in out, False)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(shim, ignore_errors=True)

print("\n[1e] the broken idiom must not come back (static guard)")
for rel in ("bin/paddock_sync.sh", "pro-tuning/install-protune.sh"):
    src = open(os.path.join(HERE, os.pardir, rel)).read()
    code = [ln for ln in src.splitlines()
            if not ln.lstrip().startswith("#") and "tar -xk" in ln]
    check(f"{rel}: no `tar -xk` in code", code, [])

# ------------------------------------------------------------------ saves
print("\n[2] save restore — the career save must land even if the game made a stub")
tmp = tempfile.mkdtemp()
try:
    src, dst = os.path.join(tmp, "bundle"), os.path.join(tmp, "savedata")
    # Bundle: the real backed-up career save + 10 replays.
    mkfiles(src, ["NPEA90002-GAME-/GAME.DAT"], "REAL CAREER PROGRESS")
    mkfiles(src, ["NPEA90002-GAME-/PARAM.SFO"], "real-sfo")
    for i in range(1, 11):
        mkfiles(src, [f"NPEA90002-REPLAY-{i:03d}/REPLAY.DAT"], f"replay{i}")
    # Local: only the fresh stub RPCS3 wrote on first launch (the pad test).
    mkfiles(dst, ["NPEA90002-GAME-/GAME.DAT"], "fresh empty stub")
    mkfiles(dst, ["NPEA90002-GAME-/PARAM.SFO"], "stub-sfo")

    rc, out = call("restore_saves", src, dst)
    check("rc 0", rc, 0)
    check("CAREER SAVE restored over the stub",
          open(os.path.join(dst, "NPEA90002-GAME-/GAME.DAT")).read(),
          "REAL CAREER PROGRESS")
    check("all 10 replays restored",
          sum(1 for d in os.listdir(dst) if "REPLAY" in d), 10)
    baks = [d for d in os.listdir(dst) if ".paddock.bak." in d]
    check("the stub was BACKED UP, not deleted", len(baks), 1)
    check("backup holds the stub's bytes",
          open(os.path.join(dst, baks[0], "GAME.DAT")).read(), "fresh empty stub")
    check("report counts", "10 restored, 1 replaced" in out, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[2b] save restore — an identical save is left alone (no churn, no backup)")
tmp = tempfile.mkdtemp()
try:
    src, dst = os.path.join(tmp, "bundle"), os.path.join(tmp, "savedata")
    mkfiles(src, ["NPEA90002-GAME-/GAME.DAT"], "same bytes")
    mkfiles(dst, ["NPEA90002-GAME-/GAME.DAT"], "same bytes")
    before = os.stat(os.path.join(dst, "NPEA90002-GAME-/GAME.DAT")).st_mtime

    rc, out = call("restore_saves", src, dst)
    check("no backup created",
          [d for d in os.listdir(dst) if ".paddock.bak." in d], [])
    check("reported as already current", "1 already current" in out, True)
    check("file untouched",
          os.stat(os.path.join(dst, "NPEA90002-GAME-/GAME.DAT")).st_mtime, before)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[2c] save restore — fresh rig with no savedata dir at all")
tmp = tempfile.mkdtemp()
try:
    src, dst = os.path.join(tmp, "bundle"), os.path.join(tmp, "nope", "savedata")
    mkfiles(src, ["NPEA90002-GAME-/GAME.DAT"], "career")
    rc, out = call("restore_saves", src, dst)
    check("savedata dir created and save restored",
          open(os.path.join(dst, "NPEA90002-GAME-/GAME.DAT")).read(), "career")
    check("report", "1 restored" in out, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[2d] save restore — nothing in the bundle is not an error")
tmp = tempfile.mkdtemp()
try:
    rc, out = call("restore_saves", os.path.join(tmp, "absent"),
                   os.path.join(tmp, "savedata"))
    check("rc 0", rc, 0)
    check("says so plainly", "none in bundle" in out, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[4] save-dir resolution — the GT6 case (saves under a FOREIGN prefix)")
# Verbatim from the rig 2026-07-27. GT6's US disc is BCUS98296 but it writes
# BCJS37016-* (the Japanese title id); every other title here follows the
# convention, which is why GT6 alone silently pushed an empty savedata/.
RIG_SAVES = ["BCJS37016-BKUP6", "BCJS37016-GAME6", "BCUS98114-GAME",
             "BCUS98158-GAME-", "NPEA00050-GAME-", "NPEA00050-RPLY2-F001",
             "NPEA90002-GAME-", "NPEA90002-GAME-.paddock.bak.20260727-103450",
             "NPEA90002-REPLAY-001"]
ALIAS = "# comment line\nBCUS98296\tBCJS37016\n"

tmp = tempfile.mkdtemp()
try:
    sv = os.path.join(tmp, "savedata")
    for d in RIG_SAVES:
        mkfiles(sv, [f"{d}/PARAM.SFO"], d)
    al = os.path.join(tmp, "save_aliases.tsv")
    open(al, "w").write(ALIAS)

    def dirs(gid, alias=al):
        rc, out = call("save_dirs_for", gid, sv, alias)
        return sorted(os.path.basename(x) for x in out.splitlines() if x)

    check("GT6 resolves via the alias",
          dirs("BCUS98296"), ["BCJS37016-BKUP6", "BCJS37016-GAME6"])
    check("conventional titles still resolve directly",
          dirs("BCUS98114"), ["BCUS98114-GAME"])
    check("prefix match covers replays",
          dirs("NPEA00050"), ["NPEA00050-GAME-", "NPEA00050-RPLY2-F001"])
    check("backup dirs are never banked",
          dirs("NPEA90002"), ["NPEA90002-GAME-", "NPEA90002-REPLAY-001"])
    check("a game with no save resolves empty", dirs("NPUA80075"), [])
    # Without the alias file GT6 finds nothing — this IS the shipped bug.
    check("no alias file -> GT6 finds nothing (the original failure)",
          dirs("BCUS98296", os.path.join(tmp, "absent.tsv")), [])
    check("alias file absent is not fatal for normal titles",
          dirs("BCUS98114", os.path.join(tmp, "absent.tsv")), ["BCUS98114-GAME"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[4b] unclaimed save folders are surfaced so the operator can map them")
tmp = tempfile.mkdtemp()
try:
    sv = os.path.join(tmp, "savedata")
    vb = os.path.join(tmp, "vault")
    for d in RIG_SAVES:
        mkfiles(sv, [f"{d}/PARAM.SFO"], d)
    # Vault holds the games this rig knows about — GT6 among them.
    for g in ("BCUS98296", "BCUS98114", "BCUS98158", "NPEA00050", "NPEA90002"):
        os.makedirs(os.path.join(vb, g, "shaders"), exist_ok=True)

    r = subprocess.run(
        ["bash", "-c",
         f'PADDOCK_LIB=1 . "{ENGINE}"\nVAULT_BASE="{vb}"\n'
         f'unclaimed_save_dirs "{sv}" "{os.path.join(tmp, "none.tsv")}"'],
        capture_output=True, text=True)
    got = sorted(x for x in r.stdout.split() if x)
    check("GT6's foreign folders show up as unclaimed",
          got, ["BCJS37016-BKUP6", "BCJS37016-GAME6"])

    # With the alias in place they are claimed, so nothing is reported.
    al = os.path.join(tmp, "save_aliases.tsv")
    open(al, "w").write(ALIAS)
    r = subprocess.run(
        ["bash", "-c",
         f'PADDOCK_LIB=1 . "{ENGINE}"\nVAULT_BASE="{vb}"\n'
         f'unclaimed_save_dirs "{sv}" "{al}"'],
        capture_output=True, text=True)
    check("once aliased, nothing is unclaimed", r.stdout.strip(), "")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[4c] the shipped alias file actually carries the GT6 mapping")
alias_path = os.path.join(HERE, os.pardir, "config", "save_aliases.tsv")
check("config/save_aliases.tsv exists", os.path.isfile(alias_path), True)
tmp = tempfile.mkdtemp()
try:
    sv = os.path.join(tmp, "savedata")
    for d in RIG_SAVES:
        mkfiles(sv, [f"{d}/PARAM.SFO"], d)
    rc, out = call("save_dirs_for", "BCUS98296", sv, os.path.abspath(alias_path))
    check("GT6 resolves using the SHIPPED file",
          sorted(os.path.basename(x) for x in out.splitlines() if x),
          ["BCJS37016-BKUP6", "BCJS37016-GAME6"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[3] dir_fingerprint")
tmp = tempfile.mkdtemp()
try:
    a, b, c = (os.path.join(tmp, x) for x in "abc")
    mkfiles(a, ["d/one", "two"], "same")
    mkfiles(b, ["d/one", "two"], "same")
    mkfiles(c, ["d/one", "two"], "different")
    _, fa = call("dir_fingerprint", a)
    _, fb = call("dir_fingerprint", b)
    _, fc = call("dir_fingerprint", c)
    check("identical trees match", fa, fb)
    check("differing contents differ", fa != fc, True)
    check("returns a sha256", len(fa), 64)
    _, fmissing = call("dir_fingerprint", os.path.join(tmp, "nope"))
    check("missing dir does not crash", fmissing, "")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[5] classify_http — the silent-401 lesson (2026-08-27)")
# The dead-token incident: an expired PAT 401'd every API call, and the old
# status fallback swallowed it into '[]', rendering the whole fleet as
# LOCAL-ONLY. The mapping is pure text, so it tests without a network.
for code, want in [("200", ""), ("401", "rejected the paddock token"),
                   ("403", "rejected the paddock token"),
                   ("404", "not reachable with this token"),
                   ("000", "unreachable"), ("500", "API error")]:
    rc, out = call("classify_http", code)
    ok = (out == "" if want == "" else want in out)
    check(f"HTTP {code} -> {'healthy' if not want else repr(want)}", ok, True)

print("\n[6] a rejected token dies loudly (end-to-end, online engine)")
# Discriminates against the pre-fix engine: there, `status` under a 401
# exited 0 and printed LOCAL-ONLY rows. The curl shim answers 401 to the
# preflight and fails (exit 22, like curl -f) everything else.
tmp = tempfile.mkdtemp()
try:
    shim = os.path.join(tmp, "bin")
    os.makedirs(shim)
    with open(os.path.join(shim, "curl"), "w") as f:
        f.write("#!/usr/bin/env python3\n"
                "import sys\n"
                "args = ' '.join(sys.argv)\n"
                "if '%{http_code}' in args:\n"
                "    sys.stdout.write('401')\n"
                "    sys.exit(0)\n"
                "sys.exit(22)\n")
    os.chmod(os.path.join(shim, "curl"), 0o755)
    cred = os.path.join(tmp, "paddock.json")
    with open(cred, "w") as f:
        f.write('{"repo":"nobody/etk-paddock","token":"ghp_dead"}')
    env = dict(os.environ, PATH=shim + os.pathsep + os.environ["PATH"],
               PADDOCK_CRED=cred)
    r = subprocess.run(["bash", ENGINE, "status"], env=env,
                       capture_output=True, text=True, timeout=30)
    check("status exits non-zero on 401", r.returncode != 0, True)
    check("stderr names the dead token",
          "rejected the paddock token" in r.stderr, True)
    check("no LOCAL-ONLY lie on stdout", "LOCAL-ONLY" not in r.stdout, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL PADDOCK CHECKS PASSED")
