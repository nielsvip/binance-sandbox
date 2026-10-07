#!/bin/bash
# mirror the R365 row-level 365D/30D evidence from s5 to the Mac (run every ~20 min by the monitor): data/newx365/
mkdir -p /Users/niels/Documents/binance/data/newx365 /Users/niels/Documents/binance/data/wiring/r365
rsync -az --timeout=60 s5:~/v15_row365_20261001/ /Users/niels/Documents/binance/data/newx365/ 2>&1 | tail -1
for h in s1-pub s2; do mkdir -p /Users/niels/Documents/binance/data/newx365/stocks365_$h; rsync -az --timeout=60 $h:~/v15_row365_20261001/progress_stocks365/ /Users/niels/Documents/binance/data/newx365/stocks365_$h/ 2>&1 | tail -1; done
mkdir -p /Users/niels/Documents/binance/data/newx365/stocks365_s5 /Users/niels/Documents/binance/data/newx365/stocks365_s5_npzbad; rsync -az --timeout=60 s5:~/v15_row365_20261001/progress_stocks365/ /Users/niels/Documents/binance/data/newx365/stocks365_s5/ 2>&1 | tail -1; rsync -az --timeout=60 s5:~/v15_row365_20261001/progress_stocks365_npzbad/ /Users/niels/Documents/binance/data/newx365/stocks365_s5_npzbad/ 2>&1 | tail -1
rsync -az --timeout=30 s5:~/r365_status.md /Users/niels/Documents/binance/data/wiring/r365/STATUS_s5.md 2>&1 | tail -1
rsync -az --timeout=30 s5:~/r365_out/ /Users/niels/Documents/binance/data/wiring/r365/crypto365/ 2>/dev/null
rsync -az --timeout=30 s5:~/r365_out_stocks365/ /Users/niels/Documents/binance/data/wiring/r365/stocks365/ 2>/dev/null
rsync -az --timeout=30 s5:~/r365_out_stocks30/ /Users/niels/Documents/binance/data/wiring/r365/stocks30/ 2>/dev/null
tail -2 /Users/niels/Documents/binance/data/wiring/r365/STATUS_s5.md | cut -c1-220
