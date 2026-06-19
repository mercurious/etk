# scripts/turnip — Turnip-fork forensics helpers (host-side)

Host-side tooling for the Stage-IV Mesa Turnip fork: decoding the GT5P a6xx GPU-hang
`hangrd` captures (freedreno **redump** `.rd` files) produced by the cockpit spotter.

These run on the **host** (the decode workstation), not the rig — they need only stock
`python3`. They are NOT pushed by `install.sh`: the rig-side spotter
(`.claude/skills/cockpit/scripts/rocknix_spotter_loop.sh`) carries its own self-contained
copy of the repair logic inline, so there is no rig-runtime dependency on these files.

## The capture-completeness problem these solve
The ROCKNIX `hangrd` debugfs node (`cat /sys/kernel/debug/dri/0/hangrd`) sometimes emits an
**incomplete** redump even when the file is size-stable and the reader has closed:
- the trailing `RD_BUFFER_CONTENTS` section is **truncated** (declares more bytes than written), and/or
- the `RD_CMDSTREAM_ADDR` section — the cmdstream-entry pointer cffdump needs, written at the
  redump **tail** — is **absent**.

`cffdump` then decodes **0 draws**: it has the full GPU memory image but no entry point.
Observed live 2026-06-18 (Save hang `235823`) and 2026-06-19 (prefer_gmem `114642` — the latter
truncated *before* any R3, so it is the node's own dump, not an early-R3 race).

## Tools
- **`rd_inspect.py <file.rd>`** — RD section-type histogram + truncation check + whether
  `RD_CMDSTREAM_ADDR` is present. First thing to run on any capture.
- **`rd_repair.py [--check] <file.rd>`** — validate, and (without `--check`) repair **in place**:
  drop the incomplete trailing section, synthesize `RD_CMDSTREAM_ADDR` from the `<file>.faultinfo`
  ib1 gpuaddr, sized to the **full** containing `RD_GPUADDR` buffer (NOT the dmesg `ib1_size`,
  which is the *remaining* count and would truncate the decode). Idempotent; clean files are
  left untouched. Exit: 0 clean/repaired · 2 cannot-repair · 3 needs-repair (`--check`).

## Decode workflow
```sh
rd_inspect.py cap.rd                 # truncated? missing cmdstream-addr?
rd_repair.py  cap.rd                 # heal in place if needed (reads cap.rd.faultinfo)
# then in the turnip-rocknix container (libarchive rpath / libxml2):
cffdump -s --once --no-color --no-pager cap.rd > cap.txt
```
`--once` decodes the cmdstream once instead of per-tile — the difference between a ~2 MB and a
~400 MB decode. Use `-D <n>` for a single draw, `--bindless` / `--dump-shaders` for descriptor
and ir3 contents at the faulting draw.

## Redump format reference
Section = `[u32 type][u32 size][size bytes]`. Enum in `src/freedreno/common/redump.h`
(`RD_GPUADDR=3`, `RD_CMDSTREAM_ADDR=6`, `RD_BUFFER_CONTENTS=12`, `RD_GPU_ID=13`, `RD_CHIP_ID=14`).
The `RD_GPUADDR` / `RD_CMDSTREAM_ADDR` payload is `[gpuaddr_lo, size/sizedwords, gpuaddr_hi]`
(per `decode/rdutil.h` `parse_addr`).
