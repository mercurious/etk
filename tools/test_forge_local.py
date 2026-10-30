#!/usr/bin/env python3
"""test_forge_local.py -- `forge.sh --local` builds on the local node, and never over a pinned name.

Sandboxed on tools/hunt/test_forge_hunt.py's fakes (no real ssh, no real build). Preflight-level
on purpose, so it runs on macOS too (that harness's build polling needs a Linux userland).

  [NODE]   --local (or FORGE_HOST=<the local node>) reaches only the local node, whatever
           FORGE_HOST says; the chiaki/wl-mirror stagers build on it too
  [PIN]    a kernel, core or Turnip name the release pins is refused before any ssh; a new name
           passes; a cloud run is not gated
  [HUNT]   --hunt still refuses --local

DISCRIMINATION: --against <rev> runs this on forge.sh from <rev>; against the pre-local commit
every NODE and PIN case fails (--local ran `bash -c` on the Mac and gated nothing).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hunt"))
import test_forge_hunt as h  # noqa: E402
import unittest  # noqa: E402

LOCAL = "fakenode.invalid"      # the fake ssh's only reachable host
CLOUD = "cloud.invalid"


class LocalNode(h.Sandbox):
    def setUp(self):
        super().setUp()
        conf = os.path.join(self.repo, "etk.conf")
        body = open(conf).read().replace(f'FORGE_HOST="{LOCAL}"', f'FORGE_HOST="{CLOUD}"')
        self.write(conf, body)
        self.stack = json.load(open(os.path.join(self.repo, "config", "gtk_stack.json")))

    def local(self, *args, **env):
        return self.forge(*args, "--verbose", hunt_env=False, FORGE_LOCAL_HOST=LOCAL, **env)

    def hosts(self):
        if not self.exists("ssh.log"):
            return set()
        return {ln.split("\t")[0] for ln in self.read("ssh.log").splitlines()}


class NodeTests(LocalNode):
    def test_local_flag_reaches_only_the_local_node(self):
        rc, out = self.local("--local", "rpcs3", "--dry-run")
        self.assertEqual(rc, 0, out[-1500:])
        self.assertEqual(self.hosts(), {LOCAL})

    def test_forge_host_alone_is_local_too(self):
        conf = os.path.join(self.repo, "etk.conf")
        self.write(conf, open(conf).read().replace(f'FORGE_HOST="{CLOUD}"\n', ""))
        rc, out = self.local("kernel", "--dry-run", FORGE_HOST=LOCAL)
        self.assertNotEqual(rc, 0)
        self.assertIn("pinned by the release", out)

    def test_stagers_follow_the_node(self):
        for tool, var in (("chiaki", "CHIAKI_BUILD_HOST"), ("wl-mirror", "WLM_BUILD_HOST")):
            stub = "build_chiaki.sh" if tool == "chiaki" else "build_wl_mirror.sh"
            self.write(os.path.join(self.repo, "tools", "rocknix-bin", stub),
                       f'#!/bin/sh\necho "${var}" > "$SANDBOX/{tool}.host"\necho bin > "$(dirname "$0")/{tool}"\n', 0o755)
        rc, out = self.local("--local", "chiaki", "wlmirror")
        self.assertEqual(rc, 0, out[-1500:])
        self.assertEqual(self.read("chiaki.host").strip(), LOCAL)
        self.assertEqual(self.read("wl-mirror.host").strip(), LOCAL)


class PinTests(LocalNode):
    def refused(self, *args, **env):
        rc, out = self.local("--local", *args, "--dry-run", **env)
        self.assertNotEqual(rc, 0, out[-800:])
        self.assertIn("pinned by the release", out)
        self.assertEqual(self.hosts(), set(), "refused before any ssh")

    def test_certified_kernel(self):
        self.refused("kernel")

    def test_certified_core(self):
        conf = os.path.join(self.repo, "etk.conf")
        self.write(conf, open(conf).read().replace("rpcs3-etk_gtk-edition-test_linux_aarch64.AppImage",
                                                   self.stack["rpcs3"]["asset"]))
        self.refused("rpcs3")

    def test_every_catalog_turnip(self):
        self.refused("turnip", FORGE_TURNIP_VERS="26.2.1", FORGE_TURNIP_GTKVER="0.7")
        self.refused("turnip", FORGE_TURNIP_VERS="99.0 26.2.2", FORGE_TURNIP_GTKVER="0.7")

    def test_a_new_name_passes_the_gate(self):
        for lane, env in (("kernel", {"FORGE_KERNEL_VER": "0.6.4.9"}),
                          ("turnip", {"FORGE_TURNIP_VERS": "26.2.2", "FORGE_TURNIP_GTKVER": "0.7.9"})):
            rc, out = self.local("--local", lane, "--dry-run", **env)
            self.assertNotIn("pinned by the release", out)
            self.assertEqual(self.hosts(), {LOCAL})

    def test_a_cloud_run_is_not_gated(self):
        rc, out = self.local("kernel", "--dry-run")
        self.assertNotIn("pinned by the release", out)
        self.assertEqual(self.hosts(), {CLOUD})


class HuntTests(LocalNode):
    def test_hunt_refuses_local(self):
        rc, out = self.forge("--hunt", h.GID, "--local", "rpcs3", "--verbose", FORGE_LOCAL_HOST=LOCAL)
        self.assertNotEqual(rc, 0)
        self.assertIn("--hunt builds on the grant's node only", out)


if __name__ == "__main__":
    if "--against" in sys.argv:
        k = sys.argv.index("--against")
        h.AGAINST = sys.argv[k + 1]
        del sys.argv[k:k + 2]
    unittest.main(verbosity=1)
