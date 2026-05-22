#!/usr/bin/env python3
# ============================================================================
# Task 0b spike -- prove the headless-ish RPCS3 install flow. DISPOSABLE.
# Not part of ETK. Runs on the rig.
#
#   python3 /tmp/task0b_install_confirm_spike.py
#
# Flow:
#   1. mako notification: "installing..."
#   2. launch  rpcs3-sa --installpkg <pkg>   (GUI, attached to live sway)
#   3. wait for the "Do you want to install this package?" dialog
#   4. focus the RPCS3 window (swaymsg), then inject KEY_ENTER via /dev/uinput
#      -> Enter triggers the dialog's default "Install" button (no coordinates)
#   5. tail RPCS3.log, push mako progress updates (replaces_id = same toast)
#   6. detect the new game/<TITLE_ID> + USRDIR/EBOOT.BIN -> done
#   7. kill RPCS3, final notification
#
# Prints a full report at the end -- paste it back.
# ============================================================================
import os, sys, time, struct, fcntl, glob, subprocess, json

ENV = dict(os.environ)
ENV.update(
    XDG_RUNTIME_DIR="/var/run/0-runtime-dir",
    WAYLAND_DISPLAY="wayland-1",
    QT_QPA_PLATFORM="wayland",
    SWAYSOCK="/var/run/0-runtime-dir/sway-ipc.0.sock",
)

RPCS3      = "/usr/bin/rpcs3-sa"
GAME_DIR   = "/storage/games-internal/roms/bios/rpcs3/dev_hdd0/game"
RPCS3_LOG  = "/storage/.cache/rpcs3/RPCS3.log"
OUT_LOG    = "/tmp/task0b_install_out.log"
PKG_GLOB   = "/storage/games-internal/roms/temp/GT_TT_CHALLENGE/*.pkg"
HARD_CAP_S = 600          # absolute ceiling for the whole monitor loop

def log(*a): print("[spike]", *a, flush=True)

# ---------------------------------------------------------------- mako notify
_nid = ["0"]
def notify(summary, body, timeout=10000):
    try:
        r = subprocess.run(
            ["dbus-send", "--session", "--print-reply",
             "--dest=org.freedesktop.Notifications",
             "/org/freedesktop/Notifications",
             "org.freedesktop.Notifications.Notify",
             "string:ETK Pitstop", "uint32:" + _nid[0], "string:",
             "string:" + summary, "string:" + body,
             "array:string:", "dict:string:variant:", "int32:%d" % timeout],
            env=ENV, capture_output=True, text=True, timeout=10)
        for ln in r.stdout.splitlines():
            ln = ln.strip()
            if ln.startswith("uint32"):
                _nid[0] = ln.split()[1]
                break
    except Exception as e:
        log("notify failed:", e)

# ----------------------------------------------------------- uinput keyboard
def _IOC(d, t, nr, size): return (d << 30) | (size << 16) | (ord(t) << 8) | nr
UI_SET_EVBIT  = _IOC(1, "U", 100, 4)
UI_SET_KEYBIT = _IOC(1, "U", 101, 4)
UI_DEV_SETUP  = _IOC(1, "U", 3, 92)
UI_DEV_CREATE = _IOC(0, "U", 1, 0)
UI_DEV_DESTROY= _IOC(0, "U", 2, 0)
EV_SYN, EV_KEY = 0x00, 0x01
KEY_ENTER = 28

def make_keyboard():
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    # UI_SET_EVBIT / UI_SET_KEYBIT take the bit number as a plain int arg
    # (the kernel uses `arg` directly -- NOT a pointer to a buffer).
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
    fcntl.ioctl(fd, UI_SET_KEYBIT, KEY_ENTER)
    # struct uinput_setup: input_id(bustype,vendor,product,version) + name[80] + ff_max
    setup = (struct.pack("HHHH", 0x03, 0x1234, 0x5678, 1)
             + b"ETK Virtual Keyboard".ljust(80, b"\0")
             + struct.pack("I", 0))
    fcntl.ioctl(fd, UI_DEV_SETUP, setup)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    return fd

def _emit(fd, t, c, v):
    os.write(fd, struct.pack("llHHi", 0, 0, t, c, v))

def tap(fd, code):
    _emit(fd, EV_KEY, code, 1); _emit(fd, EV_SYN, 0, 0)
    time.sleep(0.06)
    _emit(fd, EV_KEY, code, 0); _emit(fd, EV_SYN, 0, 0)

# ------------------------------------------------------------------- swaymsg
def sway(*args):
    try:
        return subprocess.run(["swaymsg", *args], env=ENV,
                              capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        log("swaymsg failed:", e); return ""

def find_nodes():
    """Return [(con_id, app_id, name), ...] for every rpcs3-related window."""
    try:
        tree = json.loads(sway("-t", "get_tree"))
    except Exception:
        return []
    hit = []
    def walk(n):
        aid = (n.get("app_id") or "")
        nm  = (n.get("name") or "")
        if "rpcs3" in aid.lower() or "rpcs3" in nm.lower() or "pkg" in nm.lower():
            hit.append((n.get("id"), aid, nm))
        for k in ("nodes", "floating_nodes"):
            for c in n.get(k, []):
                walk(c)
    walk(tree)
    return hit

def is_dialog(node):
    nm = (node[2] or "").lower()
    return "install" in nm or "pkg" in nm

# ----------------------------------------------------------------- helpers
def game_set():
    try:
        return set(os.listdir(GAME_DIR))
    except FileNotFoundError:
        return set()

def log_tail(n=40):
    try:
        with open(RPCS3_LOG, "rb") as f:
            data = f.read()
        return data.decode("utf-8", "replace").splitlines()[-n:]
    except Exception:
        return []

# ===========================================================================
def main():
    pkgs = glob.glob(PKG_GLOB)
    if not pkgs:
        log("NO PKG FOUND at", PKG_GLOB); sys.exit(1)
    pkg = pkgs[0]
    log("pkg:", pkg)

    before = game_set()
    log("game/ before: %d entries" % len(before))

    notify("ETK Pitstop",
           "Installing GT5 Time Trial Challenge. RPCS3 will open briefly - "
           "do not touch the controller.")

    # 1. launch rpcs3 --installpkg, detached
    of = open(OUT_LOG, "wb")
    proc = subprocess.Popen([RPCS3, "--installpkg", pkg],
                            env=ENV, stdout=of, stderr=subprocess.STDOUT,
                            start_new_session=True)
    log("launched rpcs3 pid=%d" % proc.pid)

    # 2. wait for the install-dialog WINDOW to actually map in sway
    #    (RPCS3.log parses the PKG header BEFORE the window is mappable, so
    #    polling sway's tree is the correct "dialog is up" signal).
    t0 = time.time()
    dlg = None
    while time.time() - t0 < 90:
        time.sleep(1)
        for n in find_nodes():
            if is_dialog(n):
                dlg = n
                break
        if dlg:
            log("install dialog window mapped after %.0fs: %s"
                % (time.time() - t0, dlg))
            break
    if not dlg:
        log("DIALOG WINDOW NEVER MAPPED -- aborting"); proc.kill(); sys.exit(2)
    dialog = True
    node = dlg

    # 3. focus the dialog window, build the uinput keyboard, inject Enter
    sway("[con_id=%d]" % dlg[0], "focus")
    time.sleep(0.6)
    log("creating uinput keyboard...")
    kfd = make_keyboard()
    time.sleep(1.8)             # let libinput/sway register the new keyboard
    confirmed = False
    for attempt in range(1, 4):
        sway("[con_id=%d]" % dlg[0], "focus")   # re-assert focus each try
        time.sleep(0.3)
        log(">>> injecting KEY_ENTER (attempt %d)" % attempt)
        tap(kfd, KEY_ENTER)
        time.sleep(3)
        still = [n for n in find_nodes()
                 if n[0] == dlg[0] or is_dialog(n)]
        if not still:
            confirmed = True
            log("dialog dismissed after attempt %d" % attempt)
            break
        log("dialog still present, will retry")
    subprocess.run(["grim", "/tmp/task0b_after_enter.png"], env=ENV,
                   capture_output=True)
    log("confirmed=%s  post-Enter screenshot: /tmp/task0b_after_enter.png"
        % confirmed)

    notify("Installing GT5 Time Trial Challenge",
           "Extracting package files...")

    # 4. monitor: detect new game dir + EBOOT, watch log
    new_id = None
    eboot_seen = False
    precompile_noted = False
    last_log_len = 0
    quiet_since = None
    interesting = []     # captured log lines for the report
    t1 = time.time()
    while time.time() - t1 < HARD_CAP_S:
        time.sleep(3)
        # new game dir?
        if not new_id:
            new = [d for d in (game_set() - before) if "lock" not in d.lower()]
            if new:
                new_id = new[0]
                log("NEW game dir: %s" % new_id)
        # eboot present -> extraction done
        if new_id and not eboot_seen:
            if os.path.exists(os.path.join(GAME_DIR, new_id, "USRDIR", "EBOOT.BIN")):
                eboot_seen = True
                log("EBOOT.BIN present -- file extraction complete")
                notify("Installing GT5 Time Trial Challenge",
                       "Files installed. Precompiling shaders (normal, please wait)...")
        # capture interesting log lines
        tail = log_tail(80)
        for l in tail:
            if any(k in l for k in ("PKG:", "PPU:", "SPU:", "Compil", "cache",
                                    "nstall", "Progress", "%")):
                if l not in interesting:
                    interesting.append(l)
        # quiet detection: log stopped growing
        try:
            cur = os.path.getsize(RPCS3_LOG)
        except Exception:
            cur = last_log_len
        if cur == last_log_len:
            if quiet_since is None:
                quiet_since = time.time()
            elif eboot_seen and time.time() - quiet_since > 25:
                log("log quiet >25s after EBOOT -- treating install as complete")
                break
        else:
            quiet_since = None
            last_log_len = cur
        # if rpcs3 died on its own
        if proc.poll() is not None:
            log("rpcs3 exited on its own, rc=%s" % proc.returncode)
            break

    elapsed = time.time() - t1
    log("monitor loop ended after %.0fs" % elapsed)

    # 5. final state
    notify("ETK Pitstop",
           ("GT5 Time Trial Challenge installed. Press START > Game Settings "
            "> Update Gamelists to see it.") if eboot_seen else
           "Install did not complete cleanly - see spike output.")

    # 6. kill rpcs3
    log("killing rpcs3...")
    try:
        fcntl.ioctl(kfd, UI_DEV_DESTROY); os.close(kfd)
    except Exception:
        pass
    subprocess.run(["pkill", "-9", "-f", "rpcs3-sa"], capture_output=True)
    subprocess.run(["pkill", "-9", "-f", "AppRun.wrapped"], capture_output=True)

    # ----- report -----
    print("\n" + "=" * 60)
    print("SPIKE REPORT")
    print("=" * 60)
    print("dialog appeared      :", dialog)
    print("rpcs3 sway node      :", node)
    print("new game dir         :", new_id)
    print("EBOOT.BIN present    :", eboot_seen)
    print("monitor elapsed      : %.0fs" % elapsed)
    if new_id:
        d = os.path.join(GAME_DIR, new_id)
        print("\n-- contents of game/%s --" % new_id)
        for root, _, files in os.walk(d):
            for f in files:
                print("  ", os.path.join(root, f).replace(d, new_id))
        sfo = os.path.join(d, "PARAM.SFO")
        print("PARAM.SFO present    :", os.path.exists(sfo))
    print("\n-- captured RPCS3.log lines (install / precompile) --")
    for l in interesting[-80:]:
        print("  ", l)
    print("\n-- /tmp/task0b_install_out.log tail --")
    try:
        with open(OUT_LOG, "r", errors="replace") as f:
            for l in f.read().splitlines()[-20:]:
                print("  ", l)
    except Exception as e:
        print("  (none)", e)
    print("=" * 60)

if __name__ == "__main__":
    main()
