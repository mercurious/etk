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
- **`rd_stop.py <decode.txt> <file.rd.faultinfo>`** — where the a6xx hang detector stopped the CP.
  The hang line's `ib1`/`ib2` are each IB's **base** plus its **remaining** dwords. This finds IB2's
  size from the `CP_INDIRECT_BUFFER` that calls it, computes the stop address, and prints the last
  three draws before it: VS/FS **disassembly hashes** (comparable across sessions, unlike shader
  GPU addresses), MRT0 format, scissor and depth. The GPU runs behind the CP's prefetch, so the stuck
  draw is one of the last few, not necessarily the last.
- **`rd_tailwrites.py <decode.txt> <file.rd.faultinfo>`** — the packets still ahead of the CP (rest of
  IB2 + rest of IB1), with the memory-writing and sync ones counted: what a hangcheck recovery
  throws away.

## Decode workflow
```sh
rd_inspect.py cap.rd                 # truncated? missing cmdstream-addr?
rd_repair.py  cap.rd                 # heal in place if needed (reads cap.rd.faultinfo)
LD_LIBRARY_PATH=bundle/lib RNN_PATH=bundle/registers bundle/cffdump -s --once --no-color --no-pager cap.rd > cap.txt
rd_stop.py cap.txt cap.rd.faultinfo  # the stop point + the last draws' shaders
```
**Getting `cffdump` (2026-10-24):** it is not in the `turnip-rocknix` container any more (the
10-04 node rebuild lost the old build tree). Build it on etk-cloud in a throwaway
`ubuntu:24.04` container, so the Turnip build container is never touched. Export the source with
`docker exec turnip-rocknix tar -C /work/mesa-<ver> --exclude='./build*' -cf - .`, then
`pip install meson==1.5.2` (Ubuntu's 1.3.2 is too old for Mesa 26.2), and apt
`liblua5.4-dev libarchive-dev libxml2 libzstd-dev zlib1g-dev bison flex python3-mako`.
Configure with `meson setup -Dtools=freedreno -Dgallium-drivers= -Dvulkan-drivers= -Dplatforms= -Dllvm=disabled -Dfreedreno-kmds=msm`
and run `ninja src/freedreno/decode/cffdump src/freedreno/decode/crashdec`. Bundle the two
binaries with their non-libc `ldd` libraries and `src/freedreno/registers`, and the bundle runs
on the host with the env above. A 450 MB hangrd decodes in about 4.5 min to about 220 MB of text.
`--once` decodes the cmdstream once instead of per-tile — the difference between a ~2 MB and a
~400 MB decode. Use `-D <n>` for a single draw, `--bindless` / `--dump-shaders` for descriptor
and ir3 contents at the faulting draw.

## Redump format reference
Section = `[u32 type][u32 size][size bytes]`. Enum in `src/freedreno/common/redump.h`
(`RD_GPUADDR=3`, `RD_CMDSTREAM_ADDR=6`, `RD_BUFFER_CONTENTS=12`, `RD_GPU_ID=13`, `RD_CHIP_ID=14`).
The `RD_GPUADDR` / `RD_CMDSTREAM_ADDR` payload is `[gpuaddr_lo, size/sizedwords, gpuaddr_hi]`
(per `decode/rdutil.h` `parse_addr`).
