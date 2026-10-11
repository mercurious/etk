#!/usr/bin/env python3
"""netcon_listen -- the HOST end of the panic text channel (GT5 op, 2026-10-11).

The fast PANIC class dies with nothing on the car's disk: the kmsg recorder fsyncs once a
second from userspace, and a panic stops userspace first. Kernel netconsole sends every
printk, panic text included, as UDP from inside the kernel (netpoll) while the car is still
dying. This listener is the receiving end on the M1, over the USB-net link: each datagram
is stamped with the host's clock and fsync'd to state/netconsole/netcon-<YYYYMMDD>.log,
and a panic/oops/bug line is echoed to the terminal.

It needs a car kernel with CONFIG_NETCONSOLE (not in the GTK kernel yet: a kernel-lane
mint, the operator's) and the car's `netconsole=` target pointed at this host's USB-net
address. Until then it simply receives nothing.

  netcon_listen.py [--port 6666] [--bind 0.0.0.0] [--dir state/netconsole]
"""
import argparse
import os
import re
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ALARM = re.compile(r"Kernel panic|Internal error|Oops|BUG:|Unable to handle|watchdog|SError|"
                   r"Call trace|hard LOCKUP|soft lockup|rcu_.*stall", re.I)


def stamp_line(data, src, t):
    """One log line per datagram: host time (ms), the sender, the text (CR/LF stripped,
    undecodable bytes kept as escapes so a torn message is still evidence)."""
    text = data.decode("utf-8", errors="backslashreplace").rstrip("\r\n")
    return f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))}.{int(t * 1000) % 1000:03d}\t{src}\t{text}\n"


def serve(sock, out_dir, once=False):
    os.makedirs(out_dir, exist_ok=True)
    while True:
        data, (ip, _) = sock.recvfrom(65535)
        t = time.time()
        line = stamp_line(data, ip, t)
        path = os.path.join(out_dir, f"netcon-{time.strftime('%Y%m%d', time.localtime(t))}.log")
        with open(path, "a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        if ALARM.search(line):
            print(line, end="", flush=True)
        if once:
            return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=6666)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--dir", default=os.path.join(HERE, "..", "state", "netconsole"))
    a = ap.parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((a.bind, a.port))
    print(f"netcon_listen: udp {a.bind}:{a.port} -> {os.path.abspath(a.dir)}", flush=True)
    try:
        serve(sock, a.dir)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
