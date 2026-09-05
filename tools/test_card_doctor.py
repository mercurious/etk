#!/usr/bin/env python3
"""Host regression suite for tools/card_doctor.py — the pure parts.

The root tiers open a raw device, so they cannot run in CI or unprivileged;
everything that DECIDES (verdict ladder, EIO localization, kernel-log
classification, device pick, external-tool output parsers, percentiles) is a
pure function and is pinned here. A discriminating test must also see the
BROKEN state: every verdict case has a clean twin that must NOT trip.

Run:  python3 tools/test_card_doctor.py
"""
import contextlib
import errno
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("card_doctor", HERE / "card_doctor.py")
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def rows_uniform(n=1000, ms=190.0, chunk=16 << 20):
    return [{"idx": i, "off": i * chunk, "n": chunk, "ms": ms, "sha": f"{i:064x}", "err": 0, "bad": [], "loc_status": ""}
            for i in range(n)]


def base_run(tier="scan", rows=None, **over):
    rows = rows or rows_uniform()
    run = {"tier": tier, "partial": False, "kernel_log": {"total": 3, "fatal": []},
           "identity": {"device": "/dev/sdz", "size_bytes": 256e9}}
    if tier == "scan":
        run["scan"] = {"stats": cd.chunk_stats(rows), "error_rows": [r for r in rows if r["err"]],
                       "verify": {"n": 40, "mismatch": [], "err": [], "persistent_slow": 0},
                       "random_read": {"count": 2000, "p50_ms": 1.1, "p95_ms": 2.0, "p99_ms": 4.0, "max_ms": 20.0,
                                       "iops_qd1": 800, "errors": 0},
                       "fsck": []}
    run.update(over)
    return run


class Verdict(unittest.TestCase):
    def test_clean_scan_is_healthy(self):
        v = cd.compute_verdict(base_run())
        self.assertEqual(v["level"], "HEALTHY", v)
        self.assertTrue(any("write-endurance" in n for n in v["notes"]))

    def test_eio_is_fail(self):
        rows = rows_uniform()
        rows[500].update(err=1, sha="", bad=[[500 * (16 << 20) + 8192, 4096]], loc_status="ok")
        v = cd.compute_verdict(base_run(rows=rows))
        self.assertEqual(v["level"], "FAIL")
        self.assertTrue(any("uncorrectable" in r for r in v["reasons"]))

    def test_hash_mismatch_is_fail(self):
        run = base_run()
        run["scan"]["verify"]["mismatch"] = [{"idx": 7, "off": 0, "first": "a", "second": "b"}]
        self.assertEqual(cd.compute_verdict(run)["level"], "FAIL")

    def test_kernel_fault_is_fail(self):
        run = base_run(kernel_log={"total": 5, "fatal": ["sd 0:0:0:0: [sda] tag#0 FAILED Result: ... I/O error"]})
        self.assertEqual(cd.compute_verdict(run)["level"], "FAIL")

    def test_write_protect_is_fail(self):
        run = base_run(tier="survey", survey={"write_protect_on": True})
        run.pop("scan", None)
        self.assertEqual(cd.compute_verdict(run)["level"], "FAIL")

    def test_clean_survey_is_inconclusive_not_healthy(self):
        run = base_run(tier="survey", survey={"write_protect_on": False})
        run.pop("scan", None)
        v = cd.compute_verdict(run)
        self.assertEqual(v["level"], "INCONCLUSIVE")
        self.assertEqual(v["fails"], [])
        self.assertTrue(any("measures nothing" in n for n in v["notes"]))

    def test_slow_tail_is_degraded_but_a_few_slow_chunks_are_not(self):
        rows = rows_uniform()
        for i in range(0, 1000, 100):          # 1.0 % slow — exactly at threshold: not tripped
            rows[i]["ms"] = 190.0 * 4
        v = cd.compute_verdict(base_run(rows=rows))
        self.assertEqual(v["level"], "HEALTHY", v)
        for i in range(5, 1000, 50):           # now 3 % slow
            rows[i]["ms"] = 190.0 * 4
        v = cd.compute_verdict(base_run(rows=rows))
        self.assertEqual(v["level"], "DEGRADED", v)
        self.assertTrue(any("ECC-retry" in r for r in v["reasons"]))

    def test_tail_statistics_need_enough_chunks(self):
        rows = rows_uniform(8)
        rows[3]["ms"] = 190.0 * 5                       # one hiccup in eight = 12.5 % "slow"
        v = cd.compute_verdict(base_run(rows=rows))
        self.assertEqual(v["level"], "HEALTHY", v)      # not a verdict at N=8
        self.assertTrue(any("too few" in n for n in v["notes"]))
        rows = rows_uniform(100)
        for i in (10, 40, 70):                          # 3 % at N=100 is a verdict
            rows[i]["ms"] = 190.0 * 5
        self.assertEqual(cd.compute_verdict(base_run(rows=rows))["level"], "DEGRADED")
        rows = rows_uniform(8)
        rows[3].update(err=1, sha="")                   # an error convicts at any N
        self.assertEqual(cd.compute_verdict(base_run(rows=rows))["level"], "FAIL")
        run = base_run()
        run["scan"]["random_read"].update(count=20, p99_ms=80.0)
        v = cd.compute_verdict(run)
        self.assertEqual(v["level"], "HEALTHY")         # p99 of 20 reads is not a percentile
        self.assertTrue(any("random reads" in n for n in v["notes"]))

    def test_stalls_are_degraded_at_three_not_two(self):
        rows = rows_uniform()
        rows[10]["ms"] = rows[20]["ms"] = 190.0 * 12
        self.assertEqual(cd.compute_verdict(base_run(rows=rows))["level"], "HEALTHY")
        rows[30]["ms"] = 190.0 * 12
        self.assertEqual(cd.compute_verdict(base_run(rows=rows))["level"], "DEGRADED")

    def test_random_read_p99_is_degraded(self):
        run = base_run()
        run["scan"]["random_read"]["p99_ms"] = 45.0
        self.assertEqual(cd.compute_verdict(run)["level"], "DEGRADED")

    def test_partial_clean_is_inconclusive(self):
        run = base_run(partial=True)
        self.assertEqual(cd.compute_verdict(run)["level"], "INCONCLUSIVE")

    def test_fsck_findings_warn_but_never_convict_the_medium(self):
        run = base_run()
        run["scan"]["fsck"] = [{"device": "/dev/sdz2", "fstype": "ext4", "rc": 0, "severity": "operational", "summary": "clean"}]
        v = cd.compute_verdict(run)
        self.assertEqual((v["level"], v["warnings"]), ("HEALTHY", []))
        run["scan"]["fsck"] = [{"device": "/dev/sdz2", "fstype": "ext4", "rc": 4, "severity": "structural", "summary": "orphan inodes"}]
        v = cd.compute_verdict(run)
        self.assertEqual(v["level"], "HEALTHY")          # an unclean rig power-off is not media damage
        self.assertEqual(len(v["warnings"]), 1)
        self.assertIn("unclean shutdown", v["warnings"][0])

    def test_files_cache_ratio_warns(self):
        fl = {"read_errors": [], "md5_checks": [], "big_files": {"n": 5, "mbps_median": 80.0, "mbps_min": 60.0},
              "small_files": {"n": 1}, "device_read_ratio": 0.4}
        run = base_run(tier="files", files=fl)
        run.pop("scan", None)
        v = cd.compute_verdict(run)
        self.assertEqual(v["level"], "HEALTHY")
        self.assertTrue(any("page cache" in w for w in v["warnings"]))
        fl["device_read_ratio"] = 1.0
        self.assertEqual(cd.compute_verdict(run)["warnings"], [])

    def test_files_tier(self):
        fl = {"read_errors": [], "md5_checks": [{"path": "/m/KERNEL", "ref_file": "KERNEL.md5", "expected": "a", "actual": "a", "match": True}],
              "big_files": {"n": 50, "mbps_median": 80.0, "mbps_min": 60.0}, "small_files": {"n": 10}}
        run = base_run(tier="files", files=fl)
        run.pop("scan", None)
        self.assertEqual(cd.compute_verdict(run)["level"], "HEALTHY")
        fl["md5_checks"][0]["match"] = False
        self.assertEqual(cd.compute_verdict(run)["level"], "FAIL")
        fl["md5_checks"][0]["match"] = True
        fl["read_errors"] = [{"path": "/m/x", "errno": errno.EACCES, "error": "denied"}]
        v = cd.compute_verdict(run)
        self.assertEqual(v["level"], "HEALTHY")          # permissions are not media
        fl["read_errors"].append({"path": "/m/y", "errno": errno.EIO, "error": "io"})
        self.assertEqual(cd.compute_verdict(run)["level"], "FAIL")
        fl["read_errors"] = []
        fl["big_files"]["mbps_min"] = 5.0                # a 16x stall region
        self.assertEqual(cd.compute_verdict(run)["level"], "DEGRADED")

    def test_write_tier(self):
        wr = {"sample": {"runner": "f3", "gib": 8, "write_mbps": 28.0, "read_mbps": 85.0, "ok_sectors": 100, "lost_sectors": 0,
                         "corrupted_sectors": 0, "changed_sectors": 0, "overwritten_sectors": 0, "ok": True},
              "random_write": {"runner": "fio", "iops": 420.0, "p99_ms": 12.0, "errors": 0}}
        run = base_run(tier="write", write=wr)
        run.pop("scan", None)
        self.assertEqual(cd.compute_verdict(run)["level"], "HEALTHY")
        wr["sample"]["write_mbps"] = 6.0
        self.assertEqual(cd.compute_verdict(run)["level"], "DEGRADED")
        wr["sample"]["write_mbps"] = 28.0
        wr["random_write"]["iops"] = 30.0
        self.assertEqual(cd.compute_verdict(run)["level"], "DEGRADED")
        wr["random_write"]["iops"] = 420.0
        wr["sample"]["corrupted_sectors"] = 3
        self.assertEqual(cd.compute_verdict(run)["level"], "FAIL")


class Localize(unittest.TestCase):
    def make_reader(self, bad_ranges):
        calls = []

        def read_fn(off, n):
            calls.append((off, n))
            for s, l in bad_ranges:
                if off < s + l and s < off + n:
                    raise OSError(errno.EIO, "Input/output error")
            return n
        return read_fn, calls

    def test_single_bad_page_is_found_exactly(self):
        off, n = 5 * (16 << 20), 16 << 20
        bad = [(off + 3 * (1 << 20) + 7 * 4096, 4096)]
        read_fn, calls = self.make_reader(bad)
        got, st = cd.localize_bad(read_fn, off, n)
        self.assertEqual(got, bad)
        self.assertEqual(st, "ok")
        self.assertLess(len(calls), 16 + 256 + 4)     # 16 MiB probes + one 1 MiB fine pass

    def test_adjacent_pages_merge(self):
        off, n = 0, 16 << 20
        bad = [(4096 * 10, 4096), (4096 * 11, 4096), (4096 * 12, 4096), (1 << 20, 4096)]
        read_fn, _ = self.make_reader(bad)
        got, st = cd.localize_bad(read_fn, off, n)
        self.assertEqual(got, [(4096 * 10, 3 * 4096), (1 << 20, 4096)])

    def test_cap_and_timeout(self):
        off, n = 0, 16 << 20
        read_fn, _ = self.make_reader([(0, 16 << 20)])          # whole chunk dead
        got, st = cd.localize_bad(read_fn, off, n, max_bad=8)
        self.assertEqual(st, "capped")
        self.assertEqual(len(got), 1)                            # merged contiguous
        t = [0.0]

        def clock():
            t[0] += 50.0
            return t[0]
        got, st = cd.localize_bad(read_fn, off, n, budget_s=100.0, clock=clock)
        self.assertEqual(st, "timeout")

    def test_non_eio_propagates(self):
        def read_fn(off, n):
            raise OSError(errno.EBADF, "bad fd")
        with self.assertRaises(OSError):
            cd.localize_bad(read_fn, 0, 4096)


class KernelLog(unittest.TestCase):
    def test_classify(self):
        lines = ["2026-09-04T19:15:00 host kernel: sd 0:0:0:0: [sda] Attached SCSI removable disk",
                 "2026-09-04T19:15:00 host kernel: sd 0:0:0:0: [sda] Write cache: disabled, read cache: enabled",
                 "2026-09-04T19:40:00 host kernel: sd 0:0:0:0: [sda] tag#0 FAILED Result: hostbyte=DID_OK driverbyte=DRIVER_OK",
                 "2026-09-04T19:40:00 host kernel: sd 0:0:0:0: [sda] tag#0 Sense Key : Medium Error [current]",
                 "2026-09-04T19:40:00 host kernel: blk_update_request: critical medium error, dev sda, sector 123456 op 0x0:(READ)",
                 "2026-09-04T19:41:00 host kernel: usb 2-1.1: reset SuperSpeed USB device number 3 using xhci-hcd",
                 "2026-09-04T19:42:00 host kernel: sd 0:0:0:0: [sda] Write Protect is on"]
        k = cd.klog_classify(lines)
        self.assertEqual(k["total"], 7)
        self.assertEqual(len(k["fatal"]), 5)
        self.assertNotIn(lines[0], k["fatal"])
        self.assertNotIn(lines[1], k["fatal"])

    def test_noise_filter(self):
        self.assertTrue(cd.KLOG_NOISE.search("apple-dcp: RTKit: syslog message: IOMFB: clearing M3 reset"))
        self.assertFalse(cd.KLOG_FATAL.search("EXT4-fs (sda2): mounted filesystem with ordered data mode"))


class DevicePick(unittest.TestCase):
    TREE = [
        {"name": "sda", "path": "/dev/sda", "size": 255869321216, "type": "disk", "rm": True, "tran": "usb",
         "children": [{"name": "sda1", "label": "ROCKNIX-GTK"}, {"name": "sda2", "label": "GTKSTOR"}]},
        {"name": "sdb", "path": "/dev/sdb", "size": 0, "type": "disk", "rm": True, "tran": "usb"},
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "size": 500e9, "type": "disk", "rm": False, "tran": "nvme", "children": []},
    ]

    def test_auto_picks_the_kit_card(self):
        self.assertEqual(cd.pick_device(self.TREE)["name"], "sda")

    def test_refuses_system_disk(self):
        with self.assertRaises(SystemExit):
            cd.pick_device(self.TREE, "/dev/nvme0n1")
        self.assertEqual(cd.pick_device(self.TREE, "/dev/nvme0n1", force=True)["name"], "nvme0n1")

    def test_explicit_empty_slot_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            cd.pick_device(self.TREE, "/dev/sdb")            # the reader's empty second LUN
        self.assertIn("no media", str(cm.exception))

    def test_ambiguous_requires_device(self):
        tree = self.TREE + [{"name": "sdc", "path": "/dev/sdc", "size": 64e9, "type": "disk", "rm": True, "tran": "usb",
                             "children": [{"name": "sdc1", "label": "GTKSTOR"}, {"name": "sdc2", "label": "ROCKNIX-GTK"}]}]
        with self.assertRaises(SystemExit):
            cd.pick_device(tree)
        tree[-1]["children"] = []
        self.assertEqual(cd.pick_device(tree)["name"], "sda")     # kit labels win over a blank stick

    def test_lsblk_string_bools_and_sizes(self):
        tree = [{"name": "sdq", "path": "/dev/sdq", "size": "1000", "type": "disk", "rm": "1", "tran": "usb", "children": []}]
        self.assertEqual(cd.pick_device(tree)["name"], "sdq")


class WritePartition(unittest.TestCase):
    KIT = [{"path": "/dev/sda1", "fstype": "vfat", "label": "ROCKNIX-GTK", "size": 2 << 30},
           {"path": "/dev/sda2", "fstype": "ext4", "label": "GTKSTOR", "size": 236 << 30}]
    RETAIL = [{"path": "/dev/sdb1", "fstype": "exfat", "label": None, "size": 59 << 30}]

    def test_kit_card_uses_gtkstor(self):
        self.assertEqual(cd.pick_write_partition(self.KIT)["path"], "/dev/sda2")

    def test_retail_exfat_is_accepted(self):
        self.assertEqual(cd.pick_write_partition(self.RETAIL)["path"], "/dev/sdb1")

    def test_gtkstor_label_beats_size(self):
        parts = [{"path": "/dev/sdc1", "fstype": "exfat", "label": None, "size": 500 << 30},
                 {"path": "/dev/sdc2", "fstype": "ext4", "label": "GTKSTOR", "size": 100 << 30}]
        self.assertEqual(cd.pick_write_partition(parts)["path"], "/dev/sdc2")

    def test_largest_wins_without_kit_label(self):
        parts = [{"path": "/dev/sdc1", "fstype": "vfat", "label": "BOOT", "size": 1 << 30},
                 {"path": "/dev/sdc2", "fstype": "ext4", "label": "data", "size": 30 << 30}]
        self.assertEqual(cd.pick_write_partition(parts)["path"], "/dev/sdc2")

    def test_no_filesystem_is_none(self):
        self.assertIsNone(cd.pick_write_partition([]))
        self.assertIsNone(cd.pick_write_partition([{"path": "/dev/sdd1", "fstype": None, "size": 1 << 30},
                                                   {"path": "/dev/sdd2", "fstype": "swap", "size": 1 << 30}]))


class Parsers(unittest.TestCase):
    def test_f3(self):
        w = "Free space: 188.00 GB\nCreating file 1.h2w ... OK!\nFree space: 180.00 GB\nAverage writing speed: 28.68 MB/s\n"
        self.assertAlmostEqual(cd.parse_f3write(w)["mbps"], 28.68)
        r = ("  Data OK: 8.00 GB (16777216 sectors)\nData LOST: 4.00 KB (8 sectors)\n"
             "\t       Corrupted: 4.00 KB (8 sectors)\n\tSlightly changed: 0.00 Byte (0 sectors)\n"
             "\t     Overwritten: 0.00 Byte (0 sectors)\nAverage reading speed: 88.55 MB/s\n")
        p = cd.parse_f3read(r)
        self.assertEqual((p["ok_sectors"], p["lost_sectors"], p["corrupted_sectors"], p["changed_sectors"], p["overwritten_sectors"]),
                         (16777216, 8, 8, 0, 0))
        self.assertAlmostEqual(p["mbps"], 88.55)

    def test_fio(self):
        j = {"jobs": [{"write": {"iops": 412.5, "bw_bytes": 1689600,
                                 "clat_ns": {"max": 812000000, "mean": 2400000, "percentile": {"50.000000": 1800000, "99.000000": 15000000}}}}]}
        p = cd.parse_fio_json(json.dumps(j), "write")
        self.assertAlmostEqual(p["iops"], 412.5)
        self.assertAlmostEqual(p["p99_ms"], 15.0)
        self.assertAlmostEqual(p["max_ms"], 812.0)

    def test_md5_refs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "KERNEL.md5").write_text("1b899029535581954fb43afca3563270  target/KERNEL\n")
            Path(d, "SYSTEM.md5").write_text("a9df5a5eddb1038ef083c76ab7a1d10f *SYSTEM\n")
            refs = cd.load_md5_refs(d)
            self.assertEqual(refs["KERNEL"], ("1b899029535581954fb43afca3563270", "KERNEL.md5"))
            self.assertEqual(refs["SYSTEM"][0], "a9df5a5eddb1038ef083c76ab7a1d10f")


class Stats(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        v = list(range(1, 101))
        self.assertEqual(cd.pct(v, 50), 50)
        self.assertEqual(cd.pct(v, 99), 99)
        self.assertEqual(cd.pct(v, 100), 100)
        self.assertEqual(cd.pct([7], 99), 7)
        self.assertIsNone(cd.pct([], 50))

    def test_chunk_stats_and_map(self):
        rows = rows_uniform(640)
        rows[100]["ms"] = 190 * 4
        rows[200]["ms"] = 190 * 20
        rows[300].update(err=1, sha="")
        st = cd.chunk_stats(rows)
        self.assertEqual(st["n_ok"], 639)
        self.assertEqual(st["n_err"], 1)
        self.assertEqual(st["slow_count"], 2)
        self.assertEqual(st["stall_count"], 1)
        self.assertAlmostEqual(st["seq_mbps_median"], (16 << 20) / 1e6 / 0.19, places=1)
        m = cd.surface_map(rows)
        self.assertEqual(len(m), 64)
        self.assertEqual(m[10], "+")     # chunk 100 lives in bucket 10
        self.assertEqual(m[20], "#")
        self.assertEqual(m[30], "X")
        self.assertEqual(m[0], ".")

    def test_verify_selection(self):
        rows = rows_uniform(640)
        rows[5]["ms"] = 190 * 5
        idx = cd.pick_verify_indices(rows, "outliers")
        self.assertIn(5, idx)
        self.assertIn(0, idx)
        self.assertIn(32, idx)
        self.assertLess(len(idx), 640 // 32 + 640 // 50 + 4)
        self.assertEqual(cd.pick_verify_indices(rows, "none"), [])
        self.assertEqual(len(cd.pick_verify_indices(rows, "full")), 640)

    def test_merge_ranges(self):
        self.assertEqual(cd.merge_ranges([(8, 4), (0, 4), (4, 4), (20, 4)]), [(0, 12), (20, 4)])


class WriteProbes(unittest.TestCase):
    def test_fio_target_prefers_a_full_sample_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cd.fio_target(d))
            Path(d, "1.cdw").touch()
            os.truncate(Path(d, "1.cdw"), 1 << 30)                       # sparse, but the size check is what we pin
            self.assertEqual(cd.fio_target(d), os.path.join(d, "1.cdw"))
            Path(d, "1.h2w").touch()
            os.truncate(Path(d, "1.h2w"), 1 << 30)
            self.assertEqual(cd.fio_target(d), os.path.join(d, "1.h2w"))  # f3's file wins when both exist
            os.truncate(Path(d, "1.h2w"), 4096)                          # a truncated leftover is not a target
            self.assertEqual(cd.fio_target(d), os.path.join(d, "1.cdw"))

    def test_invalid_random_write_is_set_aside_not_scored(self):
        wr = {"sample": {"runner": "python", "gib": 8, "write_mbps": 14.5, "read_mbps": 49.8, "bad_blocks": [], "ok": True},
              "random_write": {"runner": "fio", "iops": 3.7, "p99_ms": 152.0, "errors": 0,
                               "invalid": "probe v1: fallocate'd scratch file + fsync per write"}}
        run = base_run(tier="write", write=wr)
        run.pop("scan", None)
        v = cd.compute_verdict(run)
        self.assertEqual(v["level"], "HEALTHY")                 # 3.7 IOPS would be DEGRADED if it counted
        self.assertTrue(any("set aside" in n for n in v["notes"]))
        run["verdict"] = v
        self.assertEqual(cd.treadwear_row(run)[10], "")         # and it never enters the trend
        del wr["random_write"]["invalid"]
        self.assertEqual(cd.compute_verdict(run)["level"], "DEGRADED")

    def test_cache_served_random_read_is_flagged_not_scored(self):
        wr = {"sample": {"runner": "python", "gib": 8, "write_mbps": 14.5, "read_mbps": 49.8, "bad_blocks": [], "ok": True},
              "random_write": {"runner": "fio", "iops": 420.0, "p99_ms": 12.0, "errors": 0},
              "random_read_file": {"runner": "fio", "iops": 366591.0, "p99_ms": 0.002, "errors": 0}}
        run = base_run(tier="write", write=wr)
        run.pop("scan", None)
        v = cd.compute_verdict(run)
        self.assertEqual(v["level"], "HEALTHY")
        self.assertTrue(any("not the card" in n for n in v["notes"]))
        wr["random_read_file"]["iops"] = 800.0
        self.assertFalse(any("not the card" in n for n in cd.compute_verdict(run)["notes"]))
        wr["random_write_sync"] = {"runner": "fio", "iops": 7.0, "p50_ms": 130.0, "errors": 1}
        self.assertEqual(cd.compute_verdict(run)["level"], "FAIL")


class Treadwear(unittest.TestCase):
    IDENT = {"size_bytes": 255869321216, "partitions": [{"uuid": "AAAA-1111"}, {"uuid": "90729342-a1db"}]}

    def test_card_id_is_stable_and_order_free(self):
        a = cd.card_id(self.IDENT)
        b = cd.card_id({"size_bytes": 255869321216, "partitions": [{"uuid": "90729342-a1db"}, {"uuid": "AAAA-1111"}]})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 12)
        self.assertNotEqual(a, cd.card_id({"size_bytes": 255869321216, "partitions": [{"uuid": "AAAA-1111"}, {"uuid": "other"}]}))
        self.assertEqual(cd.card_id({"size_bytes": 1, "partitions": []}), "unknown")

    def test_row_from_scan_and_write_runs(self):
        run = base_run()
        run.update(identity=self.IDENT, card_label="rig card", runid="r1", finished="2026-09-04T21:27:19-0400")
        run["verdict"] = cd.compute_verdict(run)
        row = cd.treadwear_row(run)
        self.assertEqual(row[:5], ["2026-09-04T21:27:19-0400", cd.card_id(self.IDENT), "rig card", "scan", "HEALTHY"])
        self.assertEqual(row[6], "0.00")          # slow_pct
        self.assertEqual(row[8], "4.00")          # random read p99
        self.assertEqual((row[9], row[10]), ("", ""))   # a scan does not measure writes
        wr = {"sample": {"runner": "f3", "gib": 1, "write_mbps": 12.68, "ok": True, "lost_sectors": 0, "corrupted_sectors": 0,
                         "changed_sectors": 0, "overwritten_sectors": 0},
              "random_write": {"runner": "fio", "interrupted": True},
              "random_write_sync": {"runner": "fio", "iops": 135.9, "p50_ms": 1.1, "p99_ms": 131.6, "errors": 0},
              "random_read_file": {"runner": "fio", "iops": 945.0, "p99_ms": 2.09, "errors": 0}}
        run2 = base_run(tier="write", write=wr, identity=self.IDENT, partial=True, finished="2026-09-04T22:24:00-0400")
        run2.pop("scan", None)
        run2["verdict"] = cd.compute_verdict(run2)
        self.assertEqual(run2["verdict"]["level"], "INCONCLUSIVE")     # an interrupted probe is not a failed write
        self.assertTrue(any("interrupted" in n for n in run2["verdict"]["notes"]))
        row2 = cd.treadwear_row(run2)
        self.assertEqual((row2[9], row2[10], row2[11]), ("12.7", "", "131.6"))

    def test_table_from_run_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            for name, run in (("20260904-100000-scan-sdz", dict(base_run(), identity=self.IDENT, runid="a", finished="2026-09-04T10:30:00", tier="scan")),
                              ("20260904-090000-survey-sdz", {"tier": "survey", "verdict": {"level": "INCONCLUSIVE"}, "finished": "2026-09-04T09:00:00"})):
                p = Path(d, name)
                p.mkdir()
                if "verdict" not in run:
                    run["verdict"] = cd.compute_verdict(run)
                (p / "run.json").write_text(json.dumps(run))
            Path(d, "20260904-080000-scan-sdz").mkdir()
            Path(d, "20260904-080000-scan-sdz", "chunks.csv").write_text("idx\n")
            rows, skipped = cd.treadwear_table(d)
            self.assertEqual(len(rows), 1)                    # the survey row is skipped
            self.assertEqual(rows[0][3], "scan")
            self.assertEqual(len(skipped), 1)
            self.assertIn("crashed scan", skipped[0])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cd.treadwear_print(rows, skipped, vs="nomatch")
            self.assertIn("TREADWEAR", out.getvalue())
            self.assertIn("no card matches", out.getvalue())


class Quiescence(unittest.TestCase):
    def test_mismatches_under_foreign_writes_are_not_corruption(self):
        run = base_run()
        run["scan"]["verify"]["mismatch"] = [{"idx": i} for i in range(152)]
        run["scan"]["random_read"]["p99_ms"] = 96.6
        self.assertEqual(cd.compute_verdict(run)["level"], "FAIL")          # quiescent: real corruption
        run["scan"]["foreign_write_mib"] = 55 * 1024.0
        v = cd.compute_verdict(run)
        self.assertEqual(v["level"], "HEALTHY", v)                          # under a concurrent fill: void, not evidence
        self.assertTrue(any("WRITTEN" in w for w in v["warnings"]))
        self.assertTrue(any("not corruption" in w for w in v["warnings"]))
        self.assertTrue(any("not scored" in n for n in v["notes"]))
        run["scan"]["foreign_write_mib"] = 0.4                              # metadata noise does not void anything
        self.assertEqual(cd.compute_verdict(run)["level"], "FAIL")

    def test_set_aside_run_leaves_the_table(self):
        run = base_run(invalid="ran concurrently with a write tier")
        v = cd.compute_verdict(run)
        self.assertEqual(v["level"], "SET-ASIDE")
        self.assertEqual(v["reasons"], [])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "20260905-115016-scan-sdz")
            p.mkdir()
            run["verdict"] = v
            run["finished"] = "2026-09-05T12:33:23"
            (p / "run.json").write_text(json.dumps(run))
            rows, skipped = cd.treadwear_table(d)
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("set aside", skipped[0])

    def test_device_lock(self):
        with tempfile.TemporaryDirectory() as d:
            lock = cd.acquire_device_lock(d, "sdz", "scan")
            self.assertTrue(lock.exists())
            self.assertEqual(json.loads(lock.read_text())["pid"], os.getpid())
            cd.acquire_device_lock(d, "sdz", "scan", alive=lambda pid: True)         # our own pid never blocks us
            lock.write_text(json.dumps({"pid": 4242, "tier": "write", "started": "x"}))
            with self.assertRaises(SystemExit) as cm:                         # a live foreign holder refuses the second tier
                cd.acquire_device_lock(d, "sdz", "scan", alive=lambda pid: pid == 4242)
            self.assertIn("concurrent runs contaminate", str(cm.exception))
            lock.write_text(json.dumps({"pid": 999999, "tier": "scan", "started": "x"}))
            lock2 = cd.acquire_device_lock(d, "sdz", "write", alive=lambda pid: False)   # a dead holder is taken over
            self.assertEqual(json.loads(lock2.read_text())["tier"], "write")
            cd.acquire_device_lock(d, "sdy", "scan", alive=lambda pid: True)        # another device is independent


class Robustness(unittest.TestCase):
    def test_run_cmd_timeout_returns_none(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(cd.run_cmd(["sleep", "5"], timeout=0.3))

    def test_run_cmd_missing_binary_returns_none(self):
        self.assertIsNone(cd.run_cmd(["definitely-not-a-binary-xyz-cd"]))

    def test_parse_range(self):
        size, chunk = 256 << 30, 16 << 20
        self.assertEqual(cd.parse_range(None, size, chunk), (0, size))
        s, e = cd.parse_range("175-222", size, chunk)
        self.assertEqual((s, e), (175 << 30, 222 << 30))
        self.assertEqual(cd.parse_range("100-999", size, chunk)[1], size)      # clipped to the device
        self.assertEqual(cd.parse_range("0.5-1", size, chunk)[0] % chunk, 0)   # chunk-aligned
        for bad in ("abc", "300-400", "20-10"):
            with self.assertRaises(SystemExit):
                cd.parse_range(bad, size, chunk)

    def test_chunks_csv_roundtrip_and_zero_chunks(self):
        rows = rows_uniform(8, chunk=1 << 20)
        rows[3]["sha"] = hashlib.sha256(bytes(1 << 20)).hexdigest()
        rows[5].update(err=1, sha="", bad=[(5 << 20, 4096), (5 << 20 + 8192, 4096)], loc_status="ok")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "chunks.csv")
            cd.write_chunks_csv(rows, p)
            back = cd.load_chunks_csv(p)
        self.assertEqual(back, rows)
        st = cd.chunk_stats(back)
        self.assertEqual((st["zero_chunks"], st["n_err"], st["n_ok"]), (1, 1, 7))

    def test_runid_regex_covers_range_and_plain(self):
        self.assertEqual(cd.RUNID_RX.match("20260904-195657-scan-sda").group(3), "sda")
        self.assertEqual(cd.RUNID_RX.match("20260904-221000-scan-r175-222-sda").group(3), "sda")
        self.assertEqual(cd.RUNID_RX.match("20260904-221000-scan-_cd_test.img").group(3), "_cd_test.img")
        self.assertIsNone(cd.RUNID_RX.match("20260904-221000-files-sda"))


class FileModeScan(unittest.TestCase):
    """End to end on a regular file (O_DIRECT works on the repo's filesystem, not on
    tmpfs): the scan path must always leave a run.json behind, crash or not."""

    def setUp(self):
        cd.STOP["flag"] = False
        self.img = HERE / "_cd_test.img"
        self.img.write_bytes(os.urandom(8 << 20))
        self.out = tempfile.mkdtemp()

    def tearDown(self):
        self.img.unlink(missing_ok=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def run_main(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = cd.main(argv)
        runs = sorted(Path(self.out).glob("*/run.json"))
        self.assertEqual(len(runs), 1, runs)
        return rc, json.loads(runs[-1].read_text()), runs[-1].parent

    def base_argv(self, *extra):
        return ["scan", "--allow-file", "--device", str(self.img), "--chunk-mib", "1", "--random-reads", "20", "--out", self.out, *extra]

    def test_scan_writes_run_json(self):
        rc, run, d = self.run_main(self.base_argv())
        self.assertEqual(rc, 0)
        self.assertEqual(run["verdict"]["level"], "HEALTHY")
        self.assertEqual(run["scan"]["stats"]["n_ok"], 8)
        self.assertEqual(run["scan"]["stats"]["zero_chunks"], 0)
        self.assertEqual(run["scan"]["verify"]["mismatch"], [])
        self.assertEqual(run["scan"]["random_read"]["count"], 20)
        self.assertEqual(run["checkpoint_stage"], "random-read")
        for f in ("chunks.csv", "latency.svg", "report.md"):
            self.assertTrue((d / f).exists(), f)

    def test_stage_crash_still_writes_run_json(self):
        orig = cd.random_read_probe

        def boom(*a, **k):
            raise RuntimeError("boom-probe")
        cd.random_read_probe = boom
        try:
            rc, run, d = self.run_main(self.base_argv())
        finally:
            cd.random_read_probe = orig
        self.assertEqual(rc, 0)
        self.assertIn("boom-probe", run["error"])
        self.assertTrue(run["partial"])
        self.assertEqual(run["checkpoint_stage"], "verify")            # the last stage that completed
        self.assertEqual(run["scan"]["stats"]["n_ok"], 8)              # the pass survived the crash
        self.assertEqual(run["verdict"]["level"], "INCONCLUSIVE")
        self.assertTrue(any("internal error" in n for n in run["verdict"]["notes"]))

    def test_range_scan(self):
        rc, run, d = self.run_main(self.base_argv("--range", "0.002-0.005"))   # 2..5 MiB of the 8 MiB image
        self.assertEqual(run["scan"]["stats"]["n_ok"], 4)   # 2 MiB..6 MiB once aligned to 1 MiB chunks
        self.assertIn("-scan-r0.002-0.005-", run["runid"])
        self.assertEqual(run["scan"]["range"]["start"], 2 << 20)

    def test_rebuild_from_crashed_dir(self):
        rc, run, d = self.run_main(self.base_argv())
        (d / "run.json").unlink()
        (d / "report.md").unlink()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = cd.main(["rebuild", str(d), "--note", "verify: 0 mismatch (terminal)", "--card-label", "test card"])
        self.assertEqual(rc, 0)
        run2 = json.loads((d / "run.json").read_text())
        self.assertTrue(run2["rebuilt"])
        self.assertEqual(run2["scan"]["stats"]["n_ok"], 8)
        self.assertEqual(run2["card_label"], "test card")
        self.assertEqual(run2["verdict"]["level"], "INCONCLUSIVE")     # partial by construction
        self.assertTrue(any("verify: 0 mismatch" in n for n in run2["verdict"]["notes"]))
        self.assertIn("Rebuilt from", (d / "report.md").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=1)
