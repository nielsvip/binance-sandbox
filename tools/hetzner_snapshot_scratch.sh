#!/bin/bash
# hetzner_snapshot_scratch.sh — USER 2026-10-22: "once all the data from the sql's has been absorbed, before condensing/compressing,
# make a snapshot on hetzner of everything so far so it is still available if future rounds and neural network upgrades want to
# look at it again". Takes a Hetzner server snapshot (hcloud create-image --type snapshot) of each named server, waits for it,
# and books it in data/reports/hetzner_snapshots.jsonl (+ a pointer line in SELF_LEARNING_DATA.md). Never deletes anything.
# Usage: tools/hetzner_snapshot_scratch.sh <tag> <server-name> [<server-name> ...]
#   tag = short reason, e.g. v20-absorb ; description becomes "<tag>-<UTC>-<server>"
# Requires HCLOUD_TOKEN in the environment (the Mac's ~/.zshrc exports it) and the hcloud CLI (~/.local/bin/hcloud).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:?tag}"; shift
[ $# -ge 1 ] || { echo "no servers named" >&2; exit 2; }
OUT="$ROOT/data/reports/hetzner_snapshots.jsonl"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
HC="${HCLOUD_BIN:-$(command -v hcloud || echo "$HOME/.local/bin/hcloud")}"
rc=0
for SRV in "$@"; do
  DESC="${TAG}-${TS}-${SRV}"
  USED="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$SRV" 'df -B1 --output=used / | tail -1' 2>/dev/null | tr -d ' ')"
  echo "[snap] $SRV -> $DESC (used bytes on /: ${USED:-unknown})"
  if ! "$HC" server create-image --type snapshot --description "$DESC" "$SRV" >"/tmp/hcloud_snap_${SRV}_${TS}.log" 2>&1; then
    echo "[snap] FAILED $SRV: $(tail -1 "/tmp/hcloud_snap_${SRV}_${TS}.log")" >&2
    printf '{"ts":"%s","server":"%s","description":"%s","status":"FAILED","log":"%s"}\n' "$TS" "$SRV" "$DESC" "/tmp/hcloud_snap_${SRV}_${TS}.log" >> "$OUT"
    rc=1
    continue
  fi
  IMG="$("$HC" image list -t snapshot -o noheader -o columns=id,description 2>/dev/null | awk -v d="$DESC" '$2==d {print $1}' | head -1)"
  SIZE="$("$HC" image describe "$IMG" -o json 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("image_size"))' 2>/dev/null)"
  printf '{"ts":"%s","server":"%s","description":"%s","image_id":"%s","image_size_gb":"%s","used_bytes":"%s","status":"OK"}\n' "$TS" "$SRV" "$DESC" "${IMG:-?}" "${SIZE:-?}" "${USED:-?}" >> "$OUT"
  echo "[snap] OK $SRV image=${IMG:-?} size_gb=${SIZE:-?}"
done
echo "- $(date -u +%F) Hetzner snapshots (${TAG}): $(for s in "$@"; do printf '%s ' "$s"; done)— ids in data/reports/hetzner_snapshots.jsonl (USER 2026-10-22: snapshot before condensing; keep until a later upgrade no longer needs the raw corpus)" >> "$ROOT/SELF_LEARNING_DATA.md"
exit $rc
