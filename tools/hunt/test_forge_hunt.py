#!/usr/bin/env python3
"""Sandbox harness for `forge.sh --hunt` (P2, docs/AUTONOMY_SPEC.md §3.4). No network, no node.

  python3 tools/hunt/test_forge_hunt.py [--against REV]

The real forge.sh and lane_rpcs3.sh run in a sandbox repo whose etk.conf names the build
host `fakenode.invalid`; fake ssh/rsync/docker/sleep sit first on PATH. The fake ssh refuses
any other host and runs the command locally with HOME=<sandbox>/node, a directory holding a
real git checkout as the "certified tree", so the worktree, reset, patch-apply and staging
logic execute for real. tools/hunt/hunt.py is a stub whose answer the test sets.

  [HUNT]    a granted mint builds in <tree>-hunt (a worktree), leaves the certified tree
            untouched, stages emulators/hunt/<artifact> (+ .sha256) host- and node-side,
            keeps status under state/hunt/<id>/forge, uses the active_hunt_ marker, checks the
            grant with the node fingerprint at preflight and again before staging
  [REFUSE]  no grant -> exit 3 before any ssh; kernel lane / --local / missing HUNT_* / a
            non-hunt artifact name -> exit 2; a live certified build -> preflight FAIL
  [EXPIRY]  a grant that ends mid-build stages nothing
  [CERT]    a regular (non-hunt) rpcs3 mint still stages to emulators/ under state/forge,
            and refuses while a hunt build is live
DISCRIMINATION: --against <rev> runs the suites on forge.sh + lane_rpcs3.sh from <rev>;
against the pre-P2 commit every HUNT/REFUSE/EXPIRY case fails (--hunt is an unknown arg).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ETK = os.path.abspath(os.path.join(HERE, "..", ".."))
AGAINST = None
GID = "hunt-20261011-gt6"
ART = f"rpcs3-etk_{GID}-m01_armsx3-BASE_linux_aarch64.AppImage"

FAKE_SSH = r'''#!/usr/bin/env python3
import os, subprocess, sys
a, i = sys.argv[1:], 0
while i < len(a) and a[i].startswith("-"):
    i += 2 if a[i] in ("-o", "-p", "-i", "-l", "-F") else 1
host, cmd = a[i], " ".join(a[i + 1:])
td = os.environ["SANDBOX"]
with open(os.path.join(td, "ssh.log"), "a") as f:
    f.write(host + "\t" + cmd.replace("\n", "\\n") + "\n")
if host != "fakenode.invalid":
    sys.exit(f"fake ssh: refusing real host {host}")
env = dict(os.environ, HOME=os.path.join(td, "node"))
sys.exit(subprocess.run(["bash", "-c", cmd], env=env, cwd=env["HOME"]).returncode)
'''

FAKE_RSYNC = r'''#!/usr/bin/env python3
import os, shutil, sys
src, dst = [x for x in sys.argv[1:] if not x.startswith("-")][-2:]
host, path = src.split(":", 1)
if host != "fakenode.invalid":
    sys.exit(f"fake rsync: refusing real host {host}")
shutil.copyfile(os.path.join(os.environ["SANDBOX"], "node", path), dst)
'''

FAKE_DOCKER = r'''#!/usr/bin/env python3
import os, subprocess, sys
a = sys.argv[1:]
if a[:1] == ["ps"]:
    print("turnip-rocknix Up 2 days"); print("etk-imgtool Up 2 days")
elif a[:1] == ["images"]:
    print("img0123456789")
elif a[:1] == ["run"]:
    tree, cmd = None, a[-1]
    for k, x in enumerate(a):
        if x == "-v" and a[k + 1].endswith(":/rpcs3"):
            tree = a[k + 1].rsplit(":", 1)[0]
    if "build-linux-aarch64.sh" in cmd:
        os.makedirs(os.path.join(tree, "build"), exist_ok=True)
    elif "package-appimage.sh" in cmd:
        head = subprocess.run(["git", "-C", tree, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        with open(os.path.join(tree, "build", "rpcs3-built.AppImage"), "w") as f:
            f.write("appimage of " + head + " + " + open(os.path.join(tree, "a.txt")).read())
    elif "verify-markers.sh" in cmd:
        print("GTK Edition vtest")
    elif "rm -rf /rpcs3/build" in cmd:
        subprocess.run(["rm", "-rf", os.path.join(tree, "build")])
else:
    sys.exit(1)
'''

STUB_HUNT = r'''#!/usr/bin/env python3
import os, sys
td = os.environ["SANDBOX"]
with open(os.path.join(td, "hunt.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
answers = os.environ.get("FAKE_GRANT", "0").split(",")
n = len(open(os.path.join(td, "hunt.log")).read().splitlines())
sys.exit(int(answers[min(n, len(answers)) - 1]))
'''


def sh(*cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        td = self.td
        self.repo, self.node, self.bin = (os.path.join(td, x) for x in ("repo", "node", "bin"))
        for d in (self.bin, os.path.join(self.repo, "tools", "forge"), os.path.join(self.repo, "tools", "hunt"),
                  os.path.join(self.repo, "config"), os.path.join(self.repo, "bin"),
                  os.path.join(self.node, "forge-runs")):
            os.makedirs(d)
        for rel in ("forge.sh", "tools/forge/lane_rpcs3.sh"):
            dst = os.path.join(self.repo, rel)
            if AGAINST:
                with open(dst, "w") as f:
                    f.write(sh("git", "-C", ETK, "show", f"{AGAINST}:{rel}"))
                os.chmod(dst, 0o755)
            else:
                shutil.copy(os.path.join(ETK, rel), dst)
        for rel in ("tools/tui.sh", "config/gtk_stack.json", "bin/etk_pitstop.py", "install.sh"):
            shutil.copy(os.path.join(ETK, rel), os.path.join(self.repo, rel))
        self.write(os.path.join(self.repo, "tools", "release_sanity.sh"), "#!/bin/sh\necho stub sanity\n", 0o755)
        self.write(os.path.join(self.repo, "tools", "hunt", "hunt.py"), STUB_HUNT, 0o755)
        for name, body in (("ssh", FAKE_SSH), ("rsync", FAKE_RSYNC), ("docker", FAKE_DOCKER),
                           ("sleep", "#!/bin/sh\nexec /usr/bin/sleep 0.2\n")):
            self.write(os.path.join(self.bin, name), body, 0o755)

        # the node's certified tree: a real repo at BASE
        self.tree = os.path.join(self.node, "rpcs3")
        os.makedirs(self.tree)
        self.write(os.path.join(self.tree, "a.txt"), "hello\n")
        g = ["git", "-C", self.tree, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        sh("git", "-C", self.tree, "init", "-q", "-b", "main")
        sh(*g, "add", "-A")
        sh(*g, "commit", "-q", "--no-verify", "-m", "base")
        self.base = sh("git", "-C", self.tree, "rev-parse", "HEAD").strip()
        self.art = ART.replace("BASE", self.base[:9])

        # the hunt's extracted fork inputs (what hunt.py mint hands forge)
        self.hfork = os.path.join(td, "hfork")
        patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n hello\n+hunted\n"
        for rel, body in (("patches/gt6.patch", patch), ("scripts/package-appimage.sh", "true\n"),
                          ("scripts/verify-markers.sh", "true\n")):
            self.write(os.path.join(self.hfork, rel), body)
        # the certified fork (regular mints)
        self.cfork = os.path.join(td, "cfork")
        for rel, body in (("patches/x-0.10.0-dev.patch", patch.replace("hunted", "certified")),
                          ("scripts/package-appimage.sh", "true\n"), ("scripts/verify-markers.sh", "true\n")):
            self.write(os.path.join(self.cfork, rel), body)
        self.write(os.path.join(self.repo, "etk.conf"),
                   f'FORGE_HOST="fakenode.invalid"\nFORGE_RPCS3_TREE="{self.tree}"\nFORGE_RPCS3_BASE="{self.base[:9]}"\n'
                   f'FORGE_RPCS3_FORK="{self.cfork}"\nFORGE_RPCS3_PATCH="{self.cfork}/patches/x-0.10.0-dev.patch"\n'
                   f'FORGE_RPCS3_ARTIFACT="rpcs3-etk_gtk-edition-test_linux_aarch64.AppImage"\n')

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def write(self, path, body, mode=0o644):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, mode)

    def forge(self, *args, grant="0", hunt_env=True, timeout=120, **env_over):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": os.path.join(self.td, "home"), "SANDBOX": self.td,
               "FAKE_GRANT": grant, "LANG": "C.UTF-8", "TERM": "dumb"}
        if hunt_env:
            env.update(HUNT_BASE=self.base[:9], HUNT_FORK=self.hfork, HUNT_PATCH=f"{self.hfork}/patches/gt6.patch",
                       HUNT_ARTIFACT=self.art)
        env.update(env_over)
        os.makedirs(env["HOME"], exist_ok=True)
        r = subprocess.run([os.path.join(self.repo, "forge.sh"), *args], env=env, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def read(self, *rel):
        with open(os.path.join(self.td, *rel)) as f:
            return f.read()

    def exists(self, *rel):
        return os.path.exists(os.path.join(self.td, *rel))

    def live_marker(self, name):
        p = subprocess.Popen(["/usr/bin/sleep", "60"])
        self.addCleanup(p.kill)
        self.write(os.path.join(self.node, "forge-runs", name), f"/x {p.pid}\n")


class HuntTests(Sandbox):
    def test_granted_mint(self):
        rc, out = self.forge("--hunt", GID, "rpcs3", "--verbose")
        self.assertEqual(rc, 0, out[-1500:])
        staged = self.read("repo", "emulators", "hunt", self.art)
        self.assertIn("hunted", staged)
        self.assertTrue(self.exists("repo", "emulators", "hunt", self.art + ".sha256"))
        self.assertTrue(self.exists("node", "etk", "emulators", "hunt", self.art))
        self.assertFalse(self.exists("node", "etk", "emulators", self.art), "a hunt never stages node-side in the catalog dir")
        self.assertFalse(self.exists("repo", "emulators", self.art))
        self.assertTrue(self.exists("node", "rpcs3-hunt", ".git"), "the hunt builds in its own worktree")
        self.assertEqual(sh("git", "-C", self.tree, "status", "--porcelain"), "", "the certified tree is untouched")
        self.assertIn("rpcs3\tDONE", self.read("repo", "state", "hunt", GID, "forge", "status.tsv"))
        self.assertFalse(self.exists("repo", "state", "forge"), "a hunt never writes the certified forge state")
        log = self.read("ssh.log")
        self.assertIn("active_hunt_rpcs3", log)
        self.assertNotIn("forge-runs/active_rpcs3", log)
        checks = self.read("hunt.log").splitlines()
        self.assertEqual(checks[0], f"check --lane rpcs3 --node-host fakenode.invalid --id {GID}")
        self.assertEqual(checks[-1], f"check --lane rpcs3 --id {GID}")

    def test_reattach_marker_is_separate(self):
        self.live_marker("active_hunt_rpcs3")       # our own live build: reattach to it, not busy
        try:
            rc, out = self.forge("--hunt", GID, "rpcs3", "--verbose", timeout=15)
        except subprocess.TimeoutExpired as e:   # it waits on the live build, as reattach should
            out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        self.assertIn("REATTACHED to live build", out)
        self.assertNotIn("node-busy", out)


class RefuseTests(Sandbox):
    def test_no_grant(self):
        rc, out = self.forge("--hunt", GID, "rpcs3", "--verbose", grant="1")
        self.assertEqual(rc, 3, out)
        self.assertFalse(self.exists("ssh.log"), "no ssh before the grant check")

    def test_bad_requests(self):
        for args, env in (((GID, "kernel"), {}), ((GID, "image"), {}), ((GID, "--local", "rpcs3"), {}),
                          ((GID, "rpcs3"), {"HUNT_ARTIFACT": ""}),
                          ((GID, "rpcs3"), {"HUNT_ARTIFACT": "rpcs3-etk_gtk-edition-0.10.1.AppImage"}),
                          (("not-an-id", "rpcs3"), {}), ((GID, "rpcs3"), {"HUNT_BASE": "main;reboot"}),
                          ((GID, "rpcs3"), {"HUNT_ARTIFACT": "rpcs3-etk_hunt-x;reboot.AppImage"}),
                          ((GID, "rpcs3"), {"HUNT_PATCH": "/tmp/a;b.patch"}),
                          ((GID, "rpcs3"), {"HUNT_MARKER": "x y"})):
            rc, out = self.forge("--hunt", *args, "--verbose", **env)
            self.assertEqual(rc, 2, (args, env, out[-300:]))
            self.assertIn("--hunt", out)
            self.assertNotIn("unknown arg", out)
        self.assertFalse(self.exists("ssh.log"))

    def test_certified_build_running(self):
        self.live_marker("active_rpcs3")
        rc, out = self.forge("--hunt", GID, "rpcs3", "--verbose")
        self.assertNotEqual(rc, 0)
        self.assertIn("node-busy:active_rpcs3", out)
        self.assertFalse(self.exists("repo", "emulators", "hunt", self.art))


class ExpiryTests(Sandbox):
    def test_grant_ends_mid_build(self):
        rc, out = self.forge("--hunt", GID, "rpcs3", "--verbose", grant="0,1")
        self.assertNotEqual(rc, 0)
        self.assertIn("grant ended during the build", out)
        self.assertFalse(self.exists("repo", "emulators", "hunt", self.art))
        self.assertIn("rpcs3\tFAIL", self.read("repo", "state", "hunt", GID, "forge", "status.tsv"))


class CertTests(Sandbox):
    def test_regular_mint_unchanged(self):
        rc, out = self.forge("rpcs3", "--verbose", hunt_env=False)
        self.assertEqual(rc, 0, out[-1500:])
        self.assertIn("certified", self.read("repo", "emulators", "rpcs3-etk_gtk-edition-test_linux_aarch64.AppImage"))
        self.assertIn("rpcs3\tDONE", self.read("repo", "state", "forge", "status.tsv"))
        self.assertFalse(self.exists("node", "rpcs3-hunt"))
        self.assertFalse(self.exists("hunt.log"), "a regular mint never consults the hunt grant")

    def test_regular_refuses_beside_a_hunt(self):
        self.live_marker("active_hunt_rpcs3")
        rc, out = self.forge("rpcs3", "--verbose", hunt_env=False)
        self.assertNotEqual(rc, 0)
        self.assertIn("node-busy:active_hunt_rpcs3", out)


if __name__ == "__main__":
    if "--against" in sys.argv:
        k = sys.argv.index("--against")
        AGAINST = sys.argv[k + 1]
        del sys.argv[k:k + 2]
    unittest.main(verbosity=1)
