#!/usr/bin/env python3
"""ETK — host-side regression tests for the notification surfaces.

Run from the repo root:   python3 tools/test_notify.py

ETK's toasts are aligned to EmulationStation's two notification surfaces: a
top-center verdict popup and an upper-right progress card with a real bar.
Three things about that arrangement are silently breakable, and each has a
suite here.

  [CONTRACT] mako matches criteria on app-name with a byte-exact strcmp, so
             the app-name in the senders MUST equal the [app-name=...]
             headers install.sh writes. A typo does not error — the toast
             just falls back to the stock black style and nobody notices.
             This suite pins sender and installer together.

             It also refuses `max-visible` inside an app-name criteria.
             mako rejects that option there, and one bad option makes it
             reject the WHOLE config: reload keeps the old one, but the next
             boot mako exits EXIT_FAILURE and the rig loses every
             notification, ROCKNIX's own included.

  [MAKO]     the config surgery, run as the REAL script text from
             install.sh / uninstall.sh (not a copy that can drift): strips
             the legacy 0.8.3 block, is idempotent across re-installs,
             never touches ROCKNIX globals or a foreign criteria, and
             uninstalls back to exactly the original file.

  [NOTIFY]   _Notifier / _ProgressCard against a stubbed bus: transport
             selection, the int32 `value` hint (a uint32 fails the whole
             Notify call, costing the notification and not just the bar),
             id reuse, dismissal, and — most important — that a broken
             notification path degrades instead of raising into an install.

  [BEACON]   install.sh's own "ETK INSTALL" progress card, which exists so an
             install cannot run without the person holding the handheld
             noticing. That makes it a security surface, and the structural
             properties are what matter: ALWAYS ON (no env var, no etk.conf
             knob, no kill-switch may skip a beacon), fail-soft, a monotonic
             percent table ending at 100, an announcement that precedes the
             first rig mutation, and terminal verdicts on both exits. Static
             reads of install.sh — they cannot prove the rig rendered
             anything, which is the operator's screen to check.

No rig, no bus, no network, no root. BusyBox parity for the awk was verified
separately against busybox:1.36; the constructs used are POSIX-basic.
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
# ETK_REPO_ROOT lets the suite be pointed at a mutated COPY of the kit, which
# is how it gets checked against a deliberately broken version — a gate that
# has only ever been run against working code proves nothing.
ROOT = os.environ.get("ETK_REPO_ROOT",
                      os.path.normpath(os.path.join(_HERE, os.pardir)))
PITSTOP = os.path.join(ROOT, "bin", "etk_pitstop.py")
INSTALL_SH = os.path.join(ROOT, "install.sh")
UNINSTALL_SH = os.path.join(ROOT, "uninstall.sh")
NOTIFY_SH = os.path.join(ROOT, "bin", "etk_notify.sh")

spec = importlib.util.spec_from_file_location('pit', PITSTOP)
pit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pit)

FAILS = []


def check(name, got, want):
    if got == want:
        print(f"    ok   {name}")
    else:
        print(f"    FAIL {name}: got {got!r}, want {want!r}")
        FAILS.append(name)


def check_true(name, cond, why=""):
    check(name if not why else f"{name} ({why})", bool(cond), True)


# A stock ROCKNIX mako config as mako-notify seeds it on first use, plus a
# foreign criteria the operator might have added. Both must survive.
STOCK = """max-visible=1
layer=overlay
font=monospace 30
text-color=#ffffff
text-alignment=center
background-color=#000000
border-size=0
border-radius=10
default-timeout=1500
anchor=top-center
width=500

[urgency=critical]
border-color=#ff0000
"""

# What a 0.8.3 rig looks like before this change — the block we must migrate.
LEGACY = """
[app-name="ETK Pitstop"]
anchor=top-center
width=1280
height=560
font=monospace 24
default-timeout=8000
"""


def _install_block_script():
    """The REAL ETKMAKO heredoc body from install.sh, retargeted at a temp
    config. Extracting it (rather than restating it) is the point: a drift
    between this test and the shipped installer would defeat the test."""
    src = open(INSTALL_SH).read()
    m = re.search(r"<<'ETKMAKO'\n(.*?)\nETKMAKO\n", src, re.S)
    assert m, "could not find the ETKMAKO heredoc in install.sh"
    return m.group(1)


def _uninstall_block_script():
    """The mako removal from uninstall.sh, rendered exactly as its UNQUOTED
    'CLEAN' heredoc delivers it to the rig (so the \\$ escaping is tested,
    not assumed)."""
    src = open(UNINSTALL_SH).read()
    m = re.search(r'(    # ETK mako notification style.*?\n    fi\n)', src, re.S)
    assert m, "could not find the mako removal block in uninstall.sh"
    r = subprocess.run(["bash", "-c", "cat << 'X_NOEXPAND_X'\n" + "PLACEHOLDER"
                        + "\nX_NOEXPAND_X"], capture_output=True, text=True)
    # Render through a real unquoted heredoc, which is what the rig sees.
    r = subprocess.run(["bash", "-c", "cat << CLEAN\n" + m.group(1) + "\nCLEAN"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _run_block(script, cfg_path, stubdir):
    """Run a config-surgery block against `cfg_path` with the rig-only tools
    stubbed out."""
    script = script.replace("MCFG=/storage/.config/mako/config",
                            f"MCFG={cfg_path}")
    env = dict(os.environ, PATH=stubdir + os.pathsep + os.environ["PATH"])
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env=env)


def _make_stubs(tmp, mako_running=True, reload_ok=True):
    d = os.path.join(tmp, "stub")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "pgrep"), "w") as f:
        f.write("#!/bin/sh\nexit %d\n" % (0 if mako_running else 1))
    with open(os.path.join(d, "makoctl"), "w") as f:
        f.write("#!/bin/sh\nexit %d\n" % (0 if reload_ok else 1))
    for n in ("pgrep", "makoctl"):
        os.chmod(os.path.join(d, n), 0o755)
    return d


# ==========================================================
print("\n[CONTRACT] senders and installer agree on the mako criteria")
# ==========================================================
install_src = open(INSTALL_SH).read()
notify_src = open(NOTIFY_SH).read()

headers = re.findall(r'^\[app-name="([^"]+)"\]', install_src, re.M)
check("install.sh declares exactly two ETK surfaces",
      sorted(h for h in headers if h.startswith("ETK")),
      ["ETK", "ETK Progress"])
check("Pitstop toast app-name matches a criteria header",
      pit.NOTIFY_APP in headers, True)
check("Pitstop progress app-name matches a criteria header",
      pit.NOTIFY_APP_PROGRESS in headers, True)
check("etk_notify.sh toast app-name matches",
      f'APP_TOAST="{pit.NOTIFY_APP}"' in notify_src, True)
check("etk_notify.sh progress app-name matches",
      f'APP_PROGRESS="{pit.NOTIFY_APP_PROGRESS}"' in notify_src, True)

# The config-killer: max-visible is rejected inside an app-name criteria.
etk_blocks = re.findall(r'^\[app-name="ETK[^"]*"\]\n(.*?)(?=^\[|\nMK\n)',
                        install_src, re.S | re.M)
check_true("no max-visible inside an ETK criteria",
           etk_blocks and not any("max-visible" in b for b in etk_blocks),
           "mako refuses the whole config, killing all notifications on next boot")
check_true("no `sort`/`max-history` (global-only) inside an ETK criteria",
           not any(re.search(r'^\s*(sort|max-history)\s*=', b, re.M)
                   for b in etk_blocks))
check_true("progress criteria declares a progress-color",
           any("progress-color" in b for b in etk_blocks))
check_true("install.sh rolls back if mako rejects the config",
           "etk.bak" in install_src and "[FAIL]" in install_src)

# GEOMETRY. mako's default format is "<b>%s</b>\n%b" — summary and body are
# two pango PARAGRAPHS — and mako caps the text layout at
# (height - 2*border - 2*padding) via pango_layout_set_height(). A positive
# cap defeats pango's "at least one line per paragraph" rule, so a box too
# short for two lines renders the summary ALONE and the body vanishes. mako's
# built-in default height is 100, which is NOT enough at these font sizes:
# omitting `height` silently deletes the payload of nearly every ETK
# notification. Require room for >= 3 lines so a wrapped body still fits.
#
# Line height is ~1.55x the pt size on the rig's Liberation Mono (measured:
# 40px at 26pt, 34px at 22pt via pangocairo against its metric twin Courier
# New). Kept deliberately conservative — the failure is invisible on a host.
LINE_RATIO = 1.55
MIN_LINES = 3
GEOM = {}
for name, block in zip(re.findall(r'^\[app-name="(ETK[^"]*)"\]', install_src, re.M),
                       etk_blocks):
    def opt(key, default=None):
        m = re.search(rf'^{key}=(\S+)', block, re.M)
        return m.group(1) if m else default
    height = opt("height")
    check_true(f"[{name}] declares an explicit height",
               height is not None,
               "mako's default 100 drops the body line")
    if height is None:
        continue
    pad = int(opt("padding", "0"))
    border = int(opt("border-size", "0"))
    fsize = float(re.search(r'^font=\S+\s+([0-9.]+)', block, re.M).group(1))
    usable = int(height) - 2 * border - 2 * pad
    need = MIN_LINES * LINE_RATIO * fsize
    check_true(f"[{name}] fits >= {MIN_LINES} lines "
               f"({usable}px usable vs {need:.0f}px needed)",
               usable >= need)
    GEOM[name] = {"width": int(opt("width", "0")),
                  "margin": opt("margin", "0"),
                  "anchor": opt("anchor", "")}

# The two surfaces are independent layer-surfaces at different anchors, so
# sway composites them side by side and nothing prevents a collision. On the
# rig this showed up as the centred toast running under the top-right card
# and muddying its text. Assert they cannot overlap at their MAXIMUM widths.
PANEL_W = 1920
if {"ETK", "ETK Progress"} <= set(GEOM):
    toast, card = GEOM["ETK"], GEOM["ETK Progress"]
    toast_right = PANEL_W / 2 + toast["width"] / 2
    card_margin_right = int(card["margin"].split(",")[1])
    card_left = PANEL_W - card_margin_right - card["width"]
    check_true(f"toast and progress card cannot overlap "
               f"(toast ends {toast_right:.0f}px, card starts {card_left}px)",
               toast_right <= card_left,
               "measured on the 1920x1080 panel")

# ==========================================================
print("\n[MAKO] config surgery — real installer text, temp config")
# ==========================================================
tmp = tempfile.mkdtemp()
try:
    stub = _make_stubs(tmp)
    cfg = os.path.join(tmp, "config")
    with open(cfg, "w") as f:
        f.write(STOCK + LEGACY)

    r = _run_block(_install_block_script(), cfg, stub)
    check("install reports OK", "[OK]" in r.stdout, True)
    after1 = open(cfg).read()
    check("legacy 0.8.3 block removed", '[app-name="ETK Pitstop"]' in after1, False)
    check("toast surface installed", '[app-name="ETK"]' in after1, True)
    check("progress surface installed", '[app-name="ETK Progress"]' in after1, True)
    check("ROCKNIX global preserved", "max-visible=1" in after1.split("[")[0], True)
    check("foreign criteria preserved", "[urgency=critical]" in after1, True)

    _run_block(_install_block_script(), cfg, stub)
    after2 = open(cfg).read()
    check("re-install is byte-identical (idempotent)", after2, after1)
    check("toast block appears exactly once",
          after2.count('[app-name="ETK"]'), 1)

    # The bug this test exists to catch: the 0.8.3 step grew the operator's
    # config by one blank line per install, forever.
    for _ in range(3):
        _run_block(_install_block_script(), cfg, stub)
    check("no blank-line growth over repeated installs",
          open(cfg).read(), after1)

    r = _run_block(_uninstall_block_script(), cfg, stub)
    cleaned = open(cfg).read()
    check("uninstall leaves no ETK residue", "ETK" in cleaned, False)
    check("uninstall restores the original file", cleaned.rstrip("\n"),
          STOCK.rstrip("\n"))

    # Rollback: mako refusing the config must leave the file untouched.
    cfg2 = os.path.join(tmp, "config2")
    with open(cfg2, "w") as f:
        f.write(STOCK)
    bad = _make_stubs(os.path.join(tmp, "bad"), mako_running=True, reload_ok=False)
    r = _run_block(_install_block_script(), cfg2, bad)
    check("rejected config reports FAIL", "[FAIL]" in r.stdout, True)
    check("rejected config is rolled back byte-for-byte",
          open(cfg2).read(), STOCK)
    check("rollback leaves no .bak litter",
          os.path.exists(cfg2 + ".etk.bak"), False)
finally:
    import shutil as _sh
    _sh.rmtree(tmp, ignore_errors=True)

# ==========================================================
print("\n[NOTIFY] _Notifier against a stubbed bus")
# ==========================================================
CALLS = []


class _Fake:
    def __init__(self, out):
        self.stdout = out
        self.returncode = 0


def fake_run(cmd, **kw):
    CALLS.append(list(cmd))
    if cmd[0] == "busctl":
        return _Fake("u 77\n")
    if cmd[0] == "dbus-send":
        if any("CloseNotification" in c for c in cmd):
            return _Fake("")
        return _Fake("method return time=1.1 sender=:1.5\n   uint32 42\n")
    return _Fake("")


REAL_RUN, REAL_WHICH = pit.subprocess.run, pit.shutil.which
HAVE_BUSCTL = [True]
pit.subprocess.run = fake_run
pit.shutil.which = lambda n: "/usr/bin/busctl" if (
    n == "busctl" and HAVE_BUSCTL[0]) else None
try:
    pit._Notifier._busctl = None
    CALLS.clear()
    n = pit._Notifier()
    n.post("INSTALL COMPLETE", "GT5P")
    check("plain toast uses dbus-send", CALLS[0][0], "dbus-send")
    check("toast carries the toast app-name",
          f"string:{pit.NOTIFY_APP}" in CALLS[0], True)
    check("notification id captured from the reply", n._id, "42")

    CALLS.clear()
    n.post("AGAIN", "x")
    check("update replaces in place (replaces_id reused)",
          "uint32:42" in CALLS[0], True)

    CALLS.clear()
    p = pit._Notifier(pit.NOTIFY_APP_PROGRESS)
    p.post("INSTALLING", "GT5P", value=47)
    c = CALLS[0]
    check("progress uses busctl", c[0], "busctl")
    check("busctl signature is susssasa{sv}i", "susssasa{sv}i" in c, True)
    i = c.index("value")
    check("hint type is int32 (uint32 would fail the whole call)", c[i + 1], "i")
    check("hint value passed through", c[i + 2], "47")

    for raw, want in ((250, "100"), (-10, "0"), (99.7, "99")):
        CALLS.clear()
        p.post("X", "y", value=raw)
        c = CALLS[0]
        check(f"value {raw} clamped", c[c.index("value") + 2], want)

    CALLS.clear()
    p.close()
    check("close calls CloseNotification",
          any("CloseNotification" in x for x in CALLS[0]), True)
    check("close clears the id", p._id, "0")
    CALLS.clear()
    p.close()
    check("second close is a no-op", CALLS, [])

    HAVE_BUSCTL[0] = False
    pit._Notifier._busctl = None
    CALLS.clear()
    d = pit._Notifier(pit.NOTIFY_APP_PROGRESS)
    d.post("INSTALLING", "GT5P", value=50)
    check("without busctl the toast STILL goes out", CALLS[0][0], "dbus-send")
    check("without busctl the bar degrades to an ASCII meter",
          any("[" in str(a) and "#" in str(a) for a in CALLS[0]), True)

    def boom(cmd, **kw):
        raise OSError("bus gone")

    pit.subprocess.run = boom
    pit._Notifier._busctl = None
    try:
        pit._Notifier().post("X", "y")
        pit._Notifier(pit.NOTIFY_APP_PROGRESS).post("X", "y", value=10)
        pit._Notifier().close()
        check("a dead bus never raises into the caller", True, True)
    except Exception as e:
        check("a dead bus never raises into the caller", repr(e), "no exception")
    pit.subprocess.run = fake_run

    # ==========================================================
    print("\n[CARD] _ProgressCard lifecycle")
    # ==========================================================
    HAVE_BUSCTL[0] = True
    pit._Notifier._busctl = None
    CALLS.clear()
    card = pit._ProgressCard("INSTALLING", "GT5P",
                             frac=lambda: 0.25, interval=0.15).start()
    time.sleep(0.55)
    card.stop()
    posts = [c for c in CALLS if "Notify" in " ".join(map(str, c))]
    check_true("card heartbeats while the job runs", len(posts) >= 3,
               f"{len(posts)} posts")
    check("every re-post resends the hint (mako clears it otherwise)",
          all("value" in c for c in posts), True)
    check("stop() dismisses the card",
          any("CloseNotification" in " ".join(map(str, c)) for c in CALLS), True)

    CALLS.clear()
    card2 = pit._ProgressCard("INSTALLING FIRMWARE", "PS3UPDAT.PUP",
                              interval=0.15).start()
    time.sleep(0.3)
    card2.stop()
    plain = [c for c in CALLS
             if c[0] == "dbus-send" and not any("Close" in x for x in c)]
    check_true("indeterminate card posts without a hint", bool(plain))
    check_true("indeterminate card shows elapsed time, not a fake percentage",
               any(re.search(r"\d:\d\d", str(a)) for a in plain[0]))

    CALLS.clear()
    bad_card = pit._ProgressCard("X", "y", frac=lambda: 1 / 0,
                                 interval=0.15).start()
    time.sleep(0.3)
    bad_card.stop()
    check("a throwing frac() never kills the card thread", True, True)
finally:
    pit.subprocess.run, pit.shutil.which = REAL_RUN, REAL_WHICH

# ==========================================================
print("\n[SHELL] bin/etk_notify.sh exit-status contract")
# ==========================================================
# osguard retries this sender for a minute at boot, because it can run before
# sway has started mako. That retry is keyed on the EXIT STATUS, so a sender
# that always returned 0 would break out on the first failed attempt and lose
# the only on-screen recovery instruction the operator gets. Pin the contract:
# 0 only on delivery.
tmp = tempfile.mkdtemp()
try:
    def _bus(name, ok=True, with_id=True):
        d = os.path.join(tmp, name)
        os.makedirs(d, exist_ok=True)
        for tool in ("dbus-send", "busctl"):
            p = os.path.join(d, tool)
            if ok and with_id:
                body = ('#!/bin/sh\ncase "$*" in *CloseNotification*) exit 0 ;; esac\n'
                        'printf "method return\\n   uint32 42\\n"\n')
            elif ok:
                body = '#!/bin/sh\nprintf "method return\\n"\n'
            else:
                body = '#!/bin/sh\necho "no bus" >&2\nexit 1\n'
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, 0o755)
        return d

    def _run_sender(busdir, *args):
        env = dict(os.environ, PATH=busdir + os.pathsep + "/bin" + os.pathsep + "/usr/bin")
        return subprocess.run(["sh", NOTIFY_SH, *args], capture_output=True,
                              text=True, env=env)

    live, dead, noid = _bus("live"), _bus("dead", ok=False), _bus("noid", with_id=False)

    r = _run_sender(live, "OK", "body")
    check("delivered toast exits 0", r.returncode, 0)
    check("delivered toast echoes the id", r.stdout.strip(), "42")
    check("delivered progress exits 0",
          _run_sender(live, "--progress", "P", "n", "40").returncode, 0)
    check_true("undelivered toast exits non-zero",
               _run_sender(dead, "OK", "body").returncode != 0,
               "osguard's boot retry depends on this")
    check_true("undelivered progress exits non-zero",
               _run_sender(dead, "--progress", "P", "n", "40").returncode != 0)
    check_true("a reply carrying no id exits non-zero",
               _run_sender(noid, "OK", "body").returncode != 0)
    check("empty summary is a clean no-op",
          _run_sender(live, "").returncode, 0)

    # The loop body osguard actually runs, against a bus that never comes up.
    loop = ('i=0; while [ $i -lt 12 ]; do '
            f'if sh {NOTIFY_SH} "ETK OS GUARD" "msg" 15000 >/dev/null 2>&1; '
            'then break; fi; i=$((i+1)); done; echo $i')
    env = dict(os.environ, PATH=dead + os.pathsep + "/bin" + os.pathsep + "/usr/bin")
    out = subprocess.run(["sh", "-c", loop], capture_output=True, text=True,
                         env=env).stdout.strip()
    check("osguard's retry loop runs all 12 attempts while the bus is down",
          out, "12")
    env = dict(os.environ, PATH=live + os.pathsep + "/bin" + os.pathsep + "/usr/bin")
    out = subprocess.run(["sh", "-c", loop], capture_output=True, text=True,
                         env=env).stdout.strip()
    check("and stops on the first successful delivery", out, "0")
finally:
    import shutil as _sh2
    _sh2.rmtree(tmp, ignore_errors=True)

# A muted notifier drops posts. The background installer mutes itself while it
# kills its own emulator to yield to a game, because the INSTALL FAILED that
# kill produces is not the verdict worth showing — INSTALL PAUSED is. Without
# this the operator sees both, contradicting each other (rig, 2026-08-06).
_real_run = pit.subprocess.run
pit.subprocess.run = fake_run
n = pit._Notifier()
CALLS.clear()
n.post("BEFORE", "x")
n.mute()
n.post("SUPPRESSED", "this is our own doing")
n.unmute()
n.post("AFTER", "x")
flat = [a for c in CALLS for a in c]
check_true("the post before muting goes out", "string:BEFORE" in flat)
check("a muted notifier drops the post", "string:SUPPRESSED" in flat, False)
check_true("posts resume after unmute", "string:AFTER" in flat)
pit.subprocess.run = _real_run

# ==========================================================
print("\n[BEACON] install.sh announces itself on the rig's own screen")
# ==========================================================
# An ETK install must be impossible to run without the person holding the
# handheld noticing: install.sh drives an "ETK INSTALL" progress card on the
# rig for the whole run. That is a SECURITY property, so what it needs is not
# "the toast usually appears" but four structural guarantees that no future
# edit can quietly erode:
#
#   ALWAYS ON   no env var, no etk.conf knob, no kill-switch may skip a
#               beacon. A switch is exactly what a silent installer would
#               flip, so the checks below refuse a gated call site outright.
#   FAIL-SOFT   a beacon may never break or block an install, so every call
#               site carries `|| true`.
#   MONOTONIC   the percentages are a static hand-weighted table; out-of-order
#               or non-terminating values make the card lie.
#   NO SILENCE  the announcement precedes the first rig mutation, and every
#               TUI step is announced (the 6.x sub-stages between step 5 and
#               step 6 are a 1,300-line window with no TUI calls at all).
#
# WHAT THESE DO NOT PROVE: they are static reads of the source. "Top level"
# is inferred from column 0 plus the function/heredoc range scan below, which
# holds because this file indents every nested block; they cannot prove the
# rig actually rendered anything. That is the operator's screen to verify.
#
# EVERY check below is written to FAIL, not raise, when the feature is absent
# — the suite is run against a deliberately broken copy via ETK_REPO_ROOT, and
# a traceback there proves nothing and hides the checks behind it.
LINES = install_src.split("\n")
CALLS = [(i + 1, ln) for i, ln in enumerate(LINES) if re.match(r"^\s*rig_toast\s", ln)]


def _find(pat, default=None):
    """First 1-based line matching `pat`, or `default` — never raises."""
    for i, ln in enumerate(LINES):
        if re.match(pat, ln):
            return i + 1
    return default


check_true("rig_toast is defined in install.sh",
           bool(re.search(r"^rig_toast\(\)\s*\{", install_src, re.M)))
check_true("install.sh carries beacons at more than the 8 TUI steps",
           len(CALLS) >= 20, f"{len(CALLS)} call sites")

# --- ALWAYS ON -------------------------------------------------------------
# Anything indented is inside a block, and a block in this file is opened by
# `if`/`case`/`while`/`for` — i.e. by a condition that could skip the beacon.
check_true("every beacon is a bare top-level statement (nothing can gate it)",
           CALLS and all(ln.startswith("rig_toast ") for _, ln in CALLS),
           "an indented call site is inside a conditional")

# A call site inside a function body may simply never be called; one inside a
# quoted heredoc is dead text shipped to the rig. Both are silent failures.
def _fn_ranges(lines):
    out, start = [], None
    for i, ln in enumerate(lines, 1):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{\s*$", ln):
            start = i
        elif ln == "}" and start is not None:
            out.append((start, i))
            start = None
    return out


def _heredoc_ranges(lines):
    """Line spans covered by a heredoc body, so a beacon buried in remote
    script text is caught. Only the last redirect on a line is tracked, which
    is all install.sh ever uses."""
    out, tag, start = [], None, None
    for i, ln in enumerate(lines, 1):
        if tag is None:
            m = re.search(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?\s*$", ln)
            if m:
                tag, start = m.group(1), i
        elif ln.strip() == tag:
            out.append((start, i))
            tag = None
    return out


FN = _fn_ranges(LINES)
HD = _heredoc_ranges(LINES)
DEF_LINE = _find(r"^rig_toast\(\)\s*\{")
check_true("no beacon is buried in a function body or a heredoc",
           CALLS and not [n for n, _ in CALLS
                          if any(a < n < b for a, b in FN)
                          or any(a < n < b for a, b in HD)])

# The beacon must read no operator-settable knob: not an etk.conf key, not an
# ETK_* env var beyond its own state. $ETK_ROOT and $RIG_SSH are addresses, not
# switches, so they are allowed.
CONF_KEYS = set(re.findall(r"^([A-Z][A-Z0-9_]*)=",
                           open(os.path.join(ROOT, "etk.conf.example")).read(), re.M))
ALLOWED = {"RIG_SSH", "ETK_ROOT"}
BODY = ""
if DEF_LINE:
    end = next((i + 1 for i, ln in enumerate(LINES)
                if i + 1 > DEF_LINE and ln == "}"), len(LINES))
    BODY = "\n".join(LINES[DEF_LINE - 1:end])
used_conf = sorted((CONF_KEYS - ALLOWED) & set(re.findall(r"[A-Z][A-Z0-9_]*", BODY)))
check("rig_toast reads no etk.conf knob", (bool(BODY), used_conf), (True, []))
# Scan the call line AND the two lines above it: the cheapest way to smuggle a
# kill-switch back in is an `if [ "$SOME_KNOB" = "1" ]` wrapped round a beacon.
# Comment lines are excluded: several stage banners legitimately NAME their
# own kill-switch in prose right above the beacon that must ignore it.
gated = [n for n, _ in CALLS
         for ctx in ["\n".join(x for x in LINES[max(0, n - 3):n]
                               if not x.lstrip().startswith("#"))]
         if any(k in ctx for k in (CONF_KEYS - ALLOWED))]
check("no call site is gated by a knob", (bool(CALLS), gated), (True, []))
check_true("no kill-switch named for the beacon exists anywhere in install.sh",
           not re.search(r"ETK_(TOAST|BEACON|ANNOUNCE)_(ENABLE|OFF|DISABLE|SKIP)",
                         install_src),
           "ALWAYS ON is the whole security property")

# --- VIRGIN / STALE RIG ----------------------------------------------------
# The beacon starts before STEP 3 has pushed bin/, so it bootstraps its own
# sender. Its probe greps the rig's sender for the `--progress` case label:
# same byte-exact coupling as the mako app-names above, same failure mode if
# it drifts — the probe silently stops matching and every install falls back
# to the /tmp copy. Pin the two together.
PROBE = re.search(r"grep -q '(\^--progress\))'", install_src)
check_true("the beacon probes the rig sender for the progress form",
           PROBE is not None)
if PROBE:
    check_true("that probe pattern matches bin/etk_notify.sh",
               re.search(r"^--progress\)", notify_src, re.M) is not None,
               "drift here demotes every install to the /tmp fallback")
check_true("the beacon falls back to a scp'd sender on a virgin rig",
           "/tmp/etk_notify.sh" in install_src)

# --- FAIL-SOFT -------------------------------------------------------------
check_true("every beacon call site is `|| true`-guarded",
           CALLS and all(ln.rstrip().endswith("|| true") for _, ln in CALLS),
           "a beacon may never break or block an install")

# --- QUOTING ---------------------------------------------------------------
# The label crosses into a single-quoted word inside a remote shell command.
# A label carrying operator or config data would be a remote-execution seam.
LABELS = [re.match(r'^rig_toast\s+(\d+)\s+"([^"]*)"', ln) for _, ln in CALLS]
check("every call site is `rig_toast <int> \"<literal>\"`",
      (bool(CALLS), [ln for m, (_, ln) in zip(LABELS, CALLS) if m is None]),
      (True, []))
LBL = [m.group(2) for m in LABELS if m]
check_true("no label interpolates a variable, command, or quote",
           LBL and not any(re.search(r"[$`'\\]", s) for s in LBL))
check_true("every label is plain ASCII and <= 40 chars",
           LBL and all(s.isascii() and s.isprintable() and len(s) <= 40
                       for s in LBL))

# --- MONOTONIC -------------------------------------------------------------
PCTS = [int(m.group(1)) for m in LABELS if m]
check_true("percentages are monotonic non-decreasing in file order",
           PCTS and all(a <= b for a, b in zip(PCTS, PCTS[1:])),
           f"{PCTS}")
check("the last beacon is 100", PCTS[-1] if PCTS else None, 100)
check_true("percentages stay in range",
           PCTS and all(0 <= p <= 100 for p in PCTS))

# --- NO SILENCE ------------------------------------------------------------
# STEP 0's pkill is the first byte install.sh changes on the rig. The whole
# point of the feature is that the handheld has already said so by then.
QUIESCE = next((i + 1 for i, ln in enumerate(LINES)
                if "pkill -f vault_d.sh" in ln), 0)
FIRST = CALLS[0][0] if CALLS else None
check_true(f"a beacon fires before the STEP 0 quiesce "
           f"(line {FIRST} vs {QUIESCE})", FIRST is not None and FIRST < QUIESCE,
           "announcing after the first mutation defeats the purpose")
check_true("the first beacon opens at <= 2%", bool(PCTS) and PCTS[0] <= 2)
# ...and it is not merely somewhere before the pkill: it precedes tui_init,
# i.e. install.sh announces itself before it so much as takes over the
# operator's terminal. Deleting the opening beacon and leaning on the STEP 0
# one would still clear the pkill line by three lines; this is what pins it.
TUI_INIT = _find(r"^\s*tui_init\s*$")
check_true(f"the first beacon precedes tui_init (line {FIRST} vs {TUI_INIT})",
           FIRST is not None and TUI_INIT is not None and FIRST < TUI_INIT,
           "the announcement belongs to no step; it comes before all of them")

# Every TUI step must be announced, or the card goes stale for the length of
# a whole step (STEP 4's vault push and STEP 6.55's RPCS3 staging are minutes).
STEP_STARTS = [i + 1 for i, ln in enumerate(LINES)
               if re.match(r"^\s*tui_step_start\s", ln)]
unannounced = [n for n in STEP_STARTS
               if not any(abs(n - c) <= 2 for c, _ in CALLS)]
check("every tui_step_start site has a beacon within 2 lines", unannounced, [])

# The 6.x gap: tui_step_done 5 -> tui_step_start 6 has no TUI calls at all.
GAP_A = max((i + 1 for i, ln in enumerate(LINES)
             if re.match(r"^\s*tui_step_done 5\s*$", ln)), default=0)
GAP_B = next((i + 1 for i, ln in enumerate(LINES)
              if re.match(r"^\s*tui_step_start 6\s*$", ln)), 0)
in_gap = [n for n, _ in CALLS if GAP_A < n < GAP_B]
check_true(f"the silent 6.x window (lines {GAP_A}-{GAP_B}) carries beacons",
           len(in_gap) >= 10, f"{len(in_gap)} beacons")

# --- VERDICTS --------------------------------------------------------------
TAIL = "\n".join(LINES[CALLS[-1][0] - 1:]) if CALLS else ""
check_true("the success path closes the card", "--close" in TAIL)
check_true("the success path sends the COMPLETE verdict",
           "'ETK INSTALL COMPLETE'" in TAIL)
check_true("the success path fires 100 before closing",
           re.search(r'rig_toast 100 "[^"]*"', TAIL) is not None)
check_true("a failed install gets a STOPPED verdict",
           "'ETK INSTALL STOPPED'" in install_src)
check_true("the STOPPED verdict hangs off an EXIT trap",
           re.search(r"^trap\s+etk_toast_stopped\s+EXIT", install_src, re.M)
           is not None)
check_true("the STOPPED handler cannot mask the exit code",
           re.search(r"etk_toast_stopped\(\)\s*\{\s*\n\s*local rc=\$\?",
                     install_src) is not None
           and "exit $rc" in install_src)
check_true("the terminal verdict is guarded against double-firing",
           "ETK_TOAST_FIRED" in install_src
           and re.search(r'\[ "\$ETK_TOAST_FIRED" = "1" \]', install_src)
           is not None)
# tui_fail exits THROUGH tui_cleanup, which runs `trap - INT TERM EXIT` and so
# disarms the EXIT trap before the shell reaches it. Those are install.sh's
# loudest stops (a Sentry that will not start), so the verdict has to be sent
# at the call site or the card just fades out 45 s later saying nothing.
uncovered = [i + 1 for i, ln in enumerate(LINES)
             if re.match(r"^\s*tui_fail\s", ln)
             and "etk_toast_verdict_stopped" not in LINES[max(0, i - 1)]]
check("every tui_fail site sends the STOPPED verdict first", uncovered, [])
# tui.sh's own `trap tui_cleanup INT TERM EXIT` runs at tui_init and would
# clobber an EXIT trap armed before it. Both arms must be present.
check("the EXIT trap is armed on both sides of tui_init",
      len(re.findall(r"^trap\s+etk_toast_stopped\s+EXIT", install_src, re.M)), 2)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL NOTIFICATION CHECKS PASSED")
