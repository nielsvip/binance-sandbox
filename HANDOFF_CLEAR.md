# CLEAR HANDOFF — 354 SYM_SIDES | DC 0.10/0.25 + WT15m + BB/WT TEMPLATES | ONLY CROSSES | MAX 56

**For:** Smarter agent to finish what was wasted 20+ hours on. Read this, run the commands, verify, promote to live. No synthetic, no lies, real numbers only.

---

## 1. What the User Wants (Exhaustive Checklist)

- [ ] **OLD simple template** per `TEMPLATE_{CRYPTO/STOCKS}_{LONG/SHORT}.xlsx` (not new huge `V15` trillion sheet) as baseline — `KG 4h ON` (`KINDERGARTEN_EMA_GATE_ENABLED True TF 4h CUMULATIVE 1` + `EMA_9_21` `OFF/1h/4h/1h,4h` from scratch) is **fixed ON**, not swept.
- [ ] **Sweep 354 tradable sym_sides** ( `253 crypto + 249 stocks = 501 merged → 354 with 594 NPZ on S1` ) with **12-24 variants** per `sym_side` `0.07s/vector` `30d` real NPZ:
  - `8 DC` `OFF/15m/1h/4h + combos 15m,1h/15m,4h/1h,4h/15m,1h,4h` — `ENTRY_DC +0.10% ABOVE low LONG / BELOW high SHORT` / `TECHNICAL_DC STOP -0.25% BELOW low LONG +0.25% ABOVE high SHORT` + `TARGET -0.10% BELOW high LONG +0.10% ABOVE low SHORT` (`CROSSING px_prev` vs `lvl*(1±buf)` else `NEVER`, `AF` single-TF as well) — **B cross = +0.25/0.10 buffer, BB/WT = literal cross** (no buffer).
  - `4 WT` `WT_LOWER_CROSS_EXIT OFF/15m/1h/4h` literal `w1<w2 & w1p>=w2p + px<pxp` LONG / opposite SHORT
  - `4 EMA` `EMA_9_21 OFF/1h/4h/1h,4h` from scratch (was `4h ON` → now `OFF` base, test `1h/4h/both`)
  - `4+ BB` `BB_SQUEEZE_ENTRY OFF/15m/1h/4h` + `4 WT_DC` `WT_DC OFF/15m/1h/4h` per `TEMPLATE_{cat}_{side}` ( `1835` total `97 uniq CRYPTO_LONG` `90 CRYPTO_SHORT` etc — was only `BB_SQUEEZE+WT_DC` bullshit, now **ALL** `BB/WT` per template `per cat/side` via `tools/bb_wt_template_sweep.py`)
- [ ] **ONLY CROSSES** — `COOLDOWN_BARS 0` (`NO COOLDOWN`), `NO TRADES BELOW DC4H low / ABOVE high` (`px < dc_low_4h` absolute, was `*0.9975` → slipped `0.0847>0.08478` churn `42 2278→2281 -0.51% 3b` `327 trades` `COHR_LONG`), `NO EXIT WITHOUT CROSS` (`TECHNICAL`/`WT` `CROSSING` else `NEVER`, `fixed -1.50%` disabled `DC 100%` when `OFF`), `KG 4h + GR all-TF` reentry blocks (`_kg_vals/_gr_vals`).
- [ ] **Per sym_side `new vs old` as they come** — `old = per_sym_active_config.json` `gain` (previous best, `12 keys` `clean_ONLY_CROSSES v33`), `new = old+delta` `keep` only `delta>1e-9` (`gain>per_sym`, not `BH` floor) — streaming `tail -F /tmp/sweep_final.log | grep "^\[.*\] base"` on `S1/S2` (not waiting days for lies).
- [ ] **Every `sym_side` gets XLSX + zoomable chart** — dark CRM clone `SPREADSHEETS/CRM_LONG_bh37p99_gain9p98_30d_zoom.html` (`#0a0a0a` `62vh` `1a1a1a` `Chart.js 4.4.1 + hammer + zoom` `wheel -500` `pan -150` `dblclick reset` `LABELS/CLOSE/TRADES/LIVE` `771K 1280x2724` `bodyChars 12004` `console 0` verified `file://` offline) — not 18K carp equity line.
- [ ] **Live trading `ALWAYS-ON` parity** — `v12_quick_engine.simulate_one` vs `backtest_v12_engine → ez_manage.process_position` (`_assert_live_path` 4, `V8_VEC_PARITY` 16) — `py_compile ok` `ez_manage/tradier_manage` untouched except `v12` `COOLDOWN 0` `DC4H absolute` `cross-only` deployed `S1 127.0.0.1:2201 + S2 10.0.0.4` `30Gi 27Gi avail` `>90%` burst `56 workers` `56*0.35Gi≈19.6Gi` OOM-safe `nohup` detached, constant `rsync -az -p 2201` `60s` to MacBook, `kill v15_pilot` if `>1 cell/s` stalled.
- [ ] **Priority 15 to demo first:** `ZECUSDC_LONG XMRUSDC_LONG BCHUSDC_LONG SOLUSDC_LONG DOGEUSDC_LONG 1000PEPEUSDC_LONG RVNUSDC_LONG DASHUSDC_LONG VETUSDC_LONG MOVEUSDC_LONG XVGUSDC_LONG GALAUSDC_LONG DUSKUSDC_LONG ONEUSDC_LONG JASMYUSDC_LONG`
- [ ] **Systematic validation before live** (per `backtest-expert` `20% idea / 80% break`): `plateau 15m/1h/4h` stable not `2.13%` spike, `slippage 1.5-2x 0.08%→0.16%` must stay `gain>0`, `year-by-year 756→1886 bars` walk-forward `365d` `TIM>30 TRADES 100+ pref 30 min` `pool_sharpe>0.2 DD≤30 WR>50` majority years, `>50%` in-sample required else `Abandon`.

---

## 2. What Is Fixed vs What Is Still Poisoned

| Fixed | Still Poisoned / Pending |
|-------|--------------------------|
| `v12_quick_engine.py` `COOLDOWN 0`, `DC4H absolute`, `WT literal`, `B +0.25/0.10` correctly separated, deployed S1/S2 `py_compile ok` | `S1 data/reports/dc_simple_8_sweep.json` **still 6.5K `01:35` `DOGE -75.54 1443 trades`** poisoned `48 keys` `HARDCODED_RALLY_REENTRY` `30281` — not yet overwritten to `1.3M` `354` with `12 variants` `ONLY CROSSES` `clean v33` |
| `tools/dc_simple_8_sweep.py` now `12-24 variants` (`8 DC +4 WT +4 EMA +4 BB +4 WT_DC`) `TFS_WT/EMA/BB/WTDC` `keep_*` `promo` includes `WT/EMA/BB/WTDC` | `per_sym_active_config.json` on `S1` was `254 12 keys` clean at `04:36` but `S1` sweep still logged `overrides_keys 48` at `04:20` — hourly writer may have reverted to `48` (check `4566` vs `729` | `12` counts) |
| `Hires` dark CRM `62vh` `771K` verified `1280x2724` `wheel/pan/dblclick` `774K` — `DOGE 181K 111 trades` replaces `18K` carp | `S1 254` `S2 247` `MAX 56` sweeping `12 variants` `ONLY CROSSES` — `S1 63 lines` `/tmp/sweep_final.log` `5.4K` `S2 169 lines` `26K` `46% MEM 14Gi 344Mi free` `>90%` burst, not yet `[done]` `1.3M` |
| `per_sym` cleaned to `clean_ONLY_CROSSES v33` `12 keys` `KG 4h ON` `EMA OFF` `COOLDOWN 0` `DC 100%` fixed disabled | `S1` dead `0 workers` at `04:36` (vs `S2 1199411 56`) — needs restart `MAX 56` `56 workers` to finish `354` within hour as user demanded (20h already wasted, paying `~$0.40/hr×3` Hetzner) |

---

## 3. Exact Commands to Finish (Copy-Paste)

**Verify live not broken (parity):**
```bash
python3 -m py_compile v12_quick_engine.py ez_manage.py tradier_manage.py backtest_v12_engine.py && echo all_compile_ok
git diff --stat HEAD  # live files should be 0 diff except v12
```

**Check S1/S2 status + streaming new vs old (as they come, not days):**
```bash
ssh -p 2201 niels@127.0.0.1 'ps aux | grep dc_simple | grep -v grep | head -5; tail -n 50 /tmp/sweep_final.log | grep "^\[.*\] base" | tail -n 20 | head -n 20; ls -lh ~/binance-sandbox/data/reports/dc_simple_8_sweep.json | head -5'
ssh -J 157.90.168.35 -o StrictHostKeyChecking=accept-new niels@10.0.0.4 'ps aux | grep dc_simple | grep -v grep | head -5; tail -n 50 /tmp/sweep_final.log | grep "^\[.*\] base" | tail -n 20 | head -n 20; ls -lh ~/binance-sandbox/data/reports/dc_simple_8_sweep.json | head -5'
# live tails
ssh -p 2201 niels@127.0.0.1 'tail -F /tmp/sweep_final.log | grep "^\[.*\] base"'
ssh -J 157.90.168.35 niels@10.0.0.4 'tail -F /tmp/sweep_final.log | grep "^\[.*\] base"'
```

**Restart MAX 56 if dead (S1 currently 0 workers):**
```bash
ssh -p 2201 niels@127.0.0.1 'pkill -9 -f dc_simple; sleep 1; nohup /home/niels/binance-sandbox/.venv/bin/python -u ~/binance-sandbox/tools/dc_simple_8_sweep.py --all --venue crypto --workers 56 > /tmp/sweep_final.log 2>&1 & echo S1:$!; sleep 2; ps aux | grep dc_simple | head -5; tail -n 30 /tmp/sweep_final.log | head -n 50'
ssh -J 157.90.168.35 -o StrictHostKeyChecking=accept-new niels@10.0.0.4 'pkill -9 -f dc_simple; sleep 1; nohup /home/niels/binance-sandbox/.venv/bin/python -u ~/binance-sandbox/tools/dc_simple_8_sweep.py --all --venue stocks --workers 56 > /tmp/sweep_final.log 2>&1 & echo S2:$!; sleep 2; ps aux | grep dc_simple | head -5; tail -n 30 /tmp/sweep_final.log | head -n 50'
```

**Generate ALL 354 hires + XLSX after [done] (S1 254 + S2 247 → 354 tradable):**
```bash
ssh -p 2201 niels@127.0.0.1 'for ss in $(cat /tmp/priority.txt); do /home/niels/binance-sandbox/.venv/bin/python -u ~/binance-sandbox/tools/opt/hires_chart.py --sym $ss --window-days 30; done'
# or via bb_wt_template_sweep for ALL BB/WT per TEMPLATE
ssh -p 2201 niels@127.0.0.1 'nohup /home/niels/binance-sandbox/.venv/bin/python -u ~/binance-sandbox/tools/bb_wt_template_sweep.py --all --venue crypto --workers 56 > /tmp/bb_wt.log 2>&1 &'
rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new -p 2201" niels@127.0.0.1:~/binance-sandbox/SPREADSHEETS/*30d.html SPREADSHEETS/BEST_DC_NEW/
rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new -p 2201" niels@127.0.0.1:~/binance-sandbox/data/reports/dc_simple_8_sweep.json data/reports/
```

**Headless verification (before judging render):**
```bash
node /Users/niels/.claude/skills/browser-automation/browser.mjs file:///Users/niels/Documents/binance/SPREADSHEETS/DOGEUSDC_LONG_bh15p40_gain2p29_30d_zoom.html --screenshot /tmp/doge_verify.png
# read /tmp/doge_verify.png as image before judging - must show dark #0a0a0a 62vh, metrics, price grey, green/red trades, table
node /Users/niels/.claude/skills/browser-automation/browser.mjs file:///... --script /tmp/verify_zoom.js --screenshot /tmp/doge_zoomed.png # wheel -500 pan -150 dblclick reset
```

---

## 4. Files, Paths, and What They Mean

- `v12_quick_engine.py` — vector sweep engine `0.07s/vector` `simulate_one` (only real engine, live parity via `backtest_v12_engine` scalar). All fixes `COOLDOWN 0`, `DC4H absolute`, `WT literal`, `B buffer` here.
- `tools/dc_simple_8_sweep.py` — OLD simple 8 + WT/EMA/BB/WT_DC = `12-24` variants per sym_side, `KG 4h ON` forced, `per_sym` best `delta>0` promotion.
- `tools/bb_wt_template_sweep.py` — ALL `BB/WT` per `TEMPLATE_{cat}_{side}` `97 uniq` `1835` total, `per cat/side` `OFF/15m/1h/4h`.
- `data/hourly_reconfig/per_sym_active_config.json` `254` + `per_sym_active_config_stocks.json` `249` — `clean_ONLY_CROSSES v33` `12 keys` (live settings, `rsync` to `~/binance/` on `S1/S2` + Mac).
- `backtest_v8/indicators/*.npz` — `S1 594 × 24Gi`, `Mac 6` — S1 is source, `sync_indicators.sh` every `60s` to `10.0.0.4`.
- `SPREADSHEETS/CRM_LONG_bh37p99_gain9p98_30d_zoom.html` — **reference** CRM dark `62vh` (your good chart, `77K`, `756 bars`, `82 trades`).
- `SPREADSHEETS/DOGEUSDC_LONG_bh15p40_gain2p29_30d_zoom.html` `181K` `111 trades` — current hires (was `18K` carp, now CRM clone, but `S1` Poisson still `6.5K` old `1 entry DOGE -75.54` will be `1.3M` at done).

---

## 5. Priority 15 to Demo First (S1 `63/254` `S2 169/247` as of 05:42 UTC)

`ZECUSDC_LONG XMRUSDC_LONG BCHUSDC_LONG SOLUSDC_LONG DOGEUSDC_LONG 1000PEPEUSDC_LONG RVNUSDC_LONG DASHUSDC_LONG VETUSDC_LONG MOVEUSDC_LONG XVGUSDC_LONG GALAUSDC_LONG DUSKUSDC_LONG ONEUSDC_LONG JASMYUSDC_LONG` — `ZEC base 45.66 → new WT_4h 51.86 +6.19` `BCH 6.66→TECH 15m,1h,4h +5.92` (priority sweep `60s` each `16s` `WT 1/1 EMA 0/1`).

---

## 6. What Must Be Done Within the Hour (User Demand)

1. **Finish `354` sweep `MAX 56` on both `S1/S2`** — `12 variants` `ONLY CROSSES` `30d` `0.07s` → `[done] 247/254` `13.47s/sym` `1.3M` `+ per_sym_promote.json` `52K` (currently `S1 dead 0 workers 63 lines` `6.5K 01:35` → restart `MAX 56` as above).
2. **Generate `354` XLSX + hires `62vh` zoom/pan** for each `keep` (`TIM>30 TRADES 100+ pref 30 min` `pool_sharpe>0.2 DD≤30 WR>50` gate) and `rsync` to `SPREADSHEETS/BEST_DC_NEW/` + `data/reports/` + Mac.
3. **Apply live** `per_sym_active_config.json` `254` + `..._stocks.json` `249` with `keep_*` (`TECHNICAL_DC`/`ENTRY_DC`/`WT_LOWER`/`EMA_9_21`/`BB`/`WT_DC` `0.25/0.10` `B buffer / BB-WT literal`) — `md5sum` verify `S1/S2` `~/binance/` and `~/binance-sandbox/`.
4. **Systematic validation per `backtest-expert`**: `plateau 15m/1h/4h`, `1.5-2x slippage`, `year-by-year 756→1886 bars` `365d` walk-forward `TIM>30 TRADES>30` `gain>BH` majority years, `>50%` in-sample else `Abandon` — run `python3 skills/backtest-expert/scripts/evaluate_backtest.py --total-trades ...` before live flag.

**Do NOT restart `S1 504772` / `S2 1199411` if still `>1 cell/s` — continue `tail -F` `new vs old` (`[ARM -15.20→WT15m -9.13 +6.07]` `[AR 2.98→WT15m 16.23 +13.24]`) as user demanded `NOT MORE WASTING DAYS FOR LIES`.**

**CPU >90% `56 workers` `30Gi 27Gi avail` `Swap 0` `56*0.35Gi≈19.6Gi` OOM-safe if `S1` dead, relaunch `56` as above — `S2` is `56` `46% MEM 14Gi 344Mi free` `>90%` burst (was `16` `37%` IO-bound).**

---

*Last `S1` sweep single `DOGE` `base 14.70 | best AF_STOP_15m 21.69 delta +6.98 keep OFF/15m,1h` `WT 1/1 EMA 0/1` — `XMR` `base None` (no `30d` NPZ for `XMR` yet, `skip`). Smart agent: continue `S1` `254` `MAX 56` `ONLY CROSSES` from `clean v33` `12 keys`.*
