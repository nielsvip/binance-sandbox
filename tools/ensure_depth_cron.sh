#!/bin/bash
LOG="$HOME/logs/ensure_klines_depth.log"
mkdir -p "$HOME/logs"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] cron check" >> "$LOG"
# Ensure gateway ez_klines running
if ! ssh -o ConnectTimeout=8 gateway-internal "ps aux | grep -q '[e]z_klines.py'" 2>&1 | grep -q .; then
  echo "gateway ez_klines check" >> "$LOG"
fi
ssh -o ConnectTimeout=8 gateway-internal "ps aux | grep -q '[e]z_klines.py' || (nohup /home/niels/.conda/envs/binance_env/bin/python -u /home/niels/binance/ez_klines.py > /home/niels/logs/ez_klines.log 2>&1 & echo restarted)" >> "$LOG" 2>&1
# Check depth simple (no heredoc)
GW_15M=$(ssh -o ConnectTimeout=8 gateway-internal "python3 -c 'import json, glob; print(sum(1 for f in glob.glob(\"/home/niels/binance/klines_cache/*_15m.json\") if len(json.load(open(f)))>=1200))'" 2>&1)
echo "GW 15m >=1200: $GW_15M" >> "$LOG"
GW_ALL=$(ssh -o ConnectTimeout=8 gateway-internal "ls /home/niels/binance/klines_cache/AAOIUSDT_15m.json 2>&1 | head -n 1" 2>&1)
echo "AAOI: $GW_ALL" >> "$LOG"
# Sync symbols if drift
MAC_LEN=$(python3 -c 'import json; print(len(json.load(open("/Users/niels/Documents/binance/symbols.json"))))')
GW_LEN=$(ssh -o ConnectTimeout=8 gateway-internal "python3 -c 'import json; print(len(json.load(open(\"/home/niels/binance/symbols.json\"))))'" 2>&1)
if [ "$GW_LEN" != "$MAC_LEN" ]; then
  echo "symbols drift GW:$GW_LEN MAC:$MAC_LEN pushing" >> "$LOG"
  cat /Users/niels/Documents/binance/symbols.json | ssh -o ConnectTimeout=8 gateway-internal "cat > /tmp/symbols.json.new && mv /tmp/symbols.json.new /home/niels/binance/symbols.json"
  cat /Users/niels/Documents/binance/symbols_tradier.json | ssh -o ConnectTimeout=8 gateway-internal "cat > /tmp/symbols_tradier.json.new && mv /tmp/symbols_tradier.json.new /home/niels/binance/symbols_tradier.json"
  cat /Users/niels/Documents/binance/symbols.json | ssh -o ConnectTimeout=8 s1-int "cat > /tmp/symbols.json.new && mv /tmp/symbols.json.new /home/niels/binance/symbols.json"
  cat /Users/niels/Documents/binance/symbols_tradier.json | ssh -o ConnectTimeout=8 s1-int "cat > /tmp/symbols_tradier.json.new && mv /tmp/symbols_tradier.json.new /home/niels/binance/symbols_tradier.json"
fi
