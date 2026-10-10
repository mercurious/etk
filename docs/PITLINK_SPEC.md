# PITLINK — the Engineer's live link into the car

**Spec v0.2 · 2026-10-10 · status: P0 (fork patch + host + Pitstop switch written; not yet minted).**
Decided 2026-10-10: Pitlink rides core **0.10.0** = 0.9.1.2 + Pitlink (numbered to match the
GTK 0.10.0 October release); proven first on GT5P, then used to debug GT6 on the same core;
switched from Pitstop TOOLS. Owner lanes: the fork
(`mercurious/etk-rpcs3-gtk`) and the kit (`tools/pitlink/`). Every binary is built on
**etk-cloud** — an operator-run mint (§1.1); nothing is compiled on the M1.

> **Founding principle (operator, 2026-10-10): we own this chassis top to bottom — never
> fight it, rewrite it.** The agent's eyes and hands go *inside* RPCS3, at the PS3
> boundary (the flip, `cellPad`, guest RAM), not at the OS boundary (compositor, uinput,
> SDL, InputPlumber). Every hour lost on 2026-10-10 was lost in a layer we don't need.

---

## 0. Why — what the 2026-10-10 session proved

The session wired an agent onto car8 (GT5P Spec III, core v0.9.1) using only what the OS
and stock RPCS3 already offer (`tools/autopilot/`: `gtpilot.py` + a streamed rig agent).

| Channel | What worked | What fought us |
|---|---|---|
| **RAM** | `/proc/<pid>/mem` on the always-RW mirror `vm::g_sudo_addr` (logged at boot, `0x400000000` on car8): 971 MB committed in 6 ranges, one `pread` per read, no socket | PINE (`ipc.yml`) **loses its socket file**: `Emulator::Init` runs twice at a `--no-gui` boot, two IPC servers start 18 ms apart, the first one's `Cleanup()` `unlink()`s the path the second just bound. A single-Init boot keeps the socket |
| **Vision** | `grim`: ~150 ms for a full 1080p frame at ES | compositor-level, CPU-contended, ≈3–6 fps ceiling, and **no frame identity** — a grab can't be aligned to a flip, a RAM sample, or an input |
| **Control** | uinput pad bound via launch-time `--input-config`: SDL auto-maps it perfectly (probe through RPCS3's own libSDL3), RPCS3's home-menu overlay obeys every button, GT5P obeys **Start** | GT5P ignores **X, d-pad, stick** from that pad — unresolved after an afternoon (pressure-mode hypothesis unproven). InputPlumber 0.79 `SendEvent` panics in its own runtime (only `SendButtonChord` taps work). A pad attached after `cellPadInit` is ignored. **Restart Game wedges** (operator: never use it) |
| **Link** | — | WiFi RTT 3–98 ms (avg 46): no closed loop across it |

Conclusion: RAM was the only channel that worked first time, because it was the only one
that went straight to the guest. Pitlink takes vision and control to the same depth.

---

## 1. What it is

```
 ┌────────────────────── car (RPCS3 GTK Edition, rig or M1 bench) ───────────────────────┐
 │  RSX flip ──► EYES   async downscale+readback ring (no hard sync) ─┐                   │
 │  pad_thread ◄─ HANDS apply_pitlink() → m_pads[port] (bits+pressure)│                   │
 │  guest RAM ◄─► MEMORY batch r/w · per-flip WATCH · bulk snapshot   ├─► Pitlink server ─┼──► PLNK v1
 │  Emulator  ◄── CLOCK  pause · step N flips · savestate · exit      │   (1 thread,      │   USB-net TCP
 │  GTK nets ──► EVENTS  boot · title · rescues · fatal · cellPadInit ┘    silver core)   │   / abstract unix
 └────────────────────────────────────────────────────────────────────────────────────────┘
                                                │
 ┌────────────────────────── M1 (host) ─────────▼─────────────────────────────────────────┐
 │ pitlinkd: link · latest frame · 60 s ring · session recorder (joins the ledger row)     │
 │ perception: screen classifier · HUD reader · liveness (wedge vs guest-stall) · RAM maps │
 │ driver: menu navigator · racing controller (pure pursuit + PID, inputs stamped at_flip) │
 │ runner: scenarios × core × tune matrix → etk_dyno + radio guards → verdicts            │
 │ MCP server "pitlink": Claude's eyes and hands (frame, state, wait_for, press, step…)   │
 └─────────────────────────────────────────────────────────────────────────────────────────┘
```

Two halves, one protocol:
- **GTK Pitlink** — a fork feature (runtime flag `GTK_PITLINK`, **default off**, like every
  GTK_* net). A server thread inside RPCS3.
- **pitlink-host** — Python on the M1 (`tools/pitlink/`): daemon, perception, driver,
  runner, MCP server. numpy + Pillow; no build step.
- **PLNK v1** — a small binary protocol (§3). No JSON on the C++ side, no new fork deps.

---

## 2. Fork side — GTK Pitlink

Base: the cumulative GTK patch on ARMSX3 `8290349e5` (core 0.9.1.x). Line refs are base
(unpatched) ARMSX3 lines; re-derive against the patch at implementation time.

### 2.1 Lifecycle — immune to the double Init
- **Process singleton**, started under `std::call_once` from `Emulator::Init`
  (`Emu/System.cpp:578`) — the second Init that orphans PINE's socket is a no-op here.
- **Abstract Unix socket** `@etk-pitlink` (Linux abstract namespace: no filesystem path,
  so nothing can `unlink` it) for same-host clients, plus optional TCP
  `GTK_PITLINK_TCP=<addr>:<port>`. Default bind: the USB-net gadget address only
  (`169.254.170.x`), **never `0.0.0.0`**. Optional shared token (`GTK_PITLINK_TOKEN`).
- One controlling client; further clients are read-only observers (a second pair of eyes
  never steals the wheel).
- Server thread pinned to a silver core (0–3) — encoding and socket I/O never ride a gold.

### 2.2 EYES — the frame tap (at the flip, not the compositor)
- **Hook:** `VKGSRender::flip` (`Emu/RSX/VK/VKPresent.cpp:1055`), at the point the stock
  screenshot path picks `image_to_flip` (`:1514`). Record, *into the same command buffer as
  the present*: `vkCmdBlitImage(image_to_flip → tap image W×H, linear)` then
  `vkCmdCopyImageToBuffer(→ ring slot k)`. Ring = 3 host-visible buffers, each with the
  frame's fence.
- **No hard sync.** The stock screenshot/recording path calls `flush_command_queue(true)`
  (`:1569`) on every captured frame — fine for F12, fatal for a kit that measures
  frametime. Pitlink harvests slot k on a *later* flip, only when its fence has already
  signalled (non-blocking check); if it hasn't, skip. Latency 1–2 flips.
- **Latest-wins hand-off** (lock-free SPSC → server thread). Vision wants the newest frame,
  never a backlog — the autogamer lesson (RetroArch queued one message per frame and fell
  minutes behind).
- **Pre-overlay by default** (game pixels only; perf overlay, rescue notices, home menu
  excluded); `overlays=1` includes them.
- **Rate/size:** `GTK_PITLINK_VIDEO=640x360@30` (default); `SNAP full` = one native-res frame
  on the same async path, on demand.
- **Encode on the server thread:** `raw` (RGB888) · `zstd` (bundled `3rdparty/zstd`) ·
  `jpeg` (bundled `3rdparty/stblib` stb_image_write). Host negotiates per link: raw/zstd on
  USB-net or local, jpeg on WiFi.
- **Every frame carries its identity:** flip #, `vblank_count` (`RSXThread.cpp:1083`), guest
  time, host `CLOCK_MONOTONIC` at flip, fps/frametime (the `rpcs3_perf_stat` values),
  anti-lock rescue flags, and the RAM WATCH block (§2.4). A frame is never "a picture from
  around then" again.

### 2.3 HANDS — the pad, written at the cellPad boundary
- **Hook:** new `apply_pitlink()` in the `pad_thread` loop, immediately after
  `apply_copilots()` (`Input/pad_thread.cpp:613`) and modelled on it. It writes
  `Button::m_pressed` + `m_value` (**pressure 0–255**) and `AnalogStick::m_value` (0–255)
  into `m_pads[port]` *before* the `m_buttons_external` copy (`:317`), so `cellPadGetData`
  (`Emu/Cell/Modules/cellPad.cpp:356`) sees exactly what a DualShock 3 reports — digital
  bits **and** pressure. That is the layer the 2026-10-10 X failure lived under; Pitlink
  writes it directly instead of hoping three layers translate.
- **Authority per port:** `OFF` · `MERGE` (default: buttons OR, pressure max; an agent
  axis overrides only while it is set away from neutral — the human can always take the
  wheel back) · `EXCLUSIVE` (agent owns the port).
- **Present from boot:** if nothing is bound to the port, Pitlink presents a connected
  DS3-class pad before `cellPadInit` (port the existing `virtual_pad_handler`, today
  `__ANDROID__`-only — `Emu/Io/pad_config_types.h:24`). No hot-plug edge for the game to
  ignore.
- **Timing in-process:** commands apply now or `at_flip=N`; taps/holds are counted in flips
  on the emulator side — deterministic, immune to link jitter. A sequence
  (`[[flip_offset, state], …]`) runs entirely inside RPCS3.
- **Dead-man:** no PAD/heartbeat for `GTK_PITLINK_TTL_MS` (500) → agent state neutral. The
  kit's L1+R3 panic is untouched (input_d reads evdev; Pitlink never grabs anything).

### 2.4 MEMORY — RAM that lines up with the picture
- **READ/WRITE batches** through `vm::try_access` (range-locked, so an unmap cannot race the
  copy; the sudo mirror, so no page-protection faults). Writes therefore do **not** invalidate
  RSX texture-cache copies — fine for game state, the only thing Pitlink is for; never poke
  texture memory through it.
- **WATCH list** (≤ 256 `{addr, type}`): sampled *at the flip* on the RSX thread (256 loads
  — noise, through the sudo mirror) and attached to that flip's frame metadata. Every frame carries the car state
  that produced it: speed, position, lap, menu state.
- **BULK snapshot** of committed ranges, zstd-streamed, for RAM search (replaces today's
  `/proc/<pid>/mem` snap; same host-side `ramscan.py`).

### 2.5 CLOCK — the emulator as an instrument
- `PAUSE` / `RESUME` → `Emulator::Pause` (`System.cpp:3525`) / `Resume` (`:3659`), via
  `Emu.CallFromMainThread`.
- `STEP n` → resume, count n flips at the flip hook, pause. Lockstep for deterministic
  experiments and for thinking time (replaces RetroArch `SLOWMOTION`/`FRAMEADVANCE`).
- `SAVESTATE` → `GracefulShutdown(…, savestate=true)` then boot `--savestate`; `LOAD` = boot
  `--savestate <path>`. **No in-place restart** (it wedges; operator 2026-10-10). Savestates
  are build-locked: per-core start states are minted by the runner itself (§4.5).
- `EXIT` → `GracefulShutdown`: a planned exit lands a clean ledger row, never a RECOVERY.

### 2.6 EVENTS
Boot, title ID, emulator state, `cellPadInit` (with port status), every GTK anti-lock
action (fence force-signal, FIFO resync, RSX watchdog, flip retire), fatal, shader-compile
bursts. Pushed, not polled.

### 2.7 Riding along: two upstreamable fixes
- **PINE unlink race:** `pine_server::Cleanup` unlinks only if the path's inode is still its
  own socket (`fstat` vs `stat`).
- **Double `Emulator::Init` at `--no-gui` boot:** root-cause and fix, or at least guard every
  Init-time singleton (IPC, pad thread init twice: `SDL device 0 connected` at +2.56 and
  +5.20 s on car8).

### 2.8 Flags (all runtime, `RPCS3_ENV_FLAGS`; no rebuild to A/B)

| Flag | Default | Meaning |
|---|---|---|
| `GTK_PITLINK` | 0 | master switch |
| `GTK_PITLINK_TCP` | unset | `addr:port`, or `iface:port` resolved at bind (`gadget:47500` = the car's USB-net address, which differs per car; retried every 5 s until the gadget is up) |
| `GTK_PITLINK_TOKEN` | unset | shared secret for TCP clients |
| `GTK_PITLINK_VIDEO` | `640x360@30` | tap size/rate; `0` = no frames |
| `GTK_PITLINK_PAD` | `merge` | `off` · `merge` · `exclusive` |
| `GTK_PITLINK_PORT` | 0 | which player port HANDS drives |
| `GTK_PITLINK_TTL_MS` | 500 | dead-man timeout |

---

## 3. PLNK v1 — the wire (normative; C++ server and Python host are both written to this)

All integers little-endian. Python `struct` formats given with `<` (no padding).
A **string** is `u16 len` + `len` UTF-8 bytes (no NUL).

**Header** (16 B, `<IHHII`): `magic` = `0x4B4E4C50` (bytes `P L N K`) · `type` · `flags` (0) ·
`seq` · `len` (payload bytes). Replies echo the request's `seq`; server pushes use `seq` = 0.

**Client → server**

| type | name | payload | reply |
|---|---|---|---|
| `0x0001` | HELLO | `<HH` proto (=1), reserved · string token | `0x8001` HELLO_OK |
| `0x0002` | PING | — | `0x8002` PONG `<QQ` flip, host_ns |
| `0x0010` | PAD | `<BBH12sBBBBHHQ` (32 B) port · mode · buttons · pressure[12] · lx · ly · rx · ry · ttl_ms · reserved · at_flip | none (fire-and-forget) |
| `0x0011` | PAD_RELEASE | `<B` port | none |
| `0x0020` | VIDEO | `<HHHBBB3x` w · h · hz (0 = off) · codec · zstd_level · overlays | ACK |
| `0x0021` | SNAP | `<B` codec — next frame at native size, FRAME flags bit0 | ACK |
| `0x0030` | RAM_READ | `<I` n, then n × `<II` addr · size (≤ 65536; n ≤ 4096) | `0x8030` RAM_DATA |
| `0x0031` | RAM_WRITE | `<II` addr · size, then size bytes | ACK |
| `0x0032` | WATCH | `<I` n (≤ 256), then n × `<IB3x` addr · size (1/2/4/8) | ACK |
| `0x0040` | PAUSE | — | ACK |
| `0x0041` | RESUME | — | ACK |
| `0x0042` | STEP | `<I` flips — resume, pause again after `flips` flips (EVENT 7) | ACK |
| `0x0043` | EXIT | `<B` savestate (0/1) — graceful shutdown | ACK |
| `0x0050` | STATUS | — | `0x8050` STATUS_REPLY |

**Server → client**

| type | name | payload |
|---|---|---|
| `0x8001` | HELLO_OK | `<HHI` proto · caps (bit0 video, bit1 pad, bit2 ram, bit3 clock) · role (0 controller, 1 observer) · string version · string title_id · string title |
| `0x800F` | ACK | `<Hh` for_type · status (0 ok, < 0 error) · string message |
| `0x8030` | RAM_DATA | `<I` n, then per item `<BI` ok · size, then size bytes (size 0 when !ok) |
| `0x8050` | STATUS_REPLY | `<IQQ` state (0 running, 1 paused, 2 other) · flip · vblank · string title_id · string title |
| `0x8090` | FRAME | `<QQQHHBBHf` (36 B) flip · vblank · host_ns · w · h · codec · flags · watch_len · fps, then `watch_len` WATCH bytes, then the image |
| `0x80A0` | EVENT | `<H` kind · string text |

**PAD semantics.** `mode`: 0 off · 1 merge · 2 exclusive · 255 = the server default
(`GTK_PITLINK_PAD`). `buttons` bit → DS3 control: 0 SELECT · 1 L3 · 2 R3 · 3 START · 4 UP ·
5 RIGHT · 6 DOWN · 7 LEFT (= `CELL_PAD_CTRL_*` digital-1 flags) · 8 L2 · 9 R2 · 10 L1 · 11 R1 ·
12 TRIANGLE · 13 CIRCLE · 14 CROSS · 15 SQUARE (= digital-2 flags `<< 8`). `pressure[i]` is the
cellPad press offset `8 + i`: RIGHT, LEFT, UP, DOWN, TRIANGLE, CIRCLE, CROSS, SQUARE, L1, R1,
L2, R2; **0 with the button's bit set means 255**. Sticks 0–255, 128 = centre, Y 0 = up.
`ttl_ms` 0 = server default (`GTK_PITLINK_TTL_MS`). **Dead-man:** every PAD received (now or
scheduled) re-arms its port's deadline from receipt; when it lapses the port goes neutral, its
schedule is dropped, and EVENT 4 is pushed only if that released something (a non-neutral live
state or a non-empty schedule). A controller disconnecting releases nothing by itself — the TTL
does, the same way it covers a link that dies without a FIN. `at_flip` 0 = now (replaces the
live state, never cancels scheduled ones); otherwise the PAD joins the port's schedule (≤ 64,
ordered by `at_flip`, ties in arrival order) and is live from flip `at_flip` until the next due
entry — a press is two PADs, down at F and up at F+n. PAUSE/RESUME/STEP do not touch pads.

**Flip numbering.** One counter for FRAME.flip, PONG.flip, STATUS.flip and `at_flip`: it ticks
once per **game** flip (`emu_flip`; native-UI flips — overlays, pause screens, the PPU-compile
screen — never tick it), and a frame carries the value the counter has once its own flip
completes.

**FRAME.** codec 0 = raw BGRA8 (w·h·4 bytes) · 1 = zstd of that; the server streams zstd until
the first VIDEO says otherwise. VIDEO w or h 0 = native size; hz 0 = no stream (SNAP still
works); codec > 1 is refused. The image is the **displayed region** (not the whole surface),
pre-overlay. flags bit0 = native-size SNAP, bit1 = overlays included (never set in v1). A SNAP
and the frame a STEP stops on are always delivered (never skipped by the per-client
latest-wins gate) and the stepped FRAME precedes its EVENT 7; a SNAP while paused is served on
a native-UI flip the server requests. A client gets the most recent frame right after HELLO. WATCH bytes are raw guest memory (big-endian, as the PS3
stores it) in WATCH-list order; an unreadable entry is zeros. `host_ns` is the car's
`CLOCK_MONOTONIC` at the flip. `fps` is the server's flip-rate EMA.

**EVENT kinds.** 1 boot · 2 emulator state · 3 title · 4 pad dead-man release · 5 anti-lock
rescue · 6 error text · 7 step complete (now paused).

**Roles.** The role is decided at every HELLO: the wheel goes to whoever asks while no other
HELLO'd client holds it (no silent promotion; an observer re-HELLOs to take a freed wheel).
Observers get frames, events, RAM_READ, STATUS, PING; control types ACK `-1`. Before HELLO
(no token set) a client may PING, STATUS and RAM_READ but receives no frames or events. With
`GTK_PITLINK_TOKEN` set, a client whose first message is not a HELLO with that token is closed
without a reply. HELLO with proto ≠ 1 ACKs `-1` and stays open.

- Transport: TCP over USB-net (primary; ~1 ms RTT) · abstract Unix `@etk-pitlink` (same
  host: the M1 bench, or an on-car client) · WiFi (viewer fallback — never a closed loop).
- Budget on USB-net: 640×360 BGRA zstd-1 ≈ 6–10 MB/s at 30 Hz.

---

## 4. Host side — pitlink-host on the M1 (`tools/pitlink/`)

### 4.1 `pitlinkd` — link, memory of the last minute, recorder
Holds the latest frame + metadata, a 60 s ring (for "what just happened"), and records a
session to `state/pitlink/<epoch>/` (frames, frame-aligned WATCH values, every input sent,
events) — joined to the kit ledger row by the `[epoch − duration_s, epoch]` interval
(§2.3 manual rule). Exposes a local JSON-RPC socket for every other piece.

### 4.2 Perception — cheap, every frame, no model in the loop
- **Screen classifier:** perceptual-hash nearest-neighbour against a per-title exemplar bank
  (attract, loader, My Page, Arcade, track/car select, grid, racing, pause, results, replay,
  black, home menu). **Claude labels each screen once; numpy recognises it forever after** —
  the autogamer "RAM drives, the model comments" split, applied to pixels.
- **HUD reader:** digit templates for speed / gear / lap / lap time / position, cross-checked
  against WATCH values once the RAM map exists.
- **Liveness:** flips stop → RSX wedge (the kit's anti-lock class); flips continue but the
  image is static → **guest stall** — precisely the GT6-on-0.9.1 signature (loader thread
  parked in `_sys_lwmutex_lock`, RSX healthy). Detected unattended, classified, logged.
- **RAM labelling:** vision reads the HUD speed while WATCH/snapshots search for it —
  vision labels RAM; then RAM drives (`ramscan.py` from 2026-10-10 is the search engine).

### 4.3 Driver
- **Menu navigator:** per-title state machine keyed on the classifier; Claude is the
  fallback when confidence is low, and its decisions become new exemplars.
- **Racing controller:** pure pursuit on a recorded reference line (RAM position) + PID on
  speed; runs on the M1 at frame rate, inputs stamped `at_flip`. RAM drives; the model
  never steers in the loop.

### 4.4 Claude's interface — the `pitlink` MCP server
"Live real-time vision" for Claude means three things:
1. **Now:** the latest frame (small or full-res) on demand in tens of ms, annotated with
   flip #, perception and WATCH values.
2. **Continuously, without image tokens:** perception as text on every frame —
   `state()` says *what* is on screen and what the car is doing.
3. **Event-driven:** `wait_for(condition, timeout)` blocks until a perceived condition holds
   (`screen == my_page`, `speed > 100`, `liveness != ok`) — no polling, no `sleep`.

Tools: `frame` · `contact_sheet(seconds, fps)` · `state` · `wait_for` · `press` · `hold` ·
`release` · `sequence` · `ram_read` · `ram_watch` · `ram_snapshot` · `pause` · `step` ·
`resume` · `savestate` · `exit` · `run_scenario`. **One permission rule scopes the whole
surface** (the 2026-10-10 `gtpilot.py` rule is the precedent).

### 4.5 Scenario runner — unattended testing
- Scenario YAML: title, core, tune, menu route, track, car, laps, timeouts.
- First run on a core mints that core's **start savestate** at the grid (savestates are
  build-locked); later runs boot straight to the race path — the live-race render path
  that attract mode never exercises (`EtkDynoDossier` §7a: attract survived 1200 s+ where
  racing crashed at ~2 min).
- Matrix: core × title × tune (core swap is launch-cadence via `core_map.tsv`; Turnip A/B
  per process via `VK_ICD_FILENAMES` is a candidate — to validate against the kit's
  boot-stamped `loaded` truth before any verdict relies on it).
- Verdicts via `etk_dyno.py` + the radio guards: N≥3, medians, no attract crowns, no
  bake-run fps.

---

## 5. Two targets, one protocol

| Target | Role | Never used for |
|---|---|---|
| **Rig** (car8 / car12, USB-net) | the product path: tests, verdicts, unattended matrix | — |
| **M1 bench** (`rpcs3-asahi-M1`, Honeykrisp, local socket) | same guest, same RAM layout → develop RAM maps, exemplars, routes and the controller at the desk | performance verdicts (not the target GPU; 8 GB unified ceiling) |

Both binaries are minted on etk-cloud (the M1 build in a Fedora-aarch64 lane next to the
ROCKNIX lane). **Nothing compiles on the M1.**

---

## 6. Kit laws this must keep

- **§1.1 bytes-to-atoms:** the fork build is a **mint** (operator, etk-cloud), install is a
  **deploy** (operator, `install.sh`), nothing ships in a release until P3 passes.
- **The switch (operator decision 2026-10-10):** Pitstop **TOOLS → Pitlink (Engineer link)**.
  On writes `/storage/.config/profile.d/095-etk-pitlink` (`GTK_PITLINK=1`,
  `GTK_PITLINK_TCP=gadget:47500`, `GTK_PITLINK_PAD=merge`); off removes it. Applies at the next
  game launch, survives a cold boot, removed by `uninstall.sh`. A core without the feature
  ignores it. An install-time `RPCS3_ENV_FLAGS` (`096`, sourced later) still overrides it.
- **§0 surface:** this loop ends at (a) the pitlink viewer on the M1 (live frame +
  perception + WATCH) and (b) the ledger — append col 32 `drv` (`human`|`pitlink`) **at the
  end of the row, never mid-row**.
- **Measurement integrity:** Pitlink on must not move what the kit measures. Gate: dyno A/B
  (tap on vs off), N≥3 per arm, `perfect_pct` delta inside the noise floor, and
  `rsx::prof` flush-site counters unchanged.
- **The R3 panic path is sacred:** nothing in Pitlink touches input_d or evdev.
- **Identity:** mercurious only; docs stay development-focused.

---

## 7. Phases and gates

| Phase | Where | Gate (pass = all) | Falsifier |
|---|---|---|---|
| **P0 bytes** | repo only | fork patch written + reviewed; host side runs against a fake Pitlink server (the `fake_ra.py` pattern) in host tests | — |
| **P1 mint** | etk-cloud (operator) | rig core + M1 bench build both carry `GTK_PITLINK`, flag-off byte-identical behaviour | flag-off run differs from the base core |
| **P2 bench** | M1 | EYES p95 flip→host ≤ 20 ms; HANDS: X, d-pad, stick, **pressure** all obeyed in GT5P menus; WATCH aligned to the flip; GT5P speed/position/lap RAM map found | any GT5P button ignored with HANDS in EXCLUSIVE |
| **P3 rig** | car8, USB-net | EYES p95 ≤ 50 ms at 640×360@30; RSX-thread cost ≤ 0.3 ms/frame; zero added hard syncs; dyno A/B tap on/off within noise (N≥3) | `perfect_pct` drops with the tap on |
| **P4 autonomy** | car8 | GT5P: boot → Time Trial → 3 clean laps closed-loop, unattended; GT6 (BCUS98296) boot-to-menu verdict on 0.9.0.3 vs 0.9.1.2 with liveness classification | a run needs a human to finish |
| **P5 matrix** | car8 + car12 | overnight core × title matrix, ledger-attributed, dyno verdicts; manual §0 row + install knob | an unattributable row |

---

## 8. Decisions for the operator

1. **Which core line carries Pitlink first** — certified 0.9.0.3, or the 0.9.1.x line where
   the GT6 question lives?
2. **USB-net as the required link** for closed-loop work (WiFi stays a viewer-only
   fallback)?
3. **Default authority** — `merge` (human can always take the wheel) or `exclusive` for
   unattended runs?
4. **A local model on the M1 at all?** Claude is the vision brain and labeller; the loop
   itself is RAM + numpy. A local VLM is optional, not on the critical path.

---

## 9. From the 2026-10-10 prototype

- **Kept:** `tools/autopilot/` — the `/proc/<pid>/mem` RAM reader, `ramscan.py`, the
  `gtpilot.py` session (dead-man's switch), `recover` (the kit's `recovery.sh`), and the
  pad-before-launch rule. It remains the no-mint fallback on any stock core.
- **Parked:** the uinput + `--input-config` pad (game ignores pressure-class buttons —
  unresolved), InputPlumber `SendEvent` (upstream bug in 0.79), `grim` as primary vision.
- **Laws learned:** create the pad before `cellPadInit`; never use Restart Game; never run a
  control loop over WiFi.
