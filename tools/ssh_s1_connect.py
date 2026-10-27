#!/usr/bin/python3
"""ssh ProxyCommand for S1 (hostname "niels"): staggered race over every S1 path.

USER 2026-10-27: "make ssh and the tunnels ROCK SOLID and immediately switch to any of
the 3 ip's for s1 if it still drops at any moment."

Paths (diverse failure domains interleaved): public 157.180.125.52, gateway -> 10.0.0.3,
floating 49.13.39.233, s2 public -> 10.0.0.3, floating 5.75.211.216, IPv6 ::1.
The last path that won is tried first. A path wins when S1's "SSH-" banner arrives
(MSG_PEEK, nothing consumed); the next path starts after STAGGER_S or as soon as the
previous one fails. The winning socket is handed to ssh with ProxyUseFdpass, so no relay
process stays alive; without fdpass (manual use) it relays stdin/stdout itself.
Drill hook: names listed in ~/.ssh/s1_paths.blackhole are sent to 192.0.2.1 (TEST-NET,
never answers) to prove failover. Log: ~/logs/s1_connect.log.
"""
import errno
import os
import selectors
import socket
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
LAST = os.path.join(HOME, ".ssh", "s1_path.last")
BLACKHOLE_FILE = os.path.join(HOME, ".ssh", "s1_paths.blackhole")
LOG = os.path.join(HOME, "logs", "s1_connect.log")
KEY = os.path.join(HOME, ".ssh", "id_ed25519")
TARGET = "10.0.0.3:22"
BLACKHOLE = "192.0.2.1"
STAGGER_S = float(os.environ.get("S1_CONNECT_STAGGER_S", "1.0"))
TCP_TIMEOUT_S = float(os.environ.get("S1_CONNECT_TCP_TIMEOUT_S", "4.0"))
HOP_TIMEOUT_S = float(os.environ.get("S1_CONNECT_HOP_TIMEOUT_S", "7.0"))
DEADLINE_S = float(os.environ.get("S1_CONNECT_DEADLINE_S", "14.0"))
PATHS = [
    ("v4-primary", "tcp", "157.180.125.52", None),
    ("gw-private", "hop", "157.90.168.35", None),
    ("v4-fip-nbg", "tcp", "49.13.39.233", None),
    ("s2-private", "hop", "2.29.22.116", "10.0.0.4"),
    ("v4-fip-fsn", "tcp", "5.75.211.216", None),
    ("v6", "tcp", "2a01:4f9:c013:fdf7::1", None),
]


def log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(LOG, "a") as handle:
            handle.write(f"{stamp} {line}\n")
    except OSError:
        pass


def read_names(path):
    try:
        with open(path) as handle:
            return {word.strip() for word in handle.read().replace(",", " ").split() if word.strip()}
    except OSError:
        return set()


def ordered_paths():
    only = os.environ.get("S1_CONNECT_ONLY", "").replace(",", " ").split()
    paths = [path for path in PATHS if not only or path[0] in only]
    last = next(iter(read_names(LAST)), "")
    paths.sort(key=lambda path: 0 if path[0] == last else 1)
    blackholed = read_names(BLACKHOLE_FILE) | set(os.environ.get("S1_CONNECT_BLACKHOLE", "").replace(",", " ").split())
    return [(name, kind, BLACKHOLE if name in blackholed else addr, alias) for name, kind, addr, alias in paths], last


def start_tcp(addr):
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setblocking(False)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    err = sock.connect_ex((addr, 22))
    if err not in (0, errno.EINPROGRESS, errno.EAGAIN):
        sock.close()
        raise OSError(err, os.strerror(err))
    return sock, None


def tcp_pair():
    """Loopback TCP pair (not socketpair): ssh calls TCP_NODELAY on the passed fd and warns on a unix socket."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    ours = socket.create_connection(listener.getsockname())
    theirs, _ = listener.accept()
    listener.close()
    ours.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return ours, theirs


def start_hop(addr, alias):
    ours, theirs = tcp_pair()
    cmd = ["/usr/bin/ssh", "-F", "/dev/null", "-i", KEY, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
           "-o", "TCPKeepAlive=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "LogLevel=QUIET",
           "-o", "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes"]
    if alias:
        cmd += ["-o", f"HostKeyAlias={alias}"]
    cmd += ["-W", TARGET, f"niels@{addr}"]
    proc = subprocess.Popen(cmd, stdin=theirs, stdout=theirs, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    theirs.close()
    ours.setblocking(False)
    return ours, proc


def stop(entry):
    sock, proc = entry["sock"], entry["proc"]
    try:
        sock.close()
    except OSError:
        pass
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass


def race(paths):
    sel = selectors.DefaultSelector()
    pending = list(paths)
    active = {}
    notes = []
    t0 = time.monotonic()
    next_start = t0
    while True:
        now = time.monotonic()
        if now - t0 > DEADLINE_S:
            for entry in list(active.values()):
                stop(entry)
                notes.append(f"{entry['name']}:deadline")
            return None, notes, now - t0
        if pending and (now >= next_start or not active):
            name, kind, addr, alias = pending.pop(0)
            try:
                sock, proc = start_tcp(addr) if kind == "tcp" else start_hop(addr, alias)
                limit = TCP_TIMEOUT_S if kind == "tcp" else HOP_TIMEOUT_S
                entry = {"name": name, "sock": sock, "proc": proc, "started": now, "limit": limit, "connected": kind != "tcp"}
                active[sock.fileno()] = entry
                sel.register(sock, selectors.EVENT_READ if entry["connected"] else selectors.EVENT_WRITE, entry)
            except OSError as exc:
                notes.append(f"{name}:{exc.strerror or exc}")
            next_start = time.monotonic() + STAGGER_S
            continue
        if not active and not pending:
            return None, notes, now - t0
        waits = [entry["started"] + entry["limit"] - now for entry in active.values()]
        if pending:
            waits.append(next_start - now)
        waits.append(t0 + DEADLINE_S - now)
        events = sel.select(max(0.0, min(waits)))
        failed = []
        for key, _mask in events:
            entry = key.data
            sock = entry["sock"]
            if not entry["connected"]:
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err:
                    failed.append((entry, os.strerror(err)))
                    continue
                entry["connected"] = True
                sel.modify(sock, selectors.EVENT_READ, entry)
                continue
            try:
                peek = sock.recv(8, socket.MSG_PEEK)
            except BlockingIOError:
                continue
            except OSError as exc:
                failed.append((entry, exc.strerror or str(exc)))
                continue
            if not peek:
                failed.append((entry, "closed-before-banner"))
                continue
            if not (peek.startswith(b"SSH-") or b"SSH-".startswith(peek)):
                # e.g. "Exceeded MaxStartups" — sshd answered but will not serve us
                failed.append((entry, "refused:" + peek.decode("ascii", "replace").strip()[:20]))
                continue
            sel.unregister(sock)
            active.pop(sock.fileno(), None)
            for other in list(active.values()):
                stop(other)
                notes.append(f"{other['name']}:lost-race")
            return entry, notes, time.monotonic() - t0
        now = time.monotonic()
        for entry in list(active.values()):
            if now - entry["started"] > entry["limit"]:
                failed.append((entry, "timeout"))
        for entry, why in failed:
            if entry["sock"].fileno() in active:
                try:
                    sel.unregister(entry["sock"])
                except (KeyError, ValueError):
                    pass
                active.pop(entry["sock"].fileno(), None)
                stop(entry)
                notes.append(f"{entry['name']}:{why}")
                next_start = time.monotonic()


def remember(name):
    try:
        tmp = f"{LAST}.{os.getpid()}"
        with open(tmp, "w") as handle:
            handle.write(name + "\n")
        os.replace(tmp, LAST)
    except OSError:
        pass


def relay(sock):
    sock.setblocking(True)
    sel = selectors.DefaultSelector()
    sel.register(0, selectors.EVENT_READ, "in")
    sel.register(sock, selectors.EVENT_READ, "net")
    open_in = True
    while True:
        for key, _mask in sel.select():
            if key.data == "in":
                data = os.read(0, 65536)
                if not data:
                    sel.unregister(0)
                    open_in = False
                    try:
                        sock.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                else:
                    sock.sendall(data)
            else:
                data = sock.recv(65536)
                if not data:
                    return
                view = memoryview(data)
                while view:
                    view = view[os.write(1, view):]
        if not open_in and not sel.get_map():
            return


def main():
    alias = sys.argv[1] if len(sys.argv) > 1 else "-"
    paths, last = ordered_paths()
    winner, notes, took = race(paths)
    detail = " ".join(notes)
    if winner is None:
        log(f"alias={alias} FAIL all-paths took={took:.2f}s first={last or '-'} {detail}")
        sys.stderr.write(f"ssh_s1_connect: every S1 path failed in {took:.1f}s: {detail}\n")
        return 255
    name = winner["name"]
    log(f"alias={alias} win={name} took={took:.2f}s first={last or '-'} {detail}".rstrip())
    if name != last and not os.environ.get("S1_CONNECT_ONLY"):
        remember(name)
    sock = winner["sock"]
    try:
        out = socket.socket(fileno=os.dup(1))
        is_unix = out.family == socket.AF_UNIX
    except OSError:
        is_unix = False
    if is_unix and os.environ.get("S1_CONNECT_RELAY") != "1":
        socket.send_fds(out, [b"\0"], [sock.fileno()])
        out.close()
        sock.close()
        return 0
    relay(sock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
