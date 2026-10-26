#!/bin/bash
set -u
BASE=/Users/niels/Documents/binance
LOCK=/tmp/.mac_persym_small_push_s1.lock
/usr/bin/shlock -f "$LOCK" -p $$ || exit 0
trap 'rm -f "$LOCK"' EXIT INT TERM
SSH='ssh -S none -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new'
HOST=
for candidate in s1-pub s1-int; do
  if $SSH "$candidate" true 2>/dev/null; then HOST="$candidate"; break; fi
done
if [ -z "$HOST" ]; then
  echo "$(date -u +%FT%TZ) S1 unreachable for small settings push"
  exit 1
fi
files=()
for file in data/per_sym_settings.json data/persym_size_tiers.json data/cat_side_defaults_4.json \
            tradeable_keys.json managed_stops.json data/confirmed_365d.json data/excluded_tokenized_stocks.json; do
  [ -f "$BASE/$file" ] && files+=("$BASE/./$file")
done
if [ "${#files[@]}" -gt 0 ]; then
  if rsync -az --relative --timeout=30 -e "$SSH" "${files[@]}" "$HOST:~/binance/"; then
    echo "$(date -u +%FT%TZ) small settings pushed to $HOST: ${#files[@]} files"
  else
    echo "$(date -u +%FT%TZ) SMALL PUSH FAILED to $HOST"
    exit 1
  fi
fi
