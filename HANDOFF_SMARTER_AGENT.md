# HANDOFF FOR SMARTER AGENT — 354 SYM_SIDES DC/WT/EMA/BB/WT_DC ONLY CROSSES

**Goal:** 354 sym_sides (crypto + stocks tradable with 594 NPZ) with ALL test results in XLSX and zoomable chart, per_sym live promotion. Prior agent lost 20h with poisoned per_sym, fixed.

## Current State (2026-09-27 05:50 UTC)

### Per-Sym Config (clean ONLY CROSSES v33)
- `data/hourly_reconfig/per_sym_active_config.json` 254 crypto + `per_sym_active_config_stocks.json` 249 stocks = 354 tradable (501 merged, 354 with NPZ) — `clean_ONLY_CROSSES v33` 12 keys:
  ```
  KINDERGARTEN_EMA_GATE_ENABLED True TF 4h CUMULATIVE 1, EMA_9_21 OFF (swept 1h/4h/1h,4h), TECHNICAL_DC OFF, DAYTRADE_DC OFF, ENTRY_DC OFF (swept), WT_LOWER_CROSS OFF (swept), COOLDOWN_BARS 0, DC_DAYTRADE_ENABLED True, TRADIER stop/target 100% (no fixed %)
  ```
- Previous poisoned: 48 keys `-75.54 1443 trades` DOGE (now fixed to `DOGE base 14.70 120 trades` with only crosses). `*.bak_poisoned_before_clean` saved.

### Engine Fixes Deployed (v12_quick_engine.py — 56 workers MAX)
- `v12_quick_engine.py:4551 COOLDOWN_BARS 0` (NO COOLDOWN), `HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN True` kept
- `v12:22218/22253 px < dc_low_4h` absolute (was `*0.9975` → slipped `0.0847>0.08478` churn `327 trades` COHR)
- `TECHNICAL_EXIT` cross-only `STOP -0.25% BELOW low LONG +0.25% ABOVE high SHORT` (`px <= dc_low*0.9975`), `TARGET -0.10% BELOW high LONG +0.10% ABOVE low SHORT` (`CROSSING px_prev` else `NEVER`), `WT_LOWER_CROSS_EXIT OFF/15m/1h/4h` literal `w1<w2 & w1p>=w2p + px<pxp` (BB/WT literal, B +0.10/0.25)
- `KINDERGARTEN_EMA_GATE 4h ON` + `GR all-TF` reentry block in place
- Deployed `S1 127.0.0.1:2201 ~/binance-sandbox/v12_quick_engine.py` + `S2 10.0.0.4` via `rsync` + `.venv`, `py_compile ok`

### Sweep Tool (dc_simple_8_sweep.py — 12 variants + BB/WT_DC)
- `TFS_EXIT = OFF/15m/1h/4h + combos 15m,1h/15m,4h/1h,4h/15m,1h,4h` (8 EXIT TECHNICAL_DC), `TFS_ENTRY` same 8 (ENTRY_DC +0.10%), `TFS_WT OFF/15m/1h/4h` (WT lower cross), `EMA OFF/1h/4h/1h,4h`, plus `BB_SQUEEZE_ENTRY` `WT_DC` per `TEMPLATE_{CRYPTO/STOCKS}_{LONG/SHORT}` (1835 BB/WT switches, 97 uniq CRYPTO_LONG etc) — total 12-24 variants per sym_side `0.07s/vector` `30d _exact_30d_slice` real `594 NPZ` `S1 30Gi 27Gi avail`.
- Also `tools/bb_wt_template_sweep.py` enumerates ALL BB/WT per template `97 uniq` per cat/side for full BB/WT sweep.
- Promotion: `keep` only `delta>1e-9` (`gain>per_sym` previous best) → `per_sym_active_config.json` (`TECHNICAL_DC`/`ENTRY_DC`/`WT_LOWER`/`EMA_9_21` `0.25/0.10`) + `hires` dark CRM `62vh`.

### Charts (CRM clone)
- Reference: `SPREADSHEETS/CRM_LONG_bh37p99_gain9p98_30d_zoom.html` (`#0a0a0a` `62vh` `Chart.js 4.4.1 + hammer + zoom` wheel/pan/dblclick, price grey, entries green circles, exits squares/diamonds, `LABELS/CLOSE/TRADES/LIVE`)
- Generated: `SPREADSHEETS/DOGEUSDC_LONG_bh15p40_gain2p29_30d_zoom.html` `181K 111 trades` (was 18K carp), `ZEC/SAND/SOL/BCH/1000PEPE` hires `138-661K` in `/tmp/*hires.html` on S1 via `tools/opt/hires_chart.generate_hires` — `771K 1280x2724` `bodyChars 12004` `console 0` `wheel -500` `pan -150` `dblclick reset` `774K` verified via `browser.mjs` `file://` offline.

### Live Streaming Progress
- Both `S1 crypto 254 MAX 56 PID 504772` + `S2 stocks 247 MAX 56 PID 1199411` `30Gi 27Gi avail Swap 0` `56*0.35Gi≈19.6Gi` detached `nohup` — tail: `ssh -p 2201 niels@127.0.0.1 'tail -F /tmp/sweep_final.log | grep "^\[.*\] base"'` / `ssh -J 157.90.168.35 niels@10.0.0.4 'tail -F /tmp/sweep_final.log | grep "^\[.*\] base"'`
- Current snapshot (as they stream):
  ```
  [ARMUSDT_SHORT] old -15.20 bh -13.01 tr 4 | new WT_15m -9.13 delta +6.07 keep 1h
  [AR_LONG] old 2.98 bh 5.91 tr 2 | new WT_15m 16.23 delta +13.24 keep 1h
  [DOGEUSDC_LONG] old 14.70 bh 15.40 tr 120 | new AF_STOP_15m 21.69 delta +6.98 keep 15m,1h
  [COHR_LONG] old -36.06 327 trades → new WT_15m -30.84 +5.22 (still <BH, not promoted)
  ```
- `data/reports/dc_simple_8_sweep.json` (6.5K old `01:35` `-75` poisoned) will be overwritten to `1.3M` at `[done] 247/254 in 3327s 13.47s/sym` `+ per_sym_promote.json` — `rsync -az -p 2201` to Mac `SPREADSHEETS/BEST_DC_NEW/` and live `per_sym_active_config*.json` within hour.

## Pending for Smarter Agent

1. **Finish 354 sweep:** Wait for `S1 254` + `S2 247` `MAX 56` `ONLY CROSSES` `12 variants` (DC 8 + WT 4 + EMA 4 + BB 4 + WT_DC 4) to `[done]` — check `ps aux | grep dc_simple`, `wc -l /tmp/sweep_final.log`, `ls -lh data/reports/dc_simple_8_sweep.json` (expect 1.3M, not 6.5K). Kill `v15_pilot` if `>1 cell/s` stalled.

2. **Systematic validation per backtest-expert (80% time):** Before live promotion, run for each `keep`:
   - Plateau `15m/1h/4h` stable, not `2.13%` spike
   - Slippage `1.5-2x` `0.08%→0.16%` must stay `gain>0`
   - Year-by-year `756→1886 bars` walk-forward `TIM>30 TRADES>30` `pool_sharpe>0.2 DD≤30 WR>50` majority years, `1y` (`365d`) via `run_one(sym, per_sym, 365)` — see `/tmp/priority_1y.py` template
   - Sample `>100 trades` preferred, `>30` min

3. **Generate ALL 354 hires + XLSX:**
   ```bash
   ssh -p 2201 niels@127.0.0.1 'for ss in $(cat /tmp/priority.txt); do /home/niels/binance-sandbox/.venv/bin/python -u ~/binance-sandbox/tools/opt/hires_chart.py --sym $ss --window-days 30; done'
   # or via bb_wt_template_sweep with 56 workers
   rsync -az -e "ssh -S none -p 2201" niels@127.0.0.1:~/binance-sandbox/SPREADSHEETS/*30d.html SPREADSHEETS/BEST_DC_NEW/
   rsync -az -e "ssh -S none -p 2201" niels@127.0.0.1:~/binance-sandbox/data/reports/dc_simple_8_sweep*.json data/reports/
   ```
   Verify hires via `node /Users/niels/.claude/skills/browser-automation/browser.mjs file:///SPREADSHEETS/... --screenshot /tmp/verify.png` and read back image before judging.

4. **Live promotion:** `per_sym_active_config.json` `254` + `..._stocks.json` `249` with `keep_*` (`TECHNICAL_DC_STOP_TF` `ENTRY_DC` `WT_LOWER` `EMA_9_21` `0.25/0.10`) — `rsync` to `~/binance/data/hourly_reconfig/` on `S1/S2` and Mac, `md5sum` verify, real tests start when `MAX 56` live.

5. **Priority 15 to demo first:** `ZECUSDC_LONG XMRUSDC_LONG BCHUSDC_LONG SOLUSDC_LONG DOGEUSDC_LONG 1000PEPEUSDC_LONG RVNUSDC_LONG DASHUSDC_LONG VETUSDC_LONG MOVEUSDC_LONG XVGUSDC_LONG GALAUSDC_LONG DUSKUSDC_LONG ONEUSDC_LONG JASMYUSDC_LONG` — `S1 done 63/254` `S2 169/247` as of `05:50 UTC`.

## Commands
```bash
ssh -p 2201 niels@127.0.0.1 'ps aux | grep dc_simple; tail -n 50 /tmp/sweep_final.log | grep "^\[.*\] base"'
ssh -J 157.90.168.35 niels@10.0.0.4 'ps aux | grep dc_simple; tail -n 50 /tmp/sweep_final.log | grep "^\[.*\] base"'
# wait then
ssh -p 2201 niels@127.0.0.1 'cat ~/binance-sandbox/data/reports/dc_simple_8_sweep.json | python3 -c "import json; d=json.load(open(\"/home/niels/binance-sandbox/data/reports/dc_simple_8_sweep.json\")); print(len(d), sum(1 for x in d if x.get(\"best_overall\",{}).get(\"delta\",0)>0.01))"'
```

**Do not restart — continue S1 504772 / S2 1199411, keep CPU >90% 56 workers 30Gi 27Gi avail OOM-safe, constant 60s rsync to Mac.**
