"""Exactly one vector trader per venue, and never zero for long.

USER 2026-10-31 (verbatim): "ONLY s1 can trade crypto while macbook is limited to paper until fixed - but it needs to
GUARANT$EE trading 24/7 wiht s2 backup NEVER 2 traders atthe same time. SAME for s5 and stocks."

Two requirements that pull against each other: 24/7 coverage wants a backup that takes over eagerly, while "never two
traders" wants nobody to trade when in doubt. A single symmetric rule cannot serve both, so the two roles are
deliberately ASYMMETRIC:

  PRIMARY  (s1 crypto / s5 stocks) trades unless it POSITIVELY SEES the backup holding the lease. Losing sight of the
           peer never stops it — that is what keeps the venue covered when the mesh or the peer is the thing that broke.
  BACKUP   (s2 crypto / s6 stocks) trades only when it POSITIVELY ESTABLISHES that the primary is not trading: the
           primary's beat must be stale, and a WITNESS host must agree that it cannot see a fresh primary beat either.
           Doubt, including "I cannot reach anything", means the backup stays down.

The witness is what stops split brain. A backup whose own network dropped sees a stale primary beat for a local reason;
without a second opinion it would take over while the primary is still happily trading. So the backup asks another host
(the gateway, the Mac, a sibling worker) to read the primary's beat on its behalf, and only a witness that ALSO reports
the primary stale authorizes a takeover.

Handover back is hysteretic and primary-led: once the backup is active the primary stands down (it sees a fresh backup
lease), and the backup releases only after the primary's beat has been continuously fresh for RELEASE_AFTER_S. That
ordering means the gap between them is always a stand-down, never an overlap.

Every path is raced per CLAUDE.md: a peer beat is read over all of that peer's addresses at once and the freshest answer
wins. A beat is a small JSON file each trader rewrites every pass: {host, pid, epoch, role, active, venue}.
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

BEAT_STALE_S = float(os.environ.get("VD_LEASE_BEAT_STALE_S", "120"))
TAKEOVER_AFTER_S = float(os.environ.get("VD_LEASE_TAKEOVER_AFTER_S", "180"))
RELEASE_AFTER_S = float(os.environ.get("VD_LEASE_RELEASE_AFTER_S", "300"))
PEER_READ_EVERY_S = float(os.environ.get("VD_LEASE_PEER_READ_EVERY_S", "20"))
SSH = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new"]
# venue -> role -> (addresses to race). The primary and backup of a venue never share a host.
HOSTS = {
    "crypto": {"primary": ("s1-int", "s1-pub", "niels@10.0.0.3"), "backup": ("s2", "niels@10.0.0.4")},
    "stocks": {"primary": ("s5", "niels@10.0.0.5"), "backup": ("s6", "niels@10.0.0.6")},
}
# A witness only has to be a host that is not the two contenders; it reads the primary's beat on the backup's behalf.
WITNESSES = {
    "crypto": ("niels@10.0.0.6", "niels@10.0.0.5", "gateway-internal"),
    "stocks": ("niels@10.0.0.3", "niels@10.0.0.4", "gateway-internal"),
}
REMOTE_BEAT = "binance/data/vector_direct/beat_{venue}.json"


def stamp(epoch=None):
    return dt.datetime.fromtimestamp(epoch if epoch is not None else time.time(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def beat_path(out: Path, venue: str) -> Path:
    return Path(out) / f"beat_{venue}.json"


def write_beat(out: Path, venue: str, role: str, active: bool, why: str = "") -> dict:
    doc = {"host": socket.gethostname(), "pid": os.getpid(), "epoch": time.time(), "written_at": stamp(),
           "role": role, "active": bool(active), "venue": venue, "why": why[:200]}
    path = beat_path(out, venue)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=str(path.parent), prefix=f".{path.name}.", delete=False) as fh:
        json.dump(doc, fh)
        tmp = Path(fh.name)
    os.replace(tmp, path)
    return doc


def _ssh_json(address: str, command: str, timeout: float = 14.0):
    try:
        r = subprocess.run(SSH + [address, command], capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"{address}: {type(exc).__name__}"
    if r.returncode != 0:
        return None, f"{address}: rc={r.returncode} {r.stderr.strip()[:80]}"
    try:
        return json.loads(r.stdout), None
    except ValueError:
        return None, f"{address}: unparsable answer"


def read_peer(venue: str, role: str):
    """Freshest beat of the venue's `role`, raced over all of its addresses. (doc|None, [errors])"""
    addresses = HOSTS.get(venue, {}).get(role, ())
    if not addresses:
        return None, [f"no addresses for {venue}/{role}"]
    command = f"cat {REMOTE_BEAT.format(venue=venue)} 2>/dev/null"
    best, errors = None, []
    with cf.ThreadPoolExecutor(max_workers=len(addresses)) as pool:
        for doc, error in pool.map(lambda a: _ssh_json(a, command), addresses):
            if error:
                errors.append(error)
            elif isinstance(doc, dict) and float(doc.get("epoch") or 0) > float((best or {}).get("epoch") or 0):
                best = doc
    return best, errors


def witness_says_primary_stale(venue: str, now: float):
    """(verdict, detail). True only when a witness read the primary's beat and found it stale, or found none at all."""
    command = f"cat {REMOTE_BEAT.format(venue=venue)} 2>/dev/null"
    for witness in WITNESSES.get(venue, ()):
        for address in HOSTS.get(venue, {}).get("primary", ()):
            # the witness reaches the primary itself; a plain `cat` on the witness would read the witness's own file
            doc, error = _ssh_json(witness, f"ssh -o BatchMode=yes -o ConnectTimeout=6 {address} '{command}'", 22.0)
            if error:
                continue
            if not isinstance(doc, dict):
                return True, f"{witness} found no primary beat via {address}"
            age = now - float(doc.get("epoch") or 0)
            if age > TAKEOVER_AFTER_S:
                return True, f"{witness} via {address}: primary beat {age:.0f}s old"
            return False, f"{witness} via {address}: primary beat {age:.0f}s old — primary is alive"
        # that witness could not reach the primary on any address at all: it agrees the primary is unreachable
        doc, error = _ssh_json(witness, "echo '{\"ok\":1}'", 14.0)
        if not error:
            return True, f"{witness} is reachable but cannot reach the primary on any address"
    return False, "no witness could be consulted — a backup must not take over on its own word"


def decide(venue: str, role: str, out: Path, now=None, state=None, peer_reader=read_peer, witness=witness_says_primary_stale):
    """(may_trade, why, state). `state` carries the hysteresis between passes; pass it back in unchanged."""
    now = now if now is not None else time.time()
    state = dict(state or {})
    if role not in ("primary", "backup"):
        raise ValueError(f"role must be primary or backup, got {role!r}")
    cached = state.get("peer")
    if cached is None or now - float(state.get("peer_read_at") or 0) > PEER_READ_EVERY_S:
        peer, errors = peer_reader(venue, "backup" if role == "primary" else "primary")
        state["peer"], state["peer_errors"], state["peer_read_at"] = peer, errors, now
    peer = state.get("peer")
    peer_age = None if not peer else now - float(peer.get("epoch") or 0)

    if role == "primary":
        # Stand down ONLY on positive evidence that the backup is live and active. Anything else keeps the venue covered.
        if peer and peer.get("active") and peer_age is not None and peer_age <= BEAT_STALE_S:
            state["active"] = False
            return False, f"backup {peer.get('host')} holds the lease (beat {peer_age:.0f}s old) — standing down", state
        state["active"] = True
        if peer is None:
            return True, "primary trading; backup not visible (no handover possible, venue stays covered)", state
        return True, f"primary trading; backup idle (beat {('%.0fs old' % peer_age) if peer_age is not None else 'absent'})", state

    # backup
    if peer and peer_age is not None and peer_age <= TAKEOVER_AFTER_S:
        if state.get("active"):
            healthy_since = state.get("primary_healthy_since") or now
            state["primary_healthy_since"] = healthy_since
            if now - healthy_since >= RELEASE_AFTER_S:
                state["active"] = False
                state.pop("primary_healthy_since", None)
                return False, f"primary {peer.get('host')} healthy {now - healthy_since:.0f}s — releasing the lease back", state
            state["active"] = True
            return True, f"backup still active; primary fresh for {now - healthy_since:.0f}s of {RELEASE_AFTER_S:.0f}s before release", state
        state["active"] = False
        return False, f"primary alive (beat {peer_age:.0f}s old) — backup idle", state
    state.pop("primary_healthy_since", None)
    if state.get("active"):
        state["active"] = True
        return True, "backup active; primary still not beating", state
    verdict, detail = witness(venue, now)
    if not verdict:
        state["active"] = False
        return False, f"primary beat {'absent' if peer_age is None else '%.0fs old' % peer_age} but NOT taking over: {detail}", state
    state["active"] = True
    return True, f"TAKEOVER: primary beat {'absent' if peer_age is None else '%.0fs old' % peer_age} and {detail}", state
