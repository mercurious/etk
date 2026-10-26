#!/usr/bin/env python3
"""gt5_trial -- one unattended GT5 (BCUS98114) run that SURVIVES the car's death.

--game SERIAL (2026-10-22, RC validation) runs the same loop on another title: GT5P
(NPEA00050 PSN, BCUS98158 ISO) is the 0.10.0 matrix's N>=3 title. Only BCUS98114 -- the title
that kills the kernel -- is gated on a hunt grant and audited by `hunt.py panic`; every other
serial launches ungated (a shipping core on an operator-installed title is not bytes-to-atoms).

GT5 kills the car itself: 46 ledger PANIC rows (median 164 s in, tracks only), no kmsg
text, no pstore, memory flat at death (flightrec 2026-08-30/09-03: >= 1.9 GB available,
swap untouched, PSI 0). This trial treats the car going dark as an EVENT, not a stop:

  launch over the Pitlink garage -> optional menu macro (--macro) -> watch flips, the
  RPCS3 log and the link -> on loss, wait for the car to come back by ITSELF (the
  kernel's own panic=30 reboot; nothing here ever reboots anything) -> harvest the
  evidence the dead boot left: the new boot's PMIC reset reason (blackbox pon-*.txt),
  the dead boot's flight recorder + kmsg, the dead session's RPCS3.log, the ledger's
  PANIC row, the tripwire log.

Verdicts (verdict.json in --out; exit code):
  SURVIVED     0  alive and flipping for --secs (exit the game after: pitlink.py exit)
  PANIC        1  the car went dark and came back on a NEW boot_id; evidence harvested
  NO_GAME      2  the launch did not take, or a game was already running
  CRASH        3  RPCS3 logged a guest fault / froze the emulation (recover: gtpilot.py recover)
  UNREACHABLE  4  dark for longer than --return-secs, or back WITHOUT the kit (no Pitlink
                  garage on USB): the hunt STOPS and waits for a human
  FREEZE       5  car up, game running, flips stopped for --freeze-secs (recover: gtpilot.py recover)
  EXITED       6  the game left on its own (back at ES), the car never went down
  POWER_CUT    7  the car came back, but its PMIC cut power (BROWNOUT / OVERTEMP / PMIC_WD):
                  a hardware question, the hunt STOPS (AUTONOMY_SPEC §6 panic budget)
  GUEST_OOM    8  the PS3 guest ran its user memory dry and loops on CELL_ENOMEM (0.9.1 core,
                  2026-10-11: before the menu, `lv2_memory: Cannot allocate 0x100000`);
                  recover: gtpilot.py recover

Unattended GT5 runs need a valid hunt grant for BCUS98114 with panic budget left
(`hunt.py check --game BCUS98114 --panic`): a GT5 launch knowingly risks the car's kernel,
so it rides on the operator's signature, never on the Engineer's say-so. Each PANIC is
audited (`hunt.py panic --trial DIR`), which is what spends the budget. All car reads are read-only ssh (cat/ls/tail).

  gt5_trial.py --out DIR [--game BCUS98114] [--secs 1500] [--macro FILE] [--label TEXT] [--host H] [--attach]

--host defaults to env ETK_RIG, else etk.conf's CAR<N>_SSH / RIG_SSH (tools/autopilot/etkconf.py),
else the old `etk-rig` alias (car8). On car12 pass `--host flip2-12g-usb` (USB-net, read-only ssh).

--attach watches a GT5 that is ALREADY running (driven by another Pitlink client that holds
the wheel, e.g. while a menu route is being mapped): no launch, this trial is an observer.

Macro lines (one step each; # comments): `sleep S` · `press BTN[+BTN] [MS]` ·
`hold K=V ... secs=S` (gtdrive's controls, e.g. r2=0.8 lx=-0.3) · `wait_lit [S]` ·
`settle [MAX [QUIET]]` (after a press: on as soon as the screen changed, then held still and lit for QUIET s,
default 1; MAX = the old sleep; with no press before it, only the stillness counts: the end of an intro movie) · `wait_motion [MAX]` (a load ends when the picture moves: the track flyover) ·
`snap NAME` · `drive GTDRIVE-ARGS...` (the last step: hands the wheel to gtdrive.py -- the
trial lets go of the controller seat, gtdrive takes it, the trial rejoins as an observer;
gtdrive's output goes to OUT/drive.log) · `observe N` (the last step: let go of the wheel and
stay off it for N s so another client -- a menu driver, then gtdrive -- can take it; the trial
rejoins as an observer and keeps watching) · `exit [savestate]` (the last step: waits for a
`drive` to finish, takes the wheel back and asks RPCS3 to quit gracefully through Pitlink's EXIT --
the postmortem then writes a CLEAN / SURVIVED:* row instead of the RECOVERY:* a kill would leave;
the trial ends SURVIVED when the game is gone). The macro runs beside the watch; a death mid-macro
is still a PANIC.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pitlink"))
from client import PitlinkClient, PitlinkError, garage  # noqa: E402
import etkconf  # noqa: E402

GAME = "BCUS98114"
GATED = ("BCUS98114",)   # the only title that kills the kernel: grant + panic budget, audited
# car8 lift access (manual §1.1, operator 2026-10-24): the headless test car runs GT5 with no grant.
# Same scope as the guard: one of car8's pinned ssh names AND the car itself says car8.
LIFT_CAR = "car8"
LIFT_HOSTS = {"etk-rig-usb", "etk-rig", "169.254.170.2", "sm8250.local"}
ETK = "/storage/games-internal/roms/etk"
BB = ETK + "/etk_telemetry/blackbox"
LEDGER = ETK + "/etk_telemetry/sessions.tsv"
RPCS3_LOG = "/storage/.cache/rpcs3/RPCS3.log"
TRIPWIRE = "/storage/etk_tripwire.log"
GUEST_OOM = re.compile(r"lv2_memory::on_id_create\(\): Cannot allocate")
GUEST_OOM_MIN = 50          # one failed alloc can be a probe; a loop is hundreds a second
# "Segfault (reading|writing) location": the HOST's own SIGSEGV (car8 2026-10-25, GT6 on 0.10.0: a wild
# write 1 s into the boot, logged ·F, then RPCS3 paused itself at flip 0 -- no guest-side line ever came)
FAULT = re.compile(r"VM: Access violation|SYS: Emulation has been frozen|Unhandled exception|PPU: Trap"
                   r"|Segfault (?:reading|writing) location")
HUNT = os.path.join(HERE, "..", "hunt", "hunt.py")
# the blackbox's own PON parser/decoder (pure functions; importing runs no daemon code)
_bbspec = importlib.util.spec_from_file_location("blackbox_d", os.path.join(HERE, "..", "..", "bin", "blackbox_d.py"))
bb = importlib.util.module_from_spec(_bbspec)
_bbspec.loader.exec_module(bb)
# one seek-read of each PMIC's PON window, 9 bytes per "0800: 01" line (read-only)
PON_READ = ("for d in /sys/kernel/debug/regmap/0-0*; do grep -qx pmic-spmi $d/name 2>/dev/null || continue; "
            "echo \"== ${d##*/}\"; dd if=$d/registers bs=9 skip=2048 count=224 2>/dev/null; done")


# ---- pure helpers (tools/autopilot/test_gt5_trial.py) ---------------------------------

def parse_macro(text):
    """[(verb, args)] from macro text; raises ValueError naming the bad line, so a typo
    fails before the launch, not three menus deep."""
    steps = []
    for n, raw in enumerate(text.splitlines(), 1):
        ln = raw.split("#", 1)[0].strip()
        if not ln:
            continue
        verb, *args = ln.split()
        try:
            if verb == "sleep":
                steps.append(("sleep", float(args[0])))
            elif verb == "press":
                steps.append(("press", (args[0].split("+"), int(args[1]) if len(args) > 1 else 120)))
            elif verb == "hold":
                kv = dict(a.split("=", 1) for a in args)
                secs = float(kv.pop("secs"))
                steps.append(("hold", ({k: float(v) for k, v in kv.items()}, secs)))
            elif verb == "wait_lit":
                steps.append(("wait_lit", float(args[0]) if args else 120.0))
            elif verb == "settle":
                mx = float(args[0]) if args else 15.0
                steps.append(("settle", (mx, float(args[1])) if len(args) > 1 else mx))
            elif verb == "wait_motion":
                steps.append(("wait_motion", float(args[0]) if args else 90.0))
            elif verb == "snap":
                steps.append(("snap", args[0]))
            elif verb == "drive":
                steps.append(("drive", args))
            elif verb == "observe":
                steps.append(("observe", float(args[0])))
            elif verb == "exit":
                if args and args[0] != "savestate":
                    raise ValueError("exit takes nothing or `savestate`")
                steps.append(("exit", bool(args)))
            else:
                raise ValueError(f"unknown verb {verb!r}")
        except (IndexError, KeyError, ValueError) as e:
            raise ValueError(f"macro line {n}: {raw.strip()!r}: {e}") from None
    return steps


def previous_boot_file(ls_lines):
    """From `ls -t` output (newest first) of one per-boot series (flightrec-*.tsv or
    kmsg-*.log), the DEAD boot's file: the newest belongs to the boot that just came up,
    the next one to the boot that died. File names carry a boot-time epoch that an unset
    clock makes meaningless (kmsg-532654479.log), so order is by mtime, never by name."""
    files = [ln.strip() for ln in ls_lines if ln.strip() and not ln.strip().endswith(".old")]
    return files[1] if len(files) > 1 else None


def last_game_sample(tsv_text, game=GAME):
    """The dead boot's final flight-recorder sample for `game` as a dict (header names),
    plus `min_mem_avail_kb` over that game's samples; None when the game never ran."""
    header, last, lo = None, None, None
    for ln in tsv_text.splitlines():
        if ln.startswith("#"):
            header = ln.lstrip("# ").split("\t")
            continue
        cols = ln.split("\t")
        if header and len(cols) == len(header) and cols[1] == game:
            last = dict(zip(header, cols))
            try:
                v = int(cols[2])
                lo = v if lo is None else min(lo, v)
            except ValueError:
                pass
    if last is not None:
        last["min_mem_avail_kb"] = lo
    return last


def decode_pon_read(text):
    """PON_READ output -> (verdict, [decode lines]) via the blackbox's own decoder."""
    lines, sid, buf = [], None, []
    for ln in (text or "").splitlines() + ["== end"]:
        if ln.startswith("== "):
            if sid:
                d = bb.decode_pon(bb.parse_regmap_dump("\n".join(buf)))
                if d:
                    lines.append(f"{sid} {d}")
            sid, buf = ln[3:].strip(), []
        else:
            buf.append(ln)
    return (bb.pon_verdict(lines) if lines else None), lines


def host_usb_gone(after_epoch, journal=None, window=120):
    """Epoch of the first `USB disconnect, device number N` in the host's kernel log after
    `after_epoch` (journalctl -k -o short-unix). The car's gadget stays on the bus until the SoC
    resets (a SysRq panic held it for the whole panic=30), so this marks the reset moment; with
    the dead boot's last flightrec sample it measures how long the car hung first (GT5 2026-10-11:
    6.2 / 6.8 / 6.6 / 7.7 s). None when the log is unreadable or shows no disconnect."""
    if journal is None:
        try:
            journal = subprocess.run(
                ["journalctl", "-k", "-o", "short-unix", "--no-pager",
                 "--since", f"@{int(after_epoch)}", "--until", f"@{int(after_epoch) + window}"],
                capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    for ln in journal.splitlines():
        if re.search(r"USB disconnect, device number [2-9]\d*", ln):
            try:
                t = float(ln.split()[0])
            except (ValueError, IndexError):
                continue
            if t >= after_epoch:
                return t
    return None


def pon_verdict_of(text):
    """`verdict=` from a blackbox pon-*.txt header line, or None."""
    m = re.search(r"verdict=(\w+)", text or "")
    return m.group(1) if m else None


# ---- the car, read-only ---------------------------------------------------------------

def ssh(host, cmd, timeout=15):
    """(rc, stdout) of a READ-ONLY command on the car; rc 255 = unreachable."""
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, cmd],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 255, ""


def gsafe(req, default=None, timeout=30.0):
    """garage() that answers `default` when the USB link is gone instead of raising: the
    garage socket rides the link that a dying car takes down first."""
    try:
        return garage(req, timeout=timeout)
    except (OSError, PitlinkError, ValueError):
        return default


def boot_id(host):
    rc, out = ssh(host, "cat /proc/sys/kernel/random/boot_id")
    return out.strip() if rc == 0 and out.strip() else None


# Frame-driven waits (car8 2026-10-25): a fixed sleep is the slowest case, and a boot that got faster
# (0.10.1) or slower (a cold cache) shifted every press after it by a screen. `settle` presses on as
# soon as the screen has answered: it changed from the frame before the last press, then held still
# and lit; `wait_motion` ends a load the moment the track flyover moves. Their MAX is the old sleep.
SETTLE_CHANGE, SETTLE_CHANGE_WAIT = 4.0, 3.0   # mean |dgray| vs the pre-press frame; give up waiting for it
SETTLE_STILL, SETTLE_QUIET, SETTLE_LIT = 2.5, 1.0, 8.0
MOTION, MOTION_SECS = 6.0, 1.0


def small(rgb):
    """64x36 grayscale (float) of a frame's RGB array: the unit the frame-driven waits compare."""
    h, w = rgb.shape[:2]
    g = rgb[::max(h // 36, 1), ::max(w // 64, 1)].mean(axis=2)
    return g[:36, :64]


def mad(a, b):
    return float(abs(a - b).mean())


def settle_step(frames, pre, max_s, clock, quiet=SETTLE_QUIET):
    """Consume (t, small-frame) pairs until the screen answered and is still: returns
    (reason, secs). `pre` = the frame before the press (None: no change needed)."""
    t0, changed, prev, still = None, pre is None, None, None
    for t, x in frames:
        t0 = t if t0 is None else t0
        if t - t0 >= max_s:
            return "max", t - t0
        if not changed:
            changed = mad(x, pre) > SETTLE_CHANGE or t - t0 >= SETTLE_CHANGE_WAIT
        elif prev is not None and mad(x, prev) < SETTLE_STILL and x.mean() > SETTLE_LIT:
            still = t if still is None else still
            if t - still >= quiet:
                return "settled", t - t0
        else:
            still = None
        prev = x
    return "max", (clock() - t0) if t0 is not None else 0.0


def motion_step(frames, max_s, clock):
    """Consume (t, small-frame) pairs until the picture has moved for MOTION_SECS straight."""
    t0, prev, moving = None, None, None
    for t, x in frames:
        t0 = t if t0 is None else t0
        if t - t0 >= max_s:
            return "max", t - t0
        if prev is not None and mad(x, prev) > MOTION:
            moving = t if moving is None else moving
            if t - moving >= MOTION_SECS:
                return "moving", t - t0
        elif prev is not None:
            moving = None
        prev = x
    return "max", (clock() - t0) if t0 is not None else 0.0


def live_frames(c, stop):
    """(t, small-frame) from the Pitlink tap until stop."""
    while not stop.is_set():
        try:
            f = c.wait_frame(timeout=2.0)
        except PitlinkError:
            continue
        if f is not None:
            yield time.time(), small(f.rgb())


def run_macro(c_holder, steps, out, log, stop):
    for verb, arg in steps:
        if stop.is_set():
            return
        c = c_holder.get("c")
        try:
            if verb == "sleep":
                stop.wait(arg)
            elif verb == "press":
                f = c.latest_frame()
                c_holder["pre"] = small(f.rgb()) if f is not None else None
                c.press(arg[0], ms=arg[1])
            elif verb == "settle":
                mx, quiet = arg if isinstance(arg, tuple) else (arg, SETTLE_QUIET)
                why, secs = settle_step(live_frames(c, stop), c_holder.pop("pre", None), mx, time.time, quiet)
                log(f"macro: settle {why} after {secs:.1f}s")
                continue
            elif verb == "wait_motion":
                why, secs = motion_step(live_frames(c, stop), arg, time.time)
                log(f"macro: wait_motion {why} after {secs:.1f}s")
                continue
            elif verb == "hold":
                c.hold(**arg[0])
                stop.wait(arg[1])
                c.release()
            elif verb == "wait_lit":
                t = time.time()
                while time.time() - t < arg and not stop.is_set():
                    try:
                        f = c.wait_frame(timeout=2.0)
                    except PitlinkError:
                        continue
                    if f is not None and f.rgb().mean() > 3:
                        break
            elif verb == "snap":
                f = c.snap(timeout=5.0)          # native frame: the tap may be idle
                if f is not None:
                    f.png(os.path.join(out, f"{arg}.png"), max_w=640)
            elif verb == "observe":
                c_holder["pause_until"] = time.time() + arg
                c.close()
                c_holder["c"] = None
            elif verb == "drive":
                c_holder["pause_until"] = time.time() + 15   # keep the watch from re-taking the wheel
                c.close()
                c_holder["c"] = None
                d_out = os.path.join(out, "drive")
                c_holder["driver"] = subprocess.Popen(
                    [sys.executable, os.path.join(HERE, "gtdrive.py"), "--out", d_out] + list(arg),
                    stdout=open(os.path.join(out, "drive.log"), "w"), stderr=subprocess.STDOUT)
            elif verb == "exit":
                drv = c_holder.get("driver")
                while drv is not None and drv.poll() is None and not stop.is_set():
                    stop.wait(2)              # gtdrive holds the wheel until its own --secs
                if stop.is_set():
                    return
                # the first HELLO is the controller: close the watch's (observer) link and take the wheel
                c_holder["pause_until"] = time.time() + 30
                if c is not None and not c.closed:
                    c.close()
                c_holder["c"] = None
                x = PitlinkClient("usb", timeout=4)
                x.hello()
                if not x.controller:
                    x.close()
                    log("macro: exit refused -- another client holds the wheel; run pitlink.py exit by hand")
                    return
                x.exit(arg)
                x.close()
                c_holder["exited"] = time.time()
            log(f"macro: {verb} {arg}")
        except (PitlinkError, OSError, AttributeError) as e:
            log(f"macro stopped at {verb} {arg}: {e}")
            return


def harvest(host, out, base_pon, log):
    """After the car came back: wait for the new boot's PON snapshot and PANIC row, then
    copy the dead boot's evidence. Every command is a read."""
    ev = {}
    _, direct = ssh(host, PON_READ)
    ev["pon_direct_verdict"], dl = decode_pon_read(direct)
    ev["pon_direct"] = "\n".join(dl) + "\n"
    t = time.time()
    pon_name = None
    while time.time() - t < 60:
        # by boot_id, never by mtime: pon files inherited from another car's card carry that car's
        # pre-NTP clock (year 2094) and win every `ls -t` (car8 2026-10-26, SSX)
        rc, o = ssh(host, f"grep -l \"boot_id=$(cat /proc/sys/kernel/random/boot_id)\" {BB}/pon-*.txt 2>/dev/null | head -1")
        name = o.strip()
        if rc == 0 and name and name != base_pon:
            pon_name = name
            break
        time.sleep(5)
    if pon_name:
        _, ev["pon"] = ssh(host, f"cat {pon_name}")
    else:
        log("harvest: no new pon-*.txt within 60 s (blackbox without harvest_pon yet); the direct read stands")
    for series, key in (("flightrec-*.tsv", "flightrec"), ("kmsg-*.log", "kmsg")):
        _, o = ssh(host, f"ls -t {BB}/{series} 2>/dev/null")
        f = previous_boot_file(o.splitlines())
        if f:
            ev[key + "_file"] = f
            _, ev[key] = ssh(host, f"cat {f}", timeout=60)
    t = time.time()
    while time.time() - t < 120:   # the Sentry synthesizes the PANIC row early in boot
        _, o = ssh(host, f"tail -n 3 {LEDGER}")
        ev["ledger_tail"] = o
        last = o.strip().splitlines()[-1:] or [""]
        if "\tPANIC\t" in last[0]:
            break
        time.sleep(10)
    # pstore (forensics kernel 0.6.4.2+: ramoops console in reserved RAM). GT5's death is a WARM reset
    # and RAM survives it (2026-10-11 f1: the dead boot's console-ramoops-0 came back); blackbox_d
    # harvests /sys/fs/pstore at boot into blackbox/pstore/<stamp>/. Newest stamp only.
    _, o = ssh(host, f"d=$(ls -td {BB}/pstore/*/ 2>/dev/null | head -1); [ -n \"$d\" ] && "
                     "for f in \"$d\"*; do echo \"=== $f\"; cat \"$f\"; done")
    ev["pstore"] = o
    _, ev["rpcs3_log_tail"] = ssh(host, f"tail -n 4000 {RPCS3_LOG}", timeout=60)
    _, ev["tripwire_tail"] = ssh(host, f"tail -n 60 {TRIPWIRE}")
    names = {"pon": "pon.txt", "pon_direct": "pon_direct.txt", "pstore": "pstore.txt", "flightrec": "flightrec_dead_boot.tsv", "kmsg": "kmsg_dead_boot.log",
             "ledger_tail": "ledger_tail.tsv", "rpcs3_log_tail": "RPCS3_dead_session.log",
             "tripwire_tail": "tripwire_tail.log"}
    for k, v in ev.items():
        if k in names:
            with open(os.path.join(out, names[k]), "w") as fh:
                fh.write(v or "")
    return ev


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", default=GAME, help=f"title serial (default {GAME}; GT5P = NPEA00050 / BCUS98158)")
    ap.add_argument("--secs", type=float, default=1500, help="alive this long = SURVIVED")
    ap.add_argument("--macro", help="menu-path macro file (see the docstring)")
    ap.add_argument("--label", default="")
    ap.add_argument("--host", default=None,
                    help="ssh target for the read-only car probes (default: ETK_RIG, else etk.conf, else etk-rig)")
    ap.add_argument("--attach", action="store_true", help="watch the GT5 already running; do not launch")
    ap.add_argument("--lost-secs", type=float, default=20, help="link silent this long = check the car")
    ap.add_argument("--return-secs", type=float, default=600, help="dark longer than this = UNREACHABLE")
    ap.add_argument("--freeze-secs", type=float, default=90, help="flips stopped this long (after a lit frame) = FREEZE")
    ap.add_argument("--lit-secs", type=float, default=600,
                    help="no lit frame this long after the launch, game still running = FREEZE (never lit); "
                         "cold first-lit on car8: GT5P +266 s, GT6 +406 s")
    a = ap.parse_args()
    a.game = a.game.upper()
    if a.host is None:
        a.host = etkconf.rig_ssh(fallback=etkconf.FALLBACK_SSH)
    steps = parse_macro(open(a.macro).read()) if a.macro else []
    os.makedirs(a.out, exist_ok=True)

    def log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] gt5_trial: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out, "trial.log"), "a") as fh:
            fh.write(line + "\n")

    gated = a.game in GATED
    lift = False
    if gated and a.host.split("@")[-1].lower() in LIFT_HOSTS:
        for _ in range(3):   # one empty read right after a run refused a car8 arm (2026-10-25); still fails closed
            lift = ssh(a.host, "cat /storage/.etk/car")[1].strip() == LIFT_CAR
            if lift:
                break
            time.sleep(2)
    if lift:
        gated = False
        log(f"{a.game}: car8 lift access (manual §1.1) -- no grant, no panic budget; deaths still harvested")
    if gated:
        gate = subprocess.run([sys.executable, HUNT, "check", "--game", a.game, "--panic"],
                              capture_output=True, text=True)
        if gate.returncode != 0:
            sys.exit(f"gt5_trial: no valid hunt grant for {a.game} with panic budget left "
                     f"(hunt.py check --game {a.game} --panic: {(gate.stdout or '').strip()}); "
                     "an unattended GT5 launch rides on the operator's signature")
    verdict = {"tool": "gt5_trial", "label": a.label, "game": a.game, "macro": a.macro, "host": a.host,
               "gated": gated, "lift": lift, "debug_env": garage({"op": "debug_env", "action": "show"})["env"]}
    stop = threading.Event()
    t0 = time.time()

    def finish(v, code, **kw):
        stop.set()
        # t0_epoch/end_epoch: the window rc_report.py joins to the ledger row ([epoch - duration_s, epoch])
        verdict.update(verdict=v, secs=round(time.time() - t0), t0_epoch=round(t0),
                       end_epoch=round(time.time()), **kw)
        with open(os.path.join(a.out, "verdict.json"), "w") as f:
            json.dump(verdict, f, indent=1)
        print(json.dumps(verdict), flush=True)
        sys.exit(code)

    running = garage({"op": "running"})["running"]
    if a.attach:
        # the garage reports name+path only; GT5 proper is recognisable by its title, another serial
        # is taken on trust and named in the verdict (a mapping run's aid, not a gate)
        if not running or (a.game == GAME and "Turismo 5 (" not in (running.get("name") or "") + (running.get("path") or "")):
            finish("NO_GAME", 2, note=f"--attach: {a.game} is not the running game ({running})")
        verdict["attached_to"] = running.get("name")
    elif running:
        finish("NO_GAME", 2, note="a game is already running: recover first (gtpilot.py recover)")
    base_boot = boot_id(a.host)
    if not base_boot:
        sys.exit(f"gt5_trial: {a.host} unreachable over ssh before launch")
    _, o = ssh(a.host, f"ls -t {BB}/pon-*.txt 2>/dev/null | head -1; "
                       "cat /sys/class/power_supply/*battery*/capacity 2>/dev/null | head -1")
    base_pon, battery = (o.splitlines() + ["", ""])[:2]
    verdict.update(boot_id=base_boot, battery_pct_start=battery.strip())
    log0 = garage({"op": "log", "tail": 1})["size"]
    if not a.attach:
        garage({"op": "launch", "game": a.game})
    t0 = time.time()
    log(f"{'attached' if a.attach else 'launched'} ({a.label or 'no label'}), boot {base_boot[:8]}, battery {battery.strip()}%, "
        f"{len(steps)} macro steps")

    holder, macro_t = {"c": None}, None
    rotated, lit, last_ok = log0 == 0 or a.attach, False, time.time()
    last_flip, flip_t, last_log_check = None, time.time(), 0.0
    while True:
        now = time.time()
        if now - t0 > a.secs:
            finish("SURVIVED", 0, last_flip=last_flip)
        c = holder["c"]
        if (c is None or c.closed) and now < holder.get("pause_until", 0):
            c = None
        elif c is None or c.closed:
            try:
                c = PitlinkClient("usb", timeout=4)
                c.hello()
                holder["c"] = c
            except (OSError, PitlinkError):
                holder["c"] = c = None
        if c is not None:
            try:
                flip, _ = c.ping()
                last_ok = now
                if last_flip is None or flip > last_flip:
                    last_flip, flip_t = flip, now
                f = c.latest_frame()
                if f is not None and not lit and f.rgb().mean() > 3:
                    lit = True
                    f.png(os.path.join(a.out, "first_lit.png"))
                    verdict["first_lit_secs"] = round(now - t0)
                    log(f"first lit frame at +{now - t0:.0f}s")
                if steps and macro_t is None and lit:
                    macro_t = threading.Thread(target=run_macro, args=(holder, steps, a.out, log, stop), daemon=True)
                    macro_t.start()
            except (PitlinkError, OSError):
                pass
        if now - last_ok > a.lost_secs:
            bid = boot_id(a.host)
            if bid == base_boot:
                if (gsafe({"op": "running"}) or {"running": True})["running"] is None:
                    if holder.get("exited"):
                        finish("SURVIVED", 0, last_flip=last_flip, first_lit=lit,
                               note=f"exited by the macro at +{holder['exited'] - t0:.0f}s (Pitlink EXIT): "
                                    "the postmortem closes the row CLEAN")
                    finish("EXITED", 6, note="game gone, car up on the same boot")
                last_ok = now          # car up, game up, link flaky: keep watching
            else:
                t_down = now - a.lost_secs
                log(f"car DARK at +{t_down - t0:.0f}s (link silent {a.lost_secs:.0f}s, ssh {'new boot' if bid else 'down'})")
                while bid in (None, base_boot) and time.time() - t_down < a.return_secs:
                    time.sleep(5)
                    bid = boot_id(a.host)
                if bid in (None, base_boot):
                    finish("UNREACHABLE", 4, died_secs=round(t_down - t0),
                           note=f"dark > {a.return_secs:.0f}s: STOP, a human checks the car (no reboots from here)")
                back = round(time.time() - t_down)
                log(f"car BACK on boot {bid[:8]} after {back}s; harvesting")
                ev = harvest(a.host, a.out, base_pon.strip(), log)
                pon = pon_verdict_of(ev.get("pon")) or ev.get("pon_direct_verdict")
                if gated:
                    audit = subprocess.run([sys.executable, HUNT, "panic", "--trial", a.out, "--pon", str(pon)],
                                           capture_output=True, text=True)
                    verdict["audit"] = (audit.stdout or audit.stderr or "").strip()[-300:]
                else:
                    verdict["audit"] = ("unaudited: car8 lift access (no grant, no panic budget)" if lift else
                                        f"unaudited: {a.game} is not a grant-gated title (no panic budget to spend)")
                kit = None
                t_kit = time.time()
                while kit is None and time.time() - t_kit < 180:
                    kit = gsafe({"op": "status"}, timeout=10)
                    if kit is None:
                        time.sleep(10)
                last = last_game_sample(ev.get("flightrec", ""), a.game)
                gone = host_usb_gone(int(last["epoch"])) if last else None
                common = dict(died_secs=round(t_down - t0), back_secs=back, new_boot_id=bid, first_lit=lit,
                              hung_secs=round(gone - int(last["epoch"]), 1) if gone else None,
                              pon=pon, pon_direct=ev.get("pon_direct_verdict"),
                              last_sample=last,
                              ledger_row=(ev.get("ledger_tail", "").strip().splitlines() or [""])[-1][:200])
                if pon in ("BROWNOUT", "OVERTEMP", "PMIC_WD"):
                    finish("POWER_CUT", 7, note="the PMIC cut power: a hardware question, STOP for a human", **common)
                if kit is None:
                    finish("UNREACHABLE", 4, note="car back but no Pitlink garage within 180 s: STOP", **common)
                finish("PANIC", 1, **common)
        if lit and now - flip_t > a.freeze_secs and now - last_ok < a.lost_secs \
                and (gsafe({"op": "running"}) or {}).get("running"):
            dump = (gsafe({"op": "dump_threads", "timeout": 8}, {}, timeout=60) or {}).get("text", "")
            with open(os.path.join(a.out, "dump.txt"), "w") as fh:
                fh.write(dump)
            finish("FREEZE", 5, last_flip=last_flip, stopped_secs=round(now - flip_t))
        # never lit: the freeze clock above needs a lit frame, so a core that stalls before its first
        # frame (car8 2026-10-25: paused at flip 0 after a host segfault) would otherwise sit out --secs
        if not lit and now - t0 > a.lit_secs and now - last_ok < a.lost_secs \
                and (gsafe({"op": "running"}) or {}).get("running"):
            dump = (gsafe({"op": "dump_threads", "timeout": 8}, {}, timeout=60) or {}).get("text", "")
            with open(os.path.join(a.out, "dump.txt"), "w") as fh:
                fh.write(dump)
            finish("FREEZE", 5, last_flip=last_flip, never_lit=True,
                   note=f"no lit frame within --lit-secs {a.lit_secs:.0f}")
        if now - last_log_check > 10 and now - last_ok < a.lost_secs:
            last_log_check = now
            try:
                if not rotated:
                    rotated = garage({"op": "log", "tail": 1})["size"] < log0 or now - t0 > 45
                bad = garage({"op": "log", "from": 0, "grep": FAULT.pattern}) if rotated else {"lines": 0}
                if bad["lines"]:
                    with open(os.path.join(a.out, "log_tail.txt"), "w") as fh:
                        fh.write(garage({"op": "log", "tail": 400}).get("text", ""))
                    finish("CRASH", 3, fault=bad["text"][-1200:])
                oom = garage({"op": "log", "from": 0, "grep": GUEST_OOM.pattern}) if rotated else {"lines": 0}
                if oom["lines"] >= GUEST_OOM_MIN:
                    mem = garage({"op": "log", "from": 0, "grep": r"sys_memory_get_user_memory_size"})
                    with open(os.path.join(a.out, "guest_memory.txt"), "w") as fh:
                        fh.write(mem.get("text", ""))
                    finish("GUEST_OOM", 8, failed_allocs=oom["lines"], first_lit=lit,
                           last_user_memory=(mem.get("text", "").splitlines() or [""])[-1][-120:])
            except (OSError, PitlinkError, KeyError, ValueError):
                pass       # the garage socket rides the same USB link: a death shows up above
        time.sleep(2)


if __name__ == "__main__":
    main()
