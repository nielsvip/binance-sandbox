#!/bin/bash
# par_suite_on_deploy.sh VENUE(stocks|crypto) — re-run the vector-vs-scalar per-path parity panel when the deployed engine md5 changes. Cron: */10 (flock). Output ~/par_out_<md5_8>/, marker ~/.par_last_md5_<venue>.
V=$1; cd ~/binance-sandbox || exit 1
M=$(md5sum v12_quick_engine.py | cut -c1-8)
[ "$(cat ~/.par_last_md5_$V 2>/dev/null)" = "$M" ] && exit 0
pgrep -f "[p]ar_path_probe" >/dev/null && exit 0
if [ "$V" = stocks ]; then L="AAPL_LONG AAPL_SHORT CRM_LONG CRM_SHORT GOOGL_LONG GOOGL_SHORT NVDA_LONG NVDA_SHORT"; else L="ZENUSDT_LONG ZENUSDT_SHORT BNBUSDC_LONG BNBUSDC_SHORT SOLUSDC_LONG SOLUSDC_SHORT ETHUSDC_LONG ETHUSDC_SHORT"; fi
echo "$M" > ~/.par_last_md5_$V
setsid nohup nice -n 12 .venv/bin/python tools/par_path_probe.py ~/par_out_$M $L > /tmp/par_suite_$V.log 2>&1 < /dev/null &
