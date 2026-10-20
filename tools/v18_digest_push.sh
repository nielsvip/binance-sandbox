#!/bin/bash
# v18_digest_push.sh — md5-verified push of the four V18 digests (data/learn/digest_<CAT_SIDE>.json) to the hosts the pilot
# runs on: s1-int and s2, into ~/binance-sandbox/data/learn/ (doc 17 §3.1: hosts never read the store, only digests).
# Only the digests are pushed (they are the ONLY thing the pilot reads). Exit 1 if any md5 differs after the copy.
# usage: bash tools/v18_digest_push.sh [--dry-run] [--hosts "s1-int s2"]
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOSTS="s1-int s2"
DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --hosts) HOSTS="$2"; shift ;;
    esac
    shift
done
FILES=()
for cs in CRYPTO_LONG CRYPTO_SHORT STOCKS_LONG STOCKS_SHORT; do
    f="$ROOT/data/learn/digest_${cs}.json"
    if [ ! -s "$f" ]; then
        echo "[push] MISSING $f — run tools/v18_digest.py --apply first" >&2
        exit 1
    fi
    sz=$(wc -c < "$f")
    if [ "$sz" -gt 10485760 ]; then
        echo "[push] REFUSED $f is $sz bytes (> 10 MB contract)" >&2
        exit 1
    fi
    FILES+=("$f")
done
fail=0
for h in $HOSTS; do
    if [ "$DRY" = 1 ]; then
        echo "[push] dry-run: would rsync ${#FILES[@]} digests to $h:binance-sandbox/data/learn/"
        continue
    fi
    ssh -o ConnectTimeout=15 "$h" 'mkdir -p ~/binance-sandbox/data/learn' || { echo "[push] $h unreachable" >&2; fail=1; continue; }
    rsync -a --timeout=120 "${FILES[@]}" "$h:binance-sandbox/data/learn/" || { echo "[push] rsync to $h failed" >&2; fail=1; continue; }
    remote=$(ssh -o ConnectTimeout=15 "$h" 'cd ~/binance-sandbox/data/learn && md5sum digest_CRYPTO_LONG.json digest_CRYPTO_SHORT.json digest_STOCKS_LONG.json digest_STOCKS_SHORT.json')
    for f in "${FILES[@]}"; do
        b=$(basename "$f")
        if command -v md5 >/dev/null 2>&1; then l=$(md5 -q "$f"); else l=$(md5sum "$f" | cut -d' ' -f1); fi
        r=$(echo "$remote" | awk -v n="$b" '$2==n {print $1}')
        if [ "$l" = "$r" ]; then
            echo "[push] $h $b md5 $l OK"
        else
            echo "[push] $h $b md5 MISMATCH local=$l remote=$r" >&2
            fail=1
        fi
    done
done
exit $fail
