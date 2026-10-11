#!/usr/bin/env python3
"""Host tests for gt5_trial: the pure helpers, and the DEATH PATH end to end against a fake
car (Pitlink, garage, ssh and the clock all faked): a car that flips, goes dark, and comes
back on a new boot must read PANIC with the dead boot's evidence harvested; a car that never
comes back must read UNREACHABLE; a car that stays up but stops flipping must read FREEZE.

  python3 tools/autopilot/test_gt5_trial.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("gt5_trial", os.path.join(HERE, "gt5_trial.py"))
gt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gt)

FAILS = []


def check(desc, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
    if not ok:
        print(f"          got  {got!r}\n          want {want!r}")
        FAILS.append(desc)


print("[1] parse_macro")
steps = gt.parse_macro("# GT5 Arcade\nsleep 2\npress cross\npress down+cross 200\n"
                       "hold r2=0.8 lx=-0.3 secs=3\nwait_lit 60\nsnap menu  # look\n"
                       "drive --no-map --restart-every 40\n")
check("all verbs parsed", [s[0] for s in steps], ["sleep", "press", "press", "hold", "wait_lit", "snap", "drive"])
check("drive keeps gtdrive's args verbatim", steps[-1][1], ["--no-map", "--restart-every", "40"])
check("observe N parses", gt.parse_macro("observe 20")[0], ("observe", 20.0))
check("press default 120 ms, chords split on +", (steps[1][1], steps[2][1]), ((["cross"], 120), (["down", "cross"], 200)))
check("hold controls are floats, secs split off", steps[3][1], ({"r2": 0.8, "lx": -0.3}, 3.0))
for bad in ("jump 3", "hold r2=0.8", "sleep soon"):
    try:
        gt.parse_macro("sleep 1\n" + bad)
        check(f"bad line {bad!r} raises", False, True)
    except ValueError as e:
        check(f"bad line {bad!r} raises, naming line 2", "line 2" in str(e), True)

print("\n[2] previous_boot_file / last_game_sample / pon_verdict_of")
check("dead boot = second newest by mtime",
      gt.previous_boot_file(["/bb/flightrec-9.tsv\n", "/bb/flightrec-532654479.tsv\n", "/bb/flightrec-1.tsv\n"]),
      "/bb/flightrec-532654479.tsv")
check(".old rotations skipped", gt.previous_boot_file(["a.tsv", "a.tsv.old", "b.tsv"]), "b.tsv")
check("only the current boot -> None", gt.previous_boot_file(["a.tsv"]), None)
TSV = ("# epoch\tgame\tmem_avail_kb\tswap_free_kb\tpsi_mem_some10\tpsi_mem_full10\tpsi_cpu_some10\t"
       "psi_io_some10\tload1\ttmax_c\tgpu_mhz\n"
       "1\t-\t7000000\t3867644\t0.00\t0.00\t1.00\t0.00\t0.5\t50\t305\n"
       "2\tBCUS98114\t3110000\t3867644\t0.00\t0.00\t12.0\t0.1\t6.1\t70\t925\n"
       "3\tBCUS98114\t2109000\t3867644\t0.00\t0.00\t14.5\t0.1\t6.5\t77\t925\n"
       "4\tBCUS98114\ttorn")
s = gt.last_game_sample(TSV)
check("last full GT5 sample (a torn last line is ignored)", (s["epoch"], s["tmax_c"]), ("3", "77"))
check("min MemAvailable over GT5 samples", s["min_mem_avail_kb"], 2109000)
check("game never ran -> None", gt.last_game_sample(TSV, "NPEA00050"), None)
J = ("1791752000.100000 airM1alx kernel: usb 1-1: USB disconnect, device number 1\n"
     "1791753164.243750 airM1alx kernel: usb 1-1: USB disconnect, device number 5\n")
check("host USB disconnect after the last sample (t4: hung 6.2 s)",
      round(gt.host_usb_gone(1791753158, journal=J) - 1791753158, 1), 6.2)
check("no disconnect -> None", gt.host_usb_gone(1791753158, journal=""), None)
check("pon verdict read from header", gt.pon_verdict_of("# epoch=1 boot_id=x uptime_s=9.0 verdict=WARM\n"), "WARM")


# ---- the fake car -------------------------------------------------------------------------
class Clock:
    t = 1000.0


class FakeFrame:
    def rgb(self):
        return self

    def mean(self):
        return 90.0

    def png(self, path):
        open(path, "wb").close()


def run_scenario(die_at=None, back_at=None, freeze_at=None, secs=600, lit_frames=True, granted=True, uvlo=False, oom_at=None):
    """Run gt5_trial.main() against a fake car. die_at/back_at/freeze_at are seconds after
    launch. Returns (exit code, verdict dict, out dir)."""
    Clock.t = 1000.0
    launched = {}

    def now():
        return Clock.t

    def sleep(s):
        Clock.t += max(s, 0.5)

    def rel():
        return Clock.t - launched.get("t", Clock.t)

    def dead():
        return die_at is not None and launched and die_at <= rel() and (back_at is None or rel() < back_at)

    def rebooted():
        return back_at is not None and launched and rel() >= back_at

    class FakeClient:
        closed = False

        def __init__(self, *a, **k):
            if dead():
                raise OSError("no USB")

        def hello(self):
            return {}

        def ping(self):
            if dead() or rebooted():
                raise gt.PitlinkError("link down")
            r = rel()
            flip = int(min(r, freeze_at) * 30) if freeze_at is not None else int(r * 30)
            return flip, 0

        def latest_frame(self):
            return FakeFrame() if lit_frames else None

    def fake_garage(req, timeout=30.0):
        if dead():
            raise ConnectionRefusedError("USB link down")
        op = req["op"]
        if op == "debug_env":
            return {"env": {}}
        if op == "running":
            return {"running": {"name": "GT5"} if launched and not rebooted() else None}
        if op == "launch":
            launched["t"] = Clock.t
            return {"launched": {}}
        if op == "log":
            if oom_at is not None and rel() >= oom_at and "Cannot allocate" in req.get("grep", ""):
                return {"size": 10, "lines": 400, "text": "·E lv2_memory::on_id_create(): Cannot allocate 0x100000"}
            if oom_at is not None and "user_memory_size" in req.get("grep", ""):
                return {"size": 10, "lines": 1, "text": "sys_memory_get_user_memory_size: Avail=0x98000, Total=0xd500000"}
            return {"size": 10, "lines": 0, "text": ""}
        if op == "status":
            return {"daemon": "fake", "running": None}
        if op == "dump_threads":
            return {"text": "PPU[0x1000000]; State: ..."}
        raise AssertionError(op)

    ledger = "x\tCLEAN\ny\tCLEAN\n"

    def fake_ssh(host, cmd, timeout=15):
        if dead() or (not launched and False):
            return 255, ""
        boot = "boot-B" if rebooted() else "boot-A"
        if cmd.startswith("cat /proc/sys/kernel/random/boot_id"):
            return 0, boot + "\n"
        if cmd == gt.PON_READ:
            regs = {0x804: 0x01, 0x805: 0x04, 0x8C0: 0x81, 0x8C2: 0x02 if rebooted() else 0, 0x8C5: 0x02,
                    0x8C8: 0x40 if uvlo and rebooted() else 0}
            win = "".join(f"{a:04x}: {regs.get(a, 0):02x}\n" for a in range(0x800, 0x8E0))
            return 0, "== 0-00\n" + win + "== 0-01\n" + "".join(f"{a:04x}: 00\n" for a in range(0x800, 0x8E0))
        if "pon-*.txt" in cmd and "capacity" in cmd:
            return 0, "/bb/pon-1.txt\n87\n"
        if "pon-*.txt" in cmd:
            return 0, ("/bb/pon-2.txt\n" if rebooted() else "/bb/pon-1.txt\n")
        if cmd.startswith("cat /bb/pon-2.txt"):
            return 0, (f"# epoch=2 boot_id=boot-B uptime_s=40.0 verdict={'BROWNOUT' if uvlo else 'WARM'}\n"
                       "0-00 subtype=04 warm_reset=02(PS_HOLD)\n")
        if "flightrec-*.tsv" in cmd:
            return 0, "/bb/flightrec-NEW.tsv\n/bb/flightrec-DEAD.tsv\n"
        if "kmsg-*.log" in cmd:
            return 0, "/bb/kmsg-NEW.log\n/bb/kmsg-DEAD.log\n"
        if cmd.startswith("cat /bb/flightrec-DEAD.tsv"):
            return 0, TSV
        if cmd.startswith("cat /bb/kmsg-DEAD.log"):
            return 0, "6,1,1,-;quiet\n"
        if "sessions.tsv" in cmd:
            return 0, ledger + ("1788447694\t164\tFULL\tBCUS98114\tPANIC\t0.0\n" if rebooted() else "")
        if "/pstore/" in cmd:
            return 0, "=== /bb/pstore/x/console-ramoops-0\n[ 0.97] DSI PLL(0) lock failed\n"
        if "RPCS3.log" in cmd:
            return 0, "·E 0:02:41 last line\n"
        if "tripwire" in cmd:
            return 0, "BLACKBOX: PON last reset = WARM\n"
        raise AssertionError(cmd)

    class FakeRun:
        returncode = 0 if granted else 1
        stdout = "valid" if granted else "invalid: panic budget spent (2 of 2)"
        stderr = ""

    calls = []

    def fake_run(argv, **k):
        calls.append(argv[2:])
        return FakeRun()

    gt.time.time = now
    gt.time.sleep = sleep
    gt.PitlinkClient = FakeClient
    gt.garage = fake_garage
    gt.ssh = fake_ssh
    gt.subprocess.run = fake_run
    gt.host_usb_gone = lambda after, journal=None, window=120: after + 6.5
    run_scenario.calls = calls
    gt.threading.Event.wait = lambda self, t=None: sleep(t or 0)
    out = tempfile.mkdtemp(prefix="gt5trial-")
    sys.argv = ["gt5_trial.py", "--out", out, "--secs", str(secs), "--return-secs", "300"]
    code = None
    try:
        gt.main()
    except SystemExit as e:
        code = e.code
    if not granted:
        return code, launched, out
    try:
        v = json.load(open(os.path.join(out, "verdict.json")))
    except (OSError, ValueError):
        v = {}
    return code, v, out


print("\n[3] death path: dark at +164 s, back on a new boot at +240 s")
code, v, out = run_scenario(die_at=164, back_at=240)
check("verdict PANIC, exit 1", (v.get("verdict"), code), ("PANIC", 1))
check("time to death ~164 s (link-silence threshold subtracted)", abs(v.get("died_secs", 0) - 164) <= 4, True)
check("new boot recorded", v.get("new_boot_id"), "boot-B")
check("hang before reset measured", v.get("hung_secs"), 6.5)
check("PMIC reset class harvested", v.get("pon"), "WARM")
check("direct PON read decoded by the blackbox's own decoder", v.get("pon_direct"), "WARM")
check("dead boot's last GT5 sample", (v.get("last_sample") or {}).get("min_mem_avail_kb"), 2109000)
check("ledger PANIC row captured", "\tPANIC" in v.get("ledger_row", ""), True)
check("evidence files written",
      sorted(f for f in os.listdir(out) if f.endswith((".txt", ".tsv", ".log")) and f != "trial.log"),
      ["RPCS3_dead_session.log", "flightrec_dead_boot.tsv", "kmsg_dead_boot.log", "ledger_tail.tsv",
       "pon.txt", "pon_direct.txt", "pstore.txt", "tripwire_tail.log"])
check("battery at start recorded", v.get("battery_pct_start"), "87")
check("launch gated on the panic budget; the PANIC audited (spends it)",
      [c[:4] if c[0] == "check" else c[:2] for c in run_scenario.calls],
      [["check", "--game", "BCUS98114", "--panic"], ["panic", "--trial"]])

print("\n[3b] back, but the PMIC cut power (UVLO)")
code, v, _ = run_scenario(die_at=164, back_at=240, uvlo=True)
check("verdict POWER_CUT, exit 7 (STOP)", (v.get("verdict"), code, v.get("pon")), ("POWER_CUT", 7, "BROWNOUT"))

print("\n[4] the car never comes back")
code, v, _ = run_scenario(die_at=100, back_at=None)
check("verdict UNREACHABLE, exit 4 (the hunt stops for a human)", (v.get("verdict"), code), ("UNREACHABLE", 4))

print("\n[5] the car stays up, flips stop")
code, v, out = run_scenario(freeze_at=120)
check("verdict FREEZE, exit 5", (v.get("verdict"), code), ("FREEZE", 5))
check("stopped >= --freeze-secs", v.get("stopped_secs", 0) >= 90, True)
check("thread dump banked", os.path.exists(os.path.join(out, "dump.txt")), True)
code, v, _ = run_scenario(freeze_at=120, lit_frames=False, secs=400)
check("no lit frame yet (PPU compile / loading) -> never FREEZE", (v.get("verdict"), code), ("SURVIVED", 0))

print("\n[5b] the guest runs its memory dry (0.9.1 pre-menu)")
code, v, out = run_scenario(oom_at=60)
check("verdict GUEST_OOM, exit 8", (v.get("verdict"), code), ("GUEST_OOM", 8))
check("guest memory lines banked", "Avail=0x98000" in open(os.path.join(out, "guest_memory.txt")).read(), True)

print("\n[7] no grant for BCUS98114")
code, launched, _ = run_scenario(granted=False)
check("refuses before launching anything, naming the budget",
      (isinstance(code, str) and "panic budget spent" in code, launched), (True, {}))

print("\n[6] survives the window")
code, v, _ = run_scenario(secs=300)
check("verdict SURVIVED, exit 0", (v.get("verdict"), code), ("SURVIVED", 0))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} check(s) -> {FAILS}")
    sys.exit(1)
print("ALL GT5_TRIAL CHECKS PASSED")
