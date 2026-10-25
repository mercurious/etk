# ONBOARD: the car's screen on the pit wall

*Mechanism: Pitlink EYES + an ONBD screencopy feed over PLUSB, shown in a Tk window, with a 60 s frame ring.*

ONBOARD is a scrcpy-class live mirror from a ROCKNIX car to the M1. It has two sources and one window.
Plan and wire format: `etk-dossiers/Onboard_LiveMirror_20261025.md` §2 and §8.3.

| Source | What it shows | Where it comes from |
|---|---|---|
| **PANEL** (default) | what the panel would show: ES, Pitstop, RPCS3 screens, toasts, the game with the HUD | ONBD v1 on `@etk-onboard-usb`. The broker opens a car channel with target `onboard`. |
| **GAME** | game pixels only, flip-stamped, 30 Hz | PLNK v1 FRAME pushes on `@etk-pitlink-usb`, as an **observer** |

```bash
tools/onboard/onboard.py                 # the window
tools/onboard/onboard.py --game          # GAME only: the PANEL channel is never opened, so nothing captures on the car
tools/onboard/onboard.py --panel         # PANEL only: never opens PLNK (no HELLO: safe beside any hunt)
tools/onboard/onboard.py --wheel         # eyes on a car with no driver: keep the wheel the HELLO lands (idle)
tools/onboard/onboard.py --snap out.png  # one frame from the active source, no window
tools/onboard/onboard.py --ring "$RUN"   # flush the ring into a hunt run directory ($RUN/ring/)
```

Keys: `p` pin PANEL · `g` pin GAME · `a` auto · `s` snap (to `<ring>/snaps/`) · `r` record on/off · `q` quit.

Other flags: `--record` (start recording at launch), `--fps N --quality Q --scale S` (the PANEL CTRL sent
when the channel opens, carrying only the keys you give: with none of them no CTRL is sent and the car's
own knobs, e.g. `ETK_ONBOARD_QUALITY`, rule; the car clamps fps to its policy), `--addr` (the PLNK address, default
`$PITLINK_ADDR` else `usb`), `--onboard-addr` (default `@etk-onboard-usb`), `--gst` (see H.264 below),
`--ring-mb` (the ring's payload cap, default 1024 MB), `-v`.

## Source policy

- PANEL is shown by default.
- The window switches to GAME when GAME frames are flowing and PANEL sends nothing. PANEL sends nothing
  when its INFO says RUNNING with policy 0, when its link is down, or when no frame has arrived for
  max(2 s, 3 intervals).
- It switches back to PANEL when PANEL frames come again. While both sources are live, it shows PANEL.
- When neither source is live, the window keeps the last choice and the status bar reads STALE.
- `p`/`g` pin a source and `a` returns to auto. A pin is host-side only: `p` never sends the car
  `{"pin":true}`, so the car's RUNNING policy, which keeps the silver cores free during a race, stays in force.

**Status bar:** source (`*` = pinned) · fps measured on the host · frame age since its receive · link
state for both sources · flip # · PANEL seq · title (from HELLO_OK) · `REC` · `STALE`. Car time and
host time are different clocks, so the bar shows frame age instead of a latency.

**Stale:** GAME is stale when its flip # has not changed for 2 s. PANEL is stale when its seq has not
changed for 2 intervals; the interval is the slower of INFO's fps and the measured rate, with a 0.25 s
floor. A PANEL that is deliberately silent (RUNNING, policy 0) shows as `OFF`, not stale.

## Observer only

On PLNK, ONBOARD sends a HELLO and nothing else that touches the emulator: no PAD, VIDEO, SNAP, freeze,
single-flip or EXIT (`test_onboard.py` [LAW] greps for these). PLNK v1 has no "observer please"
request: the first HELLO while the wheel is free gets the wheel. When ONBOARD's HELLO lands the wheel, it
closes that link at once and tries again 5 s later. The status bar then reads `no controller on the car:
refused the wheel`.

What that does and does not guarantee: once a hunt driver holds the wheel, ONBOARD can never take it
(roles are decided at HELLO; an observer is never promoted). But while NO controller holds it -- e.g.
at every RPCS3 relaunch -- ONBOARD holds the wheel for one window every 5 s: from its HELLO_OK until its
CLOSE reaches RPCS3 (a broker round trip plus a PLUSB CLOSE through the car's relay, milliseconds over
USB). A driver whose HELLO lands inside that window becomes an observer, and the hunt drivers exit on
that (`gtdrive.py`, `pitlink.py` control ops, `gt5_trial.py`). So beside a hunt that has not yet
taken the wheel, run **`--panel`** (it never opens PLNK at all) or start ONBOARD after the driver holds
the wheel. In-game snaps use the observer's `latest_frame()`/`wait_frame()` only.

**`--wheel` vs the default.** The default (refuse the wheel, retry every 5 s) is for running beside a
hunt: ONBOARD never stands between a driver and the wheel for longer than that one window. But with no
driver at all -- a headless car (car8) running a game with nobody on PLNK -- the default shows nothing in
a game: GAME refuses the wheel and PANEL is silent by RUNNING policy. `--wheel` is for that case: when
the HELLO lands the wheel, ONBOARD KEEPS it as an idle controller, so the game's frames reach the window.
It still sends no control op of any kind (no PAD, VIDEO, SNAP, freeze, single-flip, EXIT; snaps stay
`latest_frame()`/`wait_frame()`), and beside a driver that already holds the wheel it is a plain
observer. The cost is the wheel itself: the status bar reads `holding the wheel (--wheel): a hunt cannot
start until this window quits`, and any driver that HELLOs meanwhile lands as an observer and exits. Quit
the window before starting a hunt.

## The ring (the forensic instrument)

The ring holds the last 60 s of received frames from both sources in memory, with the payloads exactly
as they arrived. It flushes to `DIR/ring/` on these events:

- **Link death:** `PitlinkClosed`, socket EOF, or the broker or USB link going away. A source's death
  flushes only when THAT source delivered frames on the link that died and since the last flush, at most
  once per 30 s per source, so a broker dying under both links writes one directory and a flapping link
  writes one, not one per flap.
- **Not on END.** A car END (policy, a capture failure, ONBOARD off) is the car stopping capture on
  purpose, not a death: no flush. The viewer reopens the channel 30 s later, or never again when the
  END reason says ONBOARD is off (`ETK_ONBOARD=0`, "onboard off/disabled").
- **Record:** `r` or `--record` writes the ring so far, then appends every live frame until `r` again
  or `q`.

`DIR` is `--ring DIR`, or `state/onboard/<epoch>/` by default. A later flush in the same `DIR` goes to
`ring.2/`, `ring.3/`, and so on. Nothing is ever overwritten.

| File | Contents |
|---|---|
| `index.tsv` | One row per frame: `source flip vblank host_ns recv_t recv_epoch seq offset len w h codec flags fps watch`. `host_ns` is the car's `CLOCK_MONOTONIC` (PLNK `host_ns` or ONBD `car_ns`). `recv_t` is host monotonic time and `recv_epoch` is host wall time. `-` means the field does not apply to that source. |
| `frames.bin` | The payloads concatenated in arrival order, never re-encoded. Each row's `offset`/`len` addresses its payload. |
| `last.png` | The most recent decodable frame. |
| `sheet.png` | A 4×4 contact sheet of the last 16 decodable frames, oldest first. Each tile is labelled with its flip or seq and its age relative to the newest frame. |
| `meta.json` | The reason, `epoch`, `duration_s` (joins the ledger row by `[epoch - duration_s, epoch]`, the Pitlink recorder's rule), per-source counts, HELLO_OK, the last INFO, and the link events. |

All disk writes go through one background worker with a bounded queue, so a flush costs the receive
thread only a list copy. One flush is in flight at a time: a request while one is queued is dropped (the
queued one holds those frames), so a storm of requests can pin at most one snapshot in memory and write
at most one copy to disk. A recorder that outruns the disk drops rows (counted) rather than memory.

## H.264 (PANEL codec 3)

`--gst` decodes H.264 into the same Tk window with GStreamer: `appsrc ! h264parse ! openh264dec !
videoconvert ! appsink`. Without `--gst`, H.264 frames are still ring-recorded raw, and the window shows
the last decodable picture with the note `H.264 (needs --gst)`. `last.png` and `sheet.png` always use the
last decodable (raw, zstd or JPEG) frames. A GTK4 window is not built; `--gst` is only the decoder.

## Broker

`usb_broker.py serve` listens on three local sockets: `@etk-pitlink-usb` (PLNK), `@etk-garage-usb`
(garage) and `@etk-onboard-usb` (ONBOARD; each connection becomes one car channel with target
`b"onboard"`). A broker started on a non-default `--socket` puts its ONBOARD socket beside it at
`<socket>-onboard`, so tests never collide with the M1's real broker. ONBOARD starts the broker on first
use, as `PitlinkClient("usb")` does. A broker that was already running before this change has no
`@etk-onboard-usb`: GAME works, and PANEL reads `down` until that broker is restarted.

## Tests

```bash
python3 tools/onboard/test_onboard.py
```

The suite is headless and uses loopback only. It drives `tools/pitlink/fake_server.py` (GAME, with a
controller holding the wheel) and a fake ONBD producer (INFO, then JPEG/raw/zstd/H.264 frames), and
runs one test through `usb_broker` with a fake USB pipe and `CarRelay` (target `onboard`).
