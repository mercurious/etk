#!/usr/bin/env python3
"""Host regression suite for tools/card_doctor.py — the pure parts.

The root tiers open a raw device, so they cannot run in CI or unprivileged;
everything that DECIDES (verdict ladder, EIO localization, kernel-log
classification, device pick, external-tool output parsers, percentiles) is a
pure function and is pinned here. A discriminating test must also see the
BROKEN state: every verdict case has a clean twin that must NOT trip.

Run:  python3 tools/test_card_doctor.py
"""
import errno
import importlib.util
import json
import os
import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
