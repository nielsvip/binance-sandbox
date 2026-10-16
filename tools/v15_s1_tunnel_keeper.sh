#!/bin/bash
# V15 S1 TUNNEL KEEPER (USER 2026-10-15: "make sure all will keep running without interruptions").
#
# s1-int reaches S1 through the ControlMaster tunnel on 127.0.0.1:2201, bootstrapped once with `ssh -fNT s1-sftp`
# (CLAUDE.md INFRASTRUCTURE). Nothing re-established it: it was refused twice while building today's monitors
# ("connect to host 127.0.0.1 port 2201: Connection refused"), which silently breaks every Mac->S1 job —
# v15_golive_refresh_published, v15_throughput_guard, v15_round_regression_guard, v15_live_follows_boards_guard,
# v15_pipeline_health, v15_consistency_check, v15_scalar_parity_queue --push, v15_gain_pusher.
#
# Test, and only re-bootstrap when the test fails. An atomic mkdir lock keeps overlapping crons from stampeding
# (macOS has no flock). Falls back to reporting
# the public route's health (s1-pub, 157.180.125.52) so a failure here is visible rather than assumed.
set -u
ROOT=/Users/niels/Documents/binance
LOG=/tmp/v15_s1_tunnel.log
say() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"; }

# macOS has no flock(1) — `flock -n 9 || exit 0` made this script a silent no-op. mkdir is atomic everywhere;
# a lock older than 10 min is from a crashed run and is reclaimed, so a crash can never wedge the keeper.
LOCK=/tmp/v15_s1_tunnel.lock.d
if [ -d "$LOCK" ]; then
  if [ -z "$(find "$LOCK" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
    exit 0
  fi
  rmdir "$LOCK" 2>/dev/null || true
  say "reclaimed a stale lock"
fi
mkdir "$LOCK" 2>/dev/null || exit 0
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

if ssh -o BatchMode=yes -o ConnectTimeout=8 s1-int "true" 2>/dev/null; then
  exit 0                      # healthy: stay silent, this runs every 5 minutes
fi

# 2026-10-15: ssh_tunnel_keeper.py ALSO owns 127.0.0.1:2201 (it re-establishes with `ssh -fN -o ControlMaster=auto`
# within a couple of seconds). Bootstrapping on top of it logged "bind [127.0.0.1]:2201: Address already in use" and
# the pkill below briefly tore down a tunnel that was already coming back — this keeper was CAUSING short outages
# every 5 min. So: retest after a pause, and only take over when nothing holds the port and the python keeper is gone.
sleep 5
if ssh -o BatchMode=yes -o ConnectTimeout=8 s1-int "true" 2>/dev/null; then
  exit 0
fi
if pgrep -f 'ssh_tunnel_keeper.py' >/dev/null 2>&1 && lsof -nP -iTCP:2201 -sTCP:LISTEN >/dev/null 2>&1; then
  say "s1-int test failed but ssh_tunnel_keeper.py is running and 2201 is held — leaving the tunnel to its owner"
  exit 0
fi

say "s1-int DOWN — re-bootstrapping the tunnel (ssh -fNT s1-sftp)"
lsof -nP -iTCP:2201 -sTCP:LISTEN >/dev/null 2>&1 || pkill -f 'ssh -fNT s1-sftp' 2>/dev/null
ssh -fNT s1-sftp 2>>"$LOG"
sleep 3

if ssh -o BatchMode=yes -o ConnectTimeout=10 s1-int "true" 2>/dev/null; then
  say "s1-int RESTORED"
  exit 0
fi

# tunnel still down: is S1 itself reachable the public way? That distinguishes a dead tunnel from a dead host.
if ssh -o BatchMode=yes -o ConnectTimeout=10 s1-pub "true" 2>/dev/null; then
  say "ALERT s1-int still down but s1-pub answers — tunnel broken, host alive (jobs using s1-int will fail)"
  mkdir -p "$ROOT/data/reports"
  printf '{"at":"%s","s1_int":"down","s1_pub":"up","why":"tunnel broken, host alive"}\n' "$(date -u +%FT%TZ)" > "$ROOT/data/reports/ALERT_V15_S1_TUNNEL.txt"
else
  say "ALERT s1 unreachable on BOTH routes (s1-int and s1-pub)"
  mkdir -p "$ROOT/data/reports"
  printf '{"at":"%s","s1_int":"down","s1_pub":"down","why":"host unreachable on both routes"}\n' "$(date -u +%FT%TZ)" > "$ROOT/data/reports/ALERT_V15_S1_TUNNEL.txt"
fi
exit 1
