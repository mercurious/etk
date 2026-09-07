#!/usr/bin/env python3
"""ETK — host-side regression tests for RADIO's data contracts and packer.

Run from the repo root:   python3 tools/test_radio.py

No rig, no node, no network, no model. Everything below runs against a synthetic
telemetry directory built in a tempdir with the REAL shapes: the ledger's 31-column
header taken from bin/radio_pack.py's own HEADER, a MangoHud CSV with the real
three-line preamble, an RPCS3 log with the lines the redactor exists to remove, a
blackbox kmsg tail, config_changes.tsv, a career file and the repo's own
crash_signatures.json / pitstop_fields.json.

Every check has an exemplar that passes and a counter that fails, because a test
that cannot fail is not a test (the kit's rule, and the exam's):

  [1] the packer builds and the pack validates against its own contract
  [2] redaction: the argv line is gone, host paths are reduced, mounts are masked
  [3] repeated E/F lines collapse WITH their count
  [4] a missing archive is a pack_note, never an abort
  [5] blackbox_tail is a PANIC-only payload
  [6] run_sheet passes through for its own game and is dropped for another's
  [7] changes_since_last_debrief moves its window when a debrief exists
  [8] perfect_windows and the lock window the shares were scored against
  [9] trim_to_cap sheds in order and says what it shed
  [10] retention keeps the newest 50 and touches nothing else
  [11] the packer's schema self-check reports instead of aborting
  [12] the end-to-end write path, and tools/radio.py rendering it
  [13] schemas.py discriminates a good debrief from ten specific bad ones
  [14] the real mirror row 1788491975, when this checkout has the mirror

The counters that would catch a regression, named: [2] passes trivially if the log
carried no argv line, so the fixture has one AND the check asserts the surviving
lines are non-empty; [3] would pass on a packer that never collapsed if the count
were not asserted; [7] plants a NEWER debrief for the OTHER game, which a packer
that ignored game_id would wrongly use as the window; [8] uses one title inside
session_postmortem.sh's per-title table and one outside it, so the table path and
the fps_med fallback are told apart.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
PACKER = os.environ.get("RADIO_PACK_PY",           # test_paddock's override pattern:
                        os.path.join(ROOT, "bin", "radio_pack.py"))   # point it at an
#      older packer (git show HEAD~1:bin/radio_pack.py > /tmp/old.py) to prove these
#      checks discriminate. Against the pre-reconciliation packer, sections [1] [6]
#      [7] [8] [10] and [11] fail; that is the point of them.
RADIO_CLI = os.path.join(HERE, "radio.py")
MIRROR = os.path.join(ROOT, "state", "etk_telemetry", "sessions.tsv")
FAILS = []


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, os.path.join(HERE, "radio"))
import schemas                                                   # noqa: E402
rp = _load("radio_pack", PACKER)


def check(label, got, want):
    ok = got == want
    print("  %s %-58s %s" % ("ok  " if ok else "FAIL", label,
                             "" if ok else "got %r want %r" % (got, want)))
    if not ok:
        FAILS.append(label)


# ------------------------------------------------------------------- the fixture
BASE = 1700000000
G1 = "NPEA00050"          # GT5P EU: inside postmortem's per-title lock table (30 fps)
G2 = "NPUB31245"          # not in the table: the window is keyed on fps_med
TUNE1 = "build=26.2.2_gtk_0.7;stack=rkTEST/k7.2.0#1/r0.9.0.3;tu_debug=zlatez"
TUNE2 = "build=26.2.2_gtk_0.7;stack=rkTEST/k7.2.0#1/r0.9.0.3;tu_debug=sddepth"

E_CLEAN = BASE + 100      # G1 CLEAN, warm, all archives present
E_PANIC = BASE + 200      # G1 PANIC, has a kmsg tail
E_SURV = BASE + 300       # G1 SURVIVED with a GPU fault status
E_BAKE = BASE + 400       # G1 bake run (shd 7423) - dyno must not score it
E_WARM2 = BASE + 500      # G1 CLEAN, second warm run on the other arm
E_G2 = BASE + 600         # G2 CLEAN, 60 fps cadence
E_G2B = BASE + 700        # G2 RECOVERY
E_ABORT = BASE + 800      # G1 ABORTED
E_BARE = BASE + 900       # G1 CLEAN with NO archives at all


def row(epoch, game, status, **kw):
    """One ledger line in the packer's own 31-column HEADER order."""
    d = dict.fromkeys(rp.HEADER, "-")
    d.update({"epoch": str(epoch), "game_id": game, "status": status,
              "duration_s": "600", "build": "FULL", "peak_load": "8.2",
              "peak_ram_mb": "3521", "peak_temp": "73", "avg_temp": "63",
              "crash_sig": "", "fence_at_crash": "0", "shaders_harvested": "2",
              "drain_pct": "-6", "thermal_overrides": "0", "tune_tag": TUNE1,
              "crash_shot": "-", "fps_med": "30.3", "fps_1low": "6",
              "ft_p99_ms": "165.6", "res_scale": "100", "gpu_mhz": "925",
              "pwr": "race", "ft_jitter_ms": "10.2", "gpu_fault_status": "-",
              "gpu_fault_fence_hex": "-", "aud": "up_s=333.7,ur=0,skip=38",
              "snd": "ok", "lock_pct": "1.2", "perfect_pct": "0", "rescues": "0",
              "perf": "-"})
    d.update({k: str(v) for k, v in kw.items()})
    return "\t".join(d[c] for c in rp.HEADER)


MANGO_PREAMBLE = ("os,cpu,gpu,ram,kernel,driver,cpuscheduler\n"
                  ",,Turnip Adreno (TM) 650,7735832,7.2.0,,ondemand\n"
                  "fps,frametime,cpu_load,cpu_power,gpu_load,cpu_temp,gpu_temp,"
                  "gpu_core_clock,gpu_mem_clock,gpu_vram_used,gpu_power,ram_used,"
                  "swap_used,process_rss,cpu_mhz,elapsed\n")


def mango_csv(path, locked_ms, loose_ms, n=100):
    """Alternate a locked frame and a loose one: every bin is exactly 50% locked."""
    out = [MANGO_PREAMBLE]
    for i in range(n):
        ft = locked_ms if i % 2 == 0 else loose_ms
        fps = 1000.0 / ft
        out.append("%.3f,%.3f,70,0,32,61,51,925,0,0,0,5.1,0,0,2841,%d\n"
                   % (fps, ft, i * 10000000))
    with open(path, "w") as fh:
        fh.write("".join(out))


RPCS3_LOG = """\
S 0:00:00 Log: RPCS3 starting
E 0:00:00 argv: /home/someone/rpcs3-etk/AppRun.wrapped --no-gui /home/someone/g.pkg
E 0:00:01 SYS: Loading /home/someone/roms/ps3/dev_hdd0/game/NPEA00050/USRDIR/EBOOT.BIN
E 0:00:02 VFS: mounted /tmp/.mount_rpcs3AbC12/usr/share/rpcs3 read-only
E 0:00:03 RSX: Fence 111 stalled after 40 ms
E 0:00:04 RSX: Fence 222 stalled after 41 ms
E 0:00:05 RSX: Fence 333 stalled after 42 ms
E 0:00:06 RSX: Fence 444 stalled after 43 ms
F 0:00:07 SPU: Verification failed
·E 0:00:08 RSX: Vulkan device lost, glyph severity form
W 0:00:09 Warning that must not be collected
"""


def build_fixture(tmp):
    """A whole telemetry mirror + config dir, in the shapes the packer really reads."""
    telem = os.path.join(tmp, "etk_telemetry")
    cfg = os.path.join(tmp, "config")
    for sub in ("mango_logs", "rpcs3_logs", "blackbox", "career", "radio"):
        os.makedirs(os.path.join(telem, sub))
    os.makedirs(cfg)

    rows = [
        row(E_CLEAN, G1, "CLEAN", lock_pct="88.0", perfect_pct="71.0"),
        row(E_PANIC, G1, "PANIC", duration_s=300, crash_sig="PANIC_REBOOT",
            shaders_harvested=3),
        row(E_SURV, G1, "SURVIVED:Adreno", duration_s=724, rescues=1,
            gpu_fault_status="00E59005", gpu_fault_fence_hex="575bf",
            crash_sig="KEEPALIVE_SURVIVE,GPU_FENCE_TIMEOUT,NOT_A_REAL_SIG"),
        row(E_BAKE, G1, "CLEAN", shaders_harvested=7423, fps_med="30.8",
            perfect_pct="99.0"),
        row(E_WARM2, G1, "CLEAN", tune_tag=TUNE2, duration_s=800,
            perfect_pct="12.0"),
        row(E_G2, G2, "CLEAN", duration_s=400, fps_med="59.4"),
        row(E_G2B, G2, "RECOVERY:Silent", duration_s=120, fps_med="58.1"),
        row(E_ABORT, G1, "ABORTED", duration_s=10),
        row(E_BARE, G1, "CLEAN", duration_s=900),
    ]
    with open(os.path.join(telem, "sessions.tsv"), "w") as fh:
        fh.write("\t".join(rp.HEADER) + "\n" + "\n".join(rows) + "\n")

    # A 30-fps title inside the lock window [31.0, 36.0]; the loose frames are not.
    mango_csv(os.path.join(telem, "mango_logs", "%d.csv" % E_CLEAN), 33.3, 100.0)
    mango_csv(os.path.join(telem, "mango_logs", "%d.csv" % E_SURV), 33.3, 100.0)
    # A 60-fps title: locked at 16.7 ms, which the 30-fps window would score as 0%.
    mango_csv(os.path.join(telem, "mango_logs", "%d.csv" % E_G2), 16.7, 100.0)

    for ep in (E_CLEAN, E_SURV, E_PANIC):
        with open(os.path.join(telem, "rpcs3_logs", "%d.log" % ep), "w") as fh:
            fh.write(RPCS3_LOG)
    with open(os.path.join(telem, "blackbox", "kmsg-%d.log" % E_PANIC), "w") as fh:
        fh.write("".join("[%d] kmsg line %d\n" % (i, i) for i in range(60)))

    with open(os.path.join(telem, "config_changes.tsv"), "w") as fh:
        fh.write("epoch\tgame_id\tfield_label\told_value\tnew_value\n")
        for i, (ep, gid, field, old, new) in enumerate([
                (BASE + 10, G1, "Antialiasing", "Auto", "Disabled"),
                (BASE + 20, G1, "Write Color Buffers", "false", "true"),
                (BASE + 30, G1, "SPU Block Size", "Safe", "Mega"),
                (BASE + 40, G1, "PPU Threads", "2", "3"),
                (BASE + 50, G1, "MSAA", "Disabled", "Auto"),
                (BASE + 60, G1, "Frame limit", "Auto", "Off"),
                (BASE + 150, G1, "Disable FIFO Reordering", "false", "true"),
                (BASE + 250, G1, "Shader Precision", "Low", "High"),
                (BASE + 160, G2, "Resolution Scale", "75", "100")]):
            # a trailing `source` column must not break the positional read (spec 1)
            tail = "\tradio:%d" % ep if i % 2 == 0 else ""
            fh.write("%d\t%s\t%s\t%s\t%s%s\n" % (ep, gid, field, old, new, tail))
        fh.write("garbage line that is not a row\n")

    with open(os.path.join(telem, "career", "%s.txt" % G1), "w") as fh:
        fh.write("total_sessions=732\nclean_rate_pct=56\ncurrent_streak=24\n")

    with open(os.path.join(telem, "radio", "run_sheet.json"), "w") as fh:
        json.dump({"schema": "ETK-RADIO-RUNSHEET v1", "accepted_epoch": E_CLEAN,
                   "game_id": G1, "stack": "S1", "res": 100,
                   "hypothesis": "zlatez vs sddepth at res 100, warm runs only",
                   "arms": [{"label": "A", "tune": "tu_debug=zlatez", "clk": 925,
                             "pwr": "race", "n_target": 3}],
                   "stop_rule": "perfect_pct gap >= 5 pts at N>=3",
                   "next": "one more warm race on A"}, fh)

    for gid in (G1, G2):
        with open(os.path.join(cfg, "config_%s.yml" % gid), "w") as fh:
            fh.write("Video:\n  Resolution Scale: 100\n"
                     "  Disable FIFO Reordering: false\n"
                     "  Write Color Buffers: \"true\"\n")
    for name in ("crash_signatures.json", "pitstop_fields.json"):
        shutil.copy(os.path.join(ROOT, "config", name), os.path.join(cfg, name))
    return telem, cfg


def build_pack(telem, cfg, epoch):
    notes = []
    args = (str(epoch), rp.Path(telem) / "sessions.tsv", rp.Path(cfg),
            rp.Path(ROOT) / "tools" / "etk_dyno.py", notes)
    try:
        pack = rp.build(*args, ROOT)
    except TypeError:                  # an older packer took no repo_root
        pack = rp.build(*args)
    return pack, notes


# ----------------------------------------------------------------------- the runs
tmp = tempfile.mkdtemp(prefix="etk-radio-test-")
try:
    telem, cfg = build_fixture(tmp)

    print("[1] the packer builds, and the pack validates against pack.v1")
    pack, notes = build_pack(telem, cfg, E_SURV)
    check("schema banner", pack["schema"], "ETK-RADIO-PACK v1")
    check("epoch is the join key, as an int", pack["epoch"], E_SURV)
    check("game_id decoded", pack["game_id"], G1)
    check("rig.dial off tune_tag", pack["rig"]["dial"], "zlatez")
    check("rig.kit is the repo APP_VERSION",
          isinstance(pack["rig"].get("kit"), str), True)
    check("rig.os is null off-rig", pack["rig"].get("os", "MISSING"), None)
    check("rig.os null is explained in pack_notes",
          any("rig.os" in n for n in pack["pack_notes"]), True)
    check("pack validates", schemas.validate(pack, schemas.load("pack.v1")), [])
    check("fault classified", pack["crash"]["fault"]["class"], "fence park (#2)")
    check("unknown crash_sig noted, not dropped silently",
          any("NOT_A_REAL_SIG" in n for n in pack["pack_notes"]), True)
    check("the old key name is gone",
          "recent_changes" in pack["history"], False)
    check("a renamed key would fail the contract",
          bool(schemas.validate(dict(pack, recent_changes=[]),
                                schemas.load("pack.v1"))), True)

    print("\n[2] redaction (spec 3.1: no argv line, nothing outside dev_hdd0/game)")
    lines = [e["line"] for e in pack["crash"]["rpcs3_errors"]]
    blob = "\n".join(lines)
    check("some E/F lines survived (the check can fail)", len(lines) >= 3, True)
    check("argv line dropped", "argv" in blob, False)
    check("host path outside dev_hdd0 is gone", "/home/someone" in blob, False)
    check("the game path is kept, rooted at dev_hdd0",
          any(ln.endswith("dev_hdd0/game/NPEA00050/USRDIR/EBOOT.BIN")
              for ln in lines), True)
    check("mount path masked", "<mount>" in blob, True)
    check("W lines are not collected", "Warning that must not" in blob, False)
    check("the raw '.'-glyph severity form is collected too",
          any("Vulkan device lost" in ln for ln in lines), True)
    check("and it reaches the pack as ASCII, glyph stripped",
          [ln for ln in lines if "Vulkan device lost" in ln],
          ["E 0:00:08 RSX: Vulkan device lost, glyph severity form"])
    check("every collected line is ASCII", blob.isascii(), True)

    print("\n[3] repeated E/F lines collapse WITH the count")
    fence = [e for e in pack["crash"]["rpcs3_errors"] if "Fence" in e["line"]]
    check("four fence lines collapse to one entry", len(fence), 1)
    check("and the entry carries n=4", fence[0]["n"] if fence else None, 4)

    print("\n[4] a missing archive is a note, never an abort")
    bare, _ = build_pack(telem, cfg, E_BARE)
    check("no rpcs3 log -> note", any("no rpcs3 log" in n for n in bare["pack_notes"]),
          True)
    check("no mango csv -> note", any("no mango csv" in n for n in bare["pack_notes"]),
          True)
    check("timeline degrades to null", bare["timeline"], None)
    check("rpcs3_errors degrades to []", bare["crash"]["rpcs3_errors"], [])
    check("and it is still a valid pack",
          schemas.validate(bare, schemas.load("pack.v1")), [])

    print("\n[5] blackbox_tail is a PANIC-only payload")
    panic, _ = build_pack(telem, cfg, E_PANIC)
    check("PANIC row carries the kmsg tail",
          len(panic["crash"]["blackbox_tail"]), rp.MAX_BLACKBOX)
    check("capped at MAX_BLACKBOX", len(panic["crash"]["blackbox_tail"]) <= 40, True)
    check("non-PANIC row carries none", pack["crash"]["blackbox_tail"], [])

    print("\n[6] run_sheet passthrough is game-scoped")
    check("this game's sheet passes through",
          (pack.get("run_sheet") or {}).get("accepted_epoch"), E_CLEAN)
    other, onotes = build_pack(telem, cfg, E_G2)
    check("another game's sheet is dropped", other.get("run_sheet", "MISSING"), None)
    check("and the drop is explained",
          any("run_sheet is for" in n for n in other["pack_notes"]), True)

    print("\n[7] changes_since_last_debrief moves with the debrief on file")
    fresh, _ = build_pack(telem, cfg, E_SURV)
    ch = fresh["history"].get("changes_since_last_debrief") or []
    check("no debrief -> the last 5 rows", len(ch), 5)
    check("no debrief -> and it says so",
          any("no prior debrief" in n for n in fresh["pack_notes"]), True)
    check("window ends at the pack epoch",
          max([c["epoch"] for c in ch] or [0]) <= E_SURV, True)
    check("the trailing source column did not shift the fields",
          [c["field"] for c in ch if c["epoch"] == BASE + 150],
          ["Disable FIFO Reordering"])
    rdir = os.path.join(telem, "radio")
    with open(os.path.join(rdir, "%d.debrief.json" % E_CLEAN), "w") as fh:
        json.dump({"schema": "ETK-RADIO-DEBRIEF v1", "epoch": E_CLEAN,
                   "game_id": G1}, fh)
    # A NEWER debrief for the OTHER game: a packer ignoring game_id would use it.
    with open(os.path.join(rdir, "%d.debrief.json" % E_G2B), "w") as fh:
        json.dump({"schema": "ETK-RADIO-DEBRIEF v1", "epoch": E_G2B,
                   "game_id": G2}, fh)
    after, _ = build_pack(telem, cfg, E_SURV)
    ch2 = after["history"].get("changes_since_last_debrief") or []
    check("window opens at this game's newest debrief",
          [c["epoch"] for c in ch2], [BASE + 150, BASE + 250])
    check("the other game's newer debrief was not used",
          all(c["epoch"] > E_CLEAN for c in ch2), True)

    print("\n[8] perfect_windows, and the window they were scored against")
    tl = pack["timeline"] or {}
    check("bins", tl.get("bins"), rp.TIMELINE_BINS)
    check("gpu_temp_c renamed to temp_c", "gpu_temp_c" in tl, False)
    check("temp_c present", len(tl.get("temp_c") or []), rp.TIMELINE_BINS)
    check("title IS in postmortem's table -> the 30 fps window",
          tl.get("lock_window_ms"), [31.0, 36.0])
    check("half the frames are locked in every bin",
          tl.get("perfect_windows"), [50.0] * rp.TIMELINE_BINS)
    check("a 60 fps title outside the table gets the 60 fps window",
          (other["timeline"] or {}).get("lock_window_ms"), [15.5, 18.0])
    check("and its 16.7 ms frames score as locked there",
          (other["timeline"] or {}).get("perfect_windows"),
          [50.0] * rp.TIMELINE_BINS)
    check("the fallback is disclosed in pack_notes",
          any("not in the title table" in n for n in other["pack_notes"]), True)

    print("\n[9] trim_to_cap sheds the heaviest evidence first, and says so")
    def heavy():
        return {"crash": {"rpcs3_errors": [{"n": 1, "line": "x" * 200}] * 15,
                          "dmesg_window": ["d" * 200] * 25,
                          "blackbox_tail": ["b" * 200] * 40},
                "timeline": {"bins": 10, "fps_med": [30.0] * 10},
                "pack_notes": []}
    big = heavy()
    full = len(json.dumps(big).encode())
    one = rp.trim_to_cap(heavy(), full - 100)
    check("blackbox_tail goes first", one["crash"]["blackbox_tail"], [])
    check("and dmesg stays for now", len(one["crash"]["dmesg_window"]), 25)
    check("the shed is recorded",
          any("blackbox_tail" in n for n in one["pack_notes"]), True)
    all_shed = rp.trim_to_cap(heavy(), 200)
    check("then dmesg, then rpcs3", (all_shed["crash"]["dmesg_window"],
                                     all_shed["crash"]["rpcs3_errors"]), ([], []))
    check("the timeline is last to go", all_shed["timeline"].get("trimmed"), True)
    check("four notes, in shed order",
          [n.split()[1] for n in all_shed["pack_notes"]],
          ["crash.blackbox_tail", "crash.dmesg_window", "crash.rpcs3_errors",
           "timeline"])
    small = rp.trim_to_cap(heavy(), 10 ** 6)
    check("a pack under the cap is not touched", small["pack_notes"], [])

    print("\n[10] retention keeps the newest 50 and touches nothing else")
    rdir2 = os.path.join(tmp, "radio_retention")
    os.makedirs(os.path.join(rdir2, "pending"))
    for i in range(60):
        for suffix in (".pack.json", ".debrief.json"):
            open(os.path.join(rdir2, "%d%s" % (BASE + i, suffix)), "w").close()
    for keep in ("run_sheet.json", "asks.log", "%d.feel" % BASE):
        open(os.path.join(rdir2, keep), "w").close()
    dropped = rp.retain(rp.Path(rdir2)) if hasattr(rp, "retain") else []
    left = os.listdir(rdir2)
    check("50 packs kept", len([f for f in left if f.endswith(".pack.json")]), 50)
    check("50 debriefs kept", len([f for f in left if f.endswith(".debrief.json")]), 50)
    check("20 files dropped", len(dropped), 20)
    check("the OLDEST went", "%d.pack.json" % BASE in left, False)
    check("the newest stayed", "%d.pack.json" % (BASE + 59) in left, True)
    check("run_sheet.json untouched", "run_sheet.json" in left, True)
    check("asks.log untouched", "asks.log" in left, True)
    check(".feel untouched", "%d.feel" % BASE in left, True)
    check("pending/ untouched", "pending" in left, True)

    print("\n[11] the packer's own self-check reports a bad pack instead of aborting")
    broken = dict(pack)
    broken["epoch"] = "not-an-int"
    broken["pack_notes"] = []
    (rp.self_check(broken, ROOT) if hasattr(rp, "self_check") else None)
    check("a contract failure lands in pack_notes",
          bool(broken["pack_notes"]) and broken["pack_notes"][0].startswith("schema:"),
          True)
    good = dict(pack)
    good["pack_notes"] = []
    (rp.self_check(good, ROOT) if hasattr(rp, "self_check") else None)
    check("a good pack gets no schema note", good["pack_notes"], [])

    print("\n[12] the end-to-end write path (atomic, retained, self-checked)")
    r = subprocess.run([sys.executable, PACKER, str(E_SURV), "--ledger",
                        os.path.join(telem, "sessions.tsv"), "--config-dir", cfg],
                       capture_output=True, text=True)
    out_path = os.path.join(telem, "radio", "%d.pack.json" % E_SURV)
    check("packer exits 0", r.returncode, 0)
    check("the pack was written", os.path.exists(out_path), True)
    check("no .tmp left behind",
          any(f.endswith(".tmp") for f in os.listdir(os.path.join(telem, "radio"))),
          False)
    written = json.load(open(out_path))
    check("the written pack validates",
          schemas.validate(written, schemas.load("pack.v1")), [])
    check("budget.bytes is the file's real size",
          written["budget"]["bytes"], os.path.getsize(out_path))
    check("under the 64 KB hard cap", written["budget"]["bytes"] <= rp.HARD_CAP, True)
    ins = subprocess.run([sys.executable, RADIO_CLI, "inspect", out_path],
                         capture_output=True, text=True)
    check("radio.py inspect renders it", ins.returncode, 0)
    check("the render is ASCII", ins.stdout.isascii(), True)
    check("the render stays inside 80 columns",
          max(len(l) for l in ins.stdout.splitlines()) <= 80, True)
    for verb in ("debrief", "ask"):
        st = subprocess.run([sys.executable, RADIO_CLI, verb],
                            capture_output=True, text=True)
        check("radio.py %s without the node is a registered stub, exit 2" % verb,
              (st.returncode, "not built yet" in st.stderr), (2, True))
    st = subprocess.run([sys.executable, RADIO_CLI, "eval", "--list"],
                        capture_output=True, text=True)
    check("radio.py eval delegates to tools/radio/eval.py (--list exits 0, names a case)",
          (st.returncode, "sysmem_no_crown" in st.stdout), (0, True))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------- the debrief contract
print("\n[13] schemas.py discriminates a good debrief from bad ones")
EXEMPLAR = {
    "schema": "ETK-RADIO-DEBRIEF v1", "epoch": 1788491975, "game_id": "NPUB31245",
    "model": "etk-radio:9b", "prompt_sha256": "a" * 64, "corpus_commit": "8401cbc",
    "tokens": {"prompt": 3410, "completion": 402}, "latency_s": 271.3,
    "radio": "Fence wedge at six minutes, keepalive caught it, you finished. "
             "Two rescues an hour is the zlatez baseline. Nothing to change yet.",
    "headline": "SURVIVED - 1 rescue absorbed; zlatez@925 N=2 of 3",
    "tags": ["survived", "low_n"],
    "findings": [{"kind": "mechanism",
                  "text": "00E59005 is a fence park (class #2); the keepalive "
                          "absorbed it.",
                  "evidence": [{"source": "ledger", "field": "rescues", "value": 1},
                               {"source": "dmesg",
                                "line": "context_keepalive: surviving hang"}]}],
    "recommendations": [{"kind": "next_run",
                         "text": "One more warm race on this arm, same track.",
                         "n_basis": {"arm": "zlatez@925/race", "n": 2, "n_needed": 3},
                         "config_changes": [], "driver_dial": None,
                         "confidence": "high"}],
    "run_sheet": None, "guards": {"passed": True, "dropped": []},
}
DSCH = schemas.load("debrief.v1")
check("the exemplar debrief passes", schemas.validate(EXEMPLAR, DSCH), [])


def counter(label, mutate, needle):
    bad = json.loads(json.dumps(EXEMPLAR))
    mutate(bad)
    errs = schemas.validate(bad, DSCH)
    ok = bool(errs) and any(needle in e for e in errs)
    print("  %s %-58s %s" % ("ok  " if ok else "FAIL", label,
                             errs[0] if errs else "(accepted a bad debrief)"))
    if not ok:
        FAILS.append(label)


counter("a hallucinated top-level field is caught",
        lambda d: d.update({"crown": "zlatez wins"}), "$.crown")
counter("radio over 280 chars is caught",
        lambda d: d.update({"radio": "x" * 281}), "$.radio")
counter("a non-ASCII headline is caught",
        lambda d: d.update({"headline": "SURVIVED — 1 rescue"}), "$.headline")
counter("a finding kind outside the enum is caught",
        lambda d: d["findings"][0].update({"kind": "crown"}), "$.findings[0].kind")
counter("a recommendation kind outside the enum is caught",
        lambda d: d["recommendations"][0].update({"kind": "auto_apply"}),
        "$.recommendations[0].kind")
counter("missing guards is caught", lambda d: d.pop("guards"), "guards")
counter("a bad confidence is caught",
        lambda d: d["recommendations"][0].update({"confidence": "certain"}),
        "$.recommendations[0].confidence")
counter("a run sheet arm below N=3 is caught",
        lambda d: d.update({"run_sheet": {
            "schema": "ETK-RADIO-RUNSHEET v1", "game_id": "NPUB31245",
            "arms": [{"label": "A", "n_target": 1}]}}),
        "$.run_sheet.arms[0].n_target")
counter("evidence with an invented key is caught",
        lambda d: d["findings"][0]["evidence"][0].update({"vibe": "bad"}),
        "$.findings[0].evidence[0].vibe")
counter("a non-hex prompt_sha256 is caught",
        lambda d: d.update({"prompt_sha256": "not-a-hash"}), "$.prompt_sha256")


# ------------------------------------------------------------------- the real row
print("\n[14] the real mirror row 1788491975")
if os.path.exists(MIRROR):
    r = subprocess.run([sys.executable, PACKER, "1788491975", "--ledger", MIRROR,
                        "--stdout"], capture_output=True, text=True)
    check("packs from the host mirror", r.returncode, 0)
    if r.returncode == 0:
        real = json.loads(r.stdout)
        check("validates against pack.v1",
              schemas.validate(real, schemas.load("pack.v1")), [])
        check("no schema note in the pack",
              [n for n in real["pack_notes"] if n.startswith("schema:")], [])
        check("inside the 64 KB hard cap",
              real["budget"]["bytes"] <= rp.HARD_CAP, True)
        check("carries a dyno arms table", bool((real.get("dyno") or {}).get("arms")),
              True)
else:
    print("  skip  no state/etk_telemetry/sessions.tsv in this checkout")

print()
if FAILS:
    print("FAILED: %d check(s) -> %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL RADIO CHECKS PASSED")
