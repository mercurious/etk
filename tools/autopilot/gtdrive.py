#!/usr/bin/env python3
"""gtdrive -- closed-loop GT5P driver over Pitlink (raw USB by default).

Eyes: GT5P's own driving line (Driving Options > Driving Line: On) -- blue = throttle,
red = brake -- and the tachometer needle. Hands: left stick X steers, R2 throttle, L2 brake,
left stick Y shifts. On the wire UP (ly -1) upshifts: 2026-10-10 the first run flicked ly +1
"to upshift" and walked 2nd -> 1st, then sat on the limiter at 46 mph. Every PAD
carries a short TTL, so if this process dies the car's dead-man releases the pad.

  gtdrive.py [--secs 60] [--addr usb] [--kp 1.6] [--snap-every 3] [--out DIR]

Bumper/roof camera, 640x360 tap. Calibrated on High Speed Ring, Skyline GT-R V-spec II, MT.
"""
import argparse
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pitlink"))
import plnk as P  # noqa: E402
from client import PitlinkClient, PitlinkError  # noqa: E402

W, H = 640, 360
BAND = (175, 255)            # road rows: below the horizon signage, above the gauges
TACH_PIVOT = (400.0, 313.0)  # needle pivot (640x360)
TACH_R = (8, 30)             # needle shaft radii (the redline arc sits further out)
UP_DEG, DOWN_DEG = 32.0, 120.0  # ~7000 rpm / ~3500 rpm on this dial (1k = 170 deg, 8k = 10 deg)
LY_UPSHIFT, LY_DOWNSHIFT = -1.0, +1.0


def line_px(rgb):
    band = rgb[BAND[0]:BAND[1]].astype(np.int16)
    R, G, B = band[..., 0], band[..., 1], band[..., 2]
    blue = (B > 150) & (B - R > 90) & (B - G > 40)
    red = (R > 150) & (R - G > 90) & (R - B > 70)
    return blue, red


def not_kerb(red, band_rgb):
    """Red LINE chevrons sit on grey asphalt; red KERB blocks alternate with white ones. Drop red
    pixels with white within 4 px (any direction)."""
    R, G, B = band_rgb[..., 0], band_rgb[..., 1], band_rgb[..., 2]
    white = (R > 190) & (G > 190) & (B > 190)
    near = np.zeros_like(white)
    for d in (1, 2, 3, 4):
        near[d:] |= white[:-d]
        near[:-d] |= white[d:]
        near[:, d:] |= white[:, :-d]
        near[:, :-d] |= white[:, d:]
    return red & ~near


def road_cx(rgb, min_px=2500):
    """Centre column of the visible asphalt (grey, unsaturated, not dark wall) in the road band,
    or None when too little road is in view."""
    band = rgb[BAND[0]:BAND[1]].astype(np.int16)
    mx, mn = band.max(-1), band.min(-1)
    grey = (mx - mn < 22) & (mx > 85) & (mx < 215)
    # An inside YELLOW line (ovals: the racing surface's apron edge) bounds the road: asphalt
    # left of it is apron / pit road (Daytona run 8 centred on the pit road and drove in).
    R, G, B = band[..., 0], band[..., 1], band[..., 2]
    yellow = (R > 170) & (G > 140) & (B < 100) & (R - B > 90)
    for y in np.nonzero(yellow.sum(1) >= 2)[0]:
        xs = np.nonzero(yellow[y])[0]
        edge = int(xs.max()) if xs.max() < 2 * band.shape[1] // 3 else None
        if edge is not None:
            grey[y, :edge] = False
    cols = grey.sum(0)
    total = int(cols.sum())
    if total < min_px:
        return None
    return float((np.arange(cols.size) * cols).sum() / total)


def wall_share(rgb):
    """(left, right) share of dark unsaturated pixels (barrier/wall) in the road band halves."""
    band = rgb[BAND[0]:BAND[1]].astype(np.int16)
    mx, mn = band.max(-1), band.min(-1)
    wall = (mx < 85) & (mx - mn < 30)
    h = wall.shape[1] // 2
    return float(wall[:, :h].mean()), float(wall[:, h:].mean())


MAP = (20, 175, 70, 132)   # minimap box (x0, x1, y0, y1), above where the scenery leaks red
# High Speed Ring (GT5P, minimap coords): the sharp top-right corner. The dot comes UP the
# right side from the start line; run 6 met this corner at 77 mph and parked in the tyre
# wall at (146, 84). Brake on the approach, carry half throttle until the exit.
def hsr_brake(x, y):
    return x > 155 and 86 < y < 100


def hsr_slow(x, y):
    return x > 147 and y < 100


# ...and just past it the road keeps bending LEFT while the near-field line still points
# straight: runs 6/7 went straight on into the outside tyre wall at (146-148, 84). Hold a
# left bias at low throttle until the dot is well along the top straight.
def hsr_left(x, y):
    return 136 < x <= 157 and y <= 89


def map_dot(rgb, y1=None):
    """The car's red dot on the minimap, or None. y1 overrides the box bottom (Daytona's oval
    minimap reaches lower than High Speed Ring's)."""
    x0, x1, y0, y1d = MAP
    y1 = y1 or y1d
    reg = rgb[y0:y1, x0:x1].astype(np.int16)
    R, G, B = reg[..., 0], reg[..., 1], reg[..., 2]
    ys, xs = np.nonzero((R > 180) & (G < 70) & (B < 70))
    if not 2 <= len(xs) <= 40:
        return None
    return float(xs.mean() + x0), float(ys.mean() + y0)


def needle_deg(rgb):
    """Tach needle angle (deg, 0 = right, 90 = up), or None."""
    px, py = TACH_PIVOT
    reg = rgb[255:350, 340:450].astype(np.int16)
    R, G, B = reg[..., 0], reg[..., 1], reg[..., 2]
    ys, xs = np.nonzero((R > 160) & (G < 90) & (B < 90))
    if not len(xs):
        return None
    dx, dy = xs + 340 - px, py - (ys + 255)
    r = np.hypot(dx, dy)
    keep = (r >= TACH_R[0]) & (r <= TACH_R[1]) & (dy > -6)
    if keep.sum() < 3:
        return None
    return math.degrees(math.atan2(dy[keep].mean(), dx[keep].mean()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--addr", default=None)
    ap.add_argument("--secs", type=float, default=60.0)
    ap.add_argument("--kp", type=float, default=1.6)
    ap.add_argument("--kd", type=float, default=0.8)
    ap.add_argument("--cruise", type=float, default=1.0, help="throttle on a blue line")
    ap.add_argument("--blind-thr", type=float, default=0.45, help="throttle with no line in view (asphalt fallback)")
    ap.add_argument("--map-y1", type=int, default=0, help="minimap search box bottom row (Daytona: 155)")
    ap.add_argument("--no-line", action="store_true", help="ignore the driving line: asphalt + walls only (ovals)")
    ap.add_argument("--mt", action="store_true", help="manual box: shift on the tach needle (default: AT, no shifts)")
    ap.add_argument("--track", default="", help="track-specific minimap zones: hsr (High Speed Ring); "
                    "default none (line-following only, e.g. Daytona)")
    ap.add_argument("--max-restarts", type=int, default=5,
                    help="GT5P's own race Restart (pause > Restart) when stuck; then give up")
    ap.add_argument("--log-every", type=int, default=15, help="frames between log lines")
    ap.add_argument("--wait-race", type=float, default=0.0,
                    help="first wait up to S seconds for the in-race HUD (tach needle) -- start the "
                         "driver before pressing Start so no rolling-start frame goes undriven")
    ap.add_argument("--snap-every", type=float, default=3.0)
    ap.add_argument("--out", default="/tmp/gtdrive")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    c = PitlinkClient(a.addr, timeout=8)
    info = c.hello()
    if not c.controller:
        sys.exit("gtdrive: another client holds the wheel (controller role)")
    if a.wait_race > 0:
        print(f"gtdrive: waiting up to {a.wait_race:.0f} s for the race HUD", flush=True)
        deadline, seen = time.time() + a.wait_race, 0
        while True:
            if time.time() > deadline:
                c.close()
                print("gtdrive: no race HUD -- not driving", flush=True)
                return 4
            try:
                f = c.wait_frame(timeout=1.0, after_flip=c.ping()[0])
            except PitlinkError:
                continue
            rgb = f.rgb()
            seen = seen + 1 if rgb.mean() > 50 and needle_deg(rgb) is not None else 0
            if seen >= 3:  # three HUD frames in a row: a menu never shows the tach needle
                break
    print(f"gtdrive: {info.get('title')} -- driving for {a.secs:.0f} s", flush=True)

    t0 = time.time()
    last_flip, prev_err, steer = 0, 0.0, 0.0
    shift_left, shift_dir, last_shift = 0, 0, 0.0
    brake_since = stuck_since = prev_small = zone_since = dot_anchor = None
    rc, restarts = 0, 0
    next_snap, n = t0, 0
    try:
        while time.time() - t0 < a.secs:
            try:
                f = c.wait_frame(timeout=1.0, after_flip=last_flip)
            except PitlinkError:  # one late frame is not a reason to drop the wheel
                continue
            last_flip = f.flip
            rgb = f.rgb()
            if rgb.shape[:2] != (H, W):
                sys.exit(f"gtdrive: expects the 640x360 tap, got {rgb.shape[1]}x{rgb.shape[0]}")
            ang = needle_deg(rgb)
            small = rgb[60:250:4, ::4].astype(np.int16)
            motion = float(np.abs(small - prev_small).mean()) if prev_small is not None else 99.0
            prev_small = small
            blue, red = line_px(rgb)
            red = not_kerb(red, rgb[BAND[0]:BAND[1]].astype(np.int16))
            if a.no_line:  # Daytona: the pit-lane entrance's blue markings read as the line
                blue[:] = False
                red[:] = False
            nb, nr = int(blue.sum()), int(red.sum())
            if nb >= 30:  # steer on the BLUE line only: red kerbs dragged run 1 onto the inside kerb
                ys, xs = np.nonzero(blue)
                wgt = 1.0 + (BAND[1] - BAND[0] - ys) / (BAND[1] - BAND[0])  # far rows lead the turn
                cx = float((xs * wgt).sum() / wgt.sum())
                err = (cx - W / 2) / (W / 2)
                raw = a.kp * err + a.kd * (err - prev_err)
                prev_err = err
                steer = 0.6 * steer + 0.4 * max(-1.0, min(1.0, raw))
            elif (rx := road_cx(rgb)) is not None:
                # No line in view (Daytona's banking hides it off to the left, 2026-10-10): steer
                # for the middle of the visible asphalt instead of holding the wheel straight.
                cx = rx
                err = (cx - W / 2) / (W / 2)
                raw = a.kp * 0.75 * err + a.kd * (err - prev_err)
                prev_err = err
                steer = 0.6 * steer + 0.4 * max(-1.0, min(1.0, raw))
            else:
                cx, err = None, None
                steer *= 0.9
            # Wall repulsion: dark, unsaturated barrier filling one side of the road band. Daytona
            # runs 3-5 drifted wide in the banking at 84 mph (centring on asphalt alone turns too
            # little) -- right-side wall share went 0.2 (straight) -> 0.86-0.97 (on the wall).
            wall_l, wall_r = wall_share(rgb)
            push = 2.0 * (max(0.0, wall_l - 0.4) - max(0.0, wall_r - 0.4))
            if push:
                steer = max(-1.0, min(1.0, steer + push))
            # A red LINE in the lane, no blue left -- and never more than 1.5 s at a time: run 2
            # parked on the start line's red/white paint with L2 down for 30 s.
            now = time.time()
            if nr > 120 and nr > nb:
                brake_since = brake_since or now
                braking = now - brake_since < 1.5
            else:
                brake_since, braking = None, False
            thr = 0.0 if braking else (a.cruise if nb >= 30 else a.blind_thr)
            if max(wall_l, wall_r) > 0.6:  # on the wall: lift so the steering can bite
                thr = min(thr, 0.4)
            brk = min(1.0, nr / 250.0) if braking else 0.0
            # The line swinging to a screen edge = a tight corner coming (run 3 met the hairpin
            # at 67 mph with the line at x 608 then 32): lift and trail the brake in proportion.
            # ...only while MOVING: run 5 sat at 0 mph for 60 s holding throttle 0.75 AND brake 0.19.
            if err is not None and abs(err) > 0.55 and not braking and motion > 4.0:
                tight = min(1.0, (abs(err) - 0.55) / 0.35)
                thr = min(thr, 1.0 - 0.8 * tight)
                brk = max(brk, 0.6 * tight)
            # Track position: brake zones off the minimap dot (line chevrons are too sparse at
            # 77 mph to warn in time).
            dot = map_dot(rgb, a.map_y1)
            zones = a.track == "hsr"
            if not zones:
                pass
            elif dot and hsr_brake(*dot):
                zone_since = zone_since or now
                if now - zone_since < 1.0:
                    thr, brk = 0.0, 1.0
                else:
                    thr, brk = min(thr, 0.5), 0.0
            elif dot and hsr_left(*dot):
                steer = min(steer, -0.65)
                thr, brk = min(thr, 0.4), 0.0
            elif dot and hsr_slow(*dot):
                thr, brk = min(thr, 0.5), 0.0
            elif dot:
                zone_since = None
            # A colour FLOOD is a painted wall / tyre barrier, not the line (run 6: b18519 r24741).
            wall = nb + nr > 3000
            if wall:
                thr, brk = 0.0, 1.0
            # Stuck: no usable line and a picture that has stopped moving for 3 s (nose in a wall)
            # -> GT5P's OWN race Restart (in-game pause menu > Restart; never RPCS3's "Restart
            # Game", which wedges the emulator), then drive on from the rolling start.
            # (Run 4 tried "needle at idle": the needle read > 140 deg at 51 mph.)
            # Second trigger: the minimap dot parked (<= 1.5 px in 4 s). Daytona run 3 sat nose-on
            # to the wall at 0 mph for a minute while the lap timer and the asphalt grain kept
            # `motion` at 5-7, so the frozen-picture test never fired.
            if dot:
                if dot_anchor is None or math.dist(dot, dot_anchor[0]) > 1.5:
                    dot_anchor = (dot, now)
            else:  # a lost dot is not a parked car (run 7 left the map box at y 132 and restarted)
                dot_anchor = None
            parked = dot_anchor is not None and now - dot_anchor[1] > 4.0
            if ((nb < 30 or wall) and motion < 2.0) or parked:
                stuck_since = stuck_since or now
                if parked or now - stuck_since > 3.0:
                    restarts += 1
                    why = "dot parked 4 s" if parked else "picture frozen 3 s"
                    print(f"gtdrive: STUCK at map {dot and tuple(round(v) for v in dot)} ({why})"
                          f" -- race Restart {restarts}/{a.max_restarts}", flush=True)
                    if restarts > a.max_restarts:
                        rc = 3
                        break
                    c.release(0)
                    for btn, settle in (("start", 1.2), ("right", 0.6), ("cross", 7.0)):
                        c.press(btn, ms=150)
                        time.sleep(settle)
                    stuck_since = zone_since = prev_small = dot_anchor = None
                    steer, prev_err = 0.0, 0.0
                    continue
            else:
                stuck_since = None

            if a.mt and shift_left == 0 and ang is not None and now - last_shift > 0.9:
                if thr > 0.8 and ang < UP_DEG:
                    shift_left, shift_dir, last_shift = 3, +1, now
                elif braking and ang > DOWN_DEG:
                    shift_left, shift_dir, last_shift = 3, -1, now
                if shift_left:
                    print(f"  shift {'UP' if shift_dir > 0 else 'DOWN'} at needle {ang:.0f} deg", flush=True)
            ly = 0.0
            if shift_left:
                ly = LY_UPSHIFT if shift_dir > 0 else LY_DOWNSHIFT
                shift_left -= 1

            st = P.PadState(port=0, ttl_ms=300)
            st.set("lx", steer)
            st.set("ly", ly)
            if thr > 0:
                st.set("r2", thr)
            if brk > 0:
                st.set("l2", brk)
            c.pad(st)

            n += 1
            if n % a.log_every == 0:
                print(f"flip {f.flip:6d} fps {f.fps:4.1f} line b{nb:4d} r{nr:4d} cx {"-" if cx is None else round(cx):>4} "
                      f"steer {steer:+.2f} thr {thr:.2f} brk {brk:.2f} wall {wall_l:.2f}|{wall_r:.2f} motion {motion:4.1f} map {dot and tuple(round(v) for v in dot)}",
                      flush=True)
            if now >= next_snap:
                f.png(os.path.join(a.out, f"d{int(now - t0):04d}.png"))
                next_snap = now + a.snap_every
    except KeyboardInterrupt:
        pass
    finally:
        try:
            c.release(0)
        finally:
            c.close()
    print(f"gtdrive: released after {time.time() - t0:.1f} s, {n} frames", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
