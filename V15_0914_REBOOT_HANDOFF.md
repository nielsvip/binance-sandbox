**UPDATE 2026-09-14 20:09 UTC: S1/S3/S5 ENOSPC + S2 missing file + psutil fixed — 4-template x shuffle/no-shuffle WF cycle in progress, huge sweep must FINISH TODAY**

- **Host health 20:09:** `S1 8 pilots 43% us 15% sy 42% idle (84% disk 49G avail, V15_V16_CELL_BY_CELL 3.6M after find -delete)`, `S3 4 pilots 21% us 9% sy 70% idle (22% disk 226G avail)`, `S5 4 pilots 20% us 11% sy 69% idle (21% disk 228G avail)`, `S2 4 pilots 3% us 97% busy on 10.0.0.4` — **S3/S5 psutil 7.2.2 now installed via .venv/bin/pip (was ModuleNotFoundError psutil)**, **S2 v15_pilot_0914.py rsynced (was missing)**, **V15 clones 29G→3.6M cleaned via find -type f -delete (was 3674112 files, ENOSPC BadZipFile mismatched tag)**
- **Valid baselines after fix:** `S1 AAPL 5 overrides +3364 defaults → 917 arrays 2322 bars hot vec valid=True gain 0.6331 trades 112 sharpe 0.0099 → E2 0.6331 bh 6.4464` (was mismatched tag on clone), `S3 AAPL same 5 overrides valid=True 112 trades`, `S5 BTC 69 overrides valid=True 29 trades` (was psutil missing) — all templates load ok `['CATEGORY_STOCKS_LONG','ORDERING_0914_REV2','LEGEND_FILTERS']`
- **4-template x shuffle/no-shuffle with WF worst-first cycle:** `WF=STDEV_SLOPE_SIZING (-4.49) → ENTRY_REVERSAL_BOUNCE (-3.76) → ENTRY_BREAKOUT (-3.54) → ENTRY_CONFIRMATION (-3.48) → EXIT_STRUCTURAL (-3.43) → AUGMENT_TREND (-3.26) → EXIT_VELOCITY (-2.77) → REENTRY_ADAPTIVE (-2.76) → REENTRY_WINDOWED (-2.70) → AUGMENT_RISK (-2.44) → REDUCE_SIGNAL (-2.36) → GLOBAL (-2.30) → REDUCE_PROFIT (-2.30)` + within-sheet `AUGMENT -12.95 first → WT True +0.15 last` — **cycle-on-neg deque round_robin 3012/3012 cycles424** — testing `TEMPLATE_0914_STOCKS_LONG.xlsx (19 syms)`, `CRYPTO_LONG (7)`, `STOCKS_SHORT (13)`, `CRYPTO_SHORT (3)` each with `TEMPLATE_0914_SHUFFLE.xlsx` (random seed 42 shuffled) vs ordered WF, `S1 AAPL SHUFFLE2 cum 11.6897 delta +11.06 31k lines hustler 3019 switches` vs `WF2 cum 7.3440` (but `E-BLAND delta 0.0000 != vg 9.1155` still), **marginal +2.47 vs shipped -4.02 must be used**
- **Huge sweep:** after defining winners per backtest-expert (sample ≥30, slippage 1.5-2×, plateaus not peaks), **resume 30-50 sym_sides/server (S1/STOCKS_LONG, S3/CRYPTO_LONG, S5/STOCKS_SHORT+CRYPTO_SHORT, S2 continues v15_pilot.py)** with `365D` confirmation — **must FINISH TODAY per user**

**UPDATE 2026-09-14 17:55 UTC: 7D killed — 30D only + 365D confirmation of final settings (per user, 7D is trash)**

# V15 0914 Reboot Handoff — Worst-First + 4 Category Lean Templates

**Created:** 2026-09-14 17:45 UTC (Mac survived near-crash) — this file is source of truth to resume after reboot.
**Goal:** Prove `worst-first sheet/switch + cycle-on-NEG` beats `sequential` on `pool_sharpe`/`VECTOR_DELTA` before overwriting live `v15`/`TEMPLATE`.

---

## 1. What exists — do NOT overwrite live until proven

| File | Size | Location | Purpose |
|---|---|---|---|
| `TEMPLATE.xlsx` | 819K | `SPREADSHEETS/TEMPLATE.xlsx` (Mac) / `S1 SPREADSHEETS/TEMPLATE.xlsx` | LIVE — keep until REV2/CAT proves lift |
| `TEMPLATE_0914.xlsx` REV2 worst-first | 861K | `SPREADSHEETS/TEMPLATE_0914.xlsx` / `S1 SPREADSHEETS/TEMPLATE_0914.xlsx` | Global worst-first (`STDEV -4.49 → REDUCE_PROFIT -2.30`) + within-sheet `AUGMENT -12.95 first → WT True +0.15 last` — `ORDERING_0914_REV2` sheet |
| `TEMPLATE_0914_REV2_WORSTFIRST.xlsx` | 861K | same + `SPREADSHEETS/TEMPLATE_0914_REV2_WORSTFIRST.xlsx` | Backup of REV2 |
| `TEMPLATE_0914_CRYPTO_LONG.xlsx` | 884K | `SPREADSHEETS/TEMPLATE_0914_CRYPTO_LONG.xlsx` + `S1/S3/S5 SPREADSHEETS/` | `7 syms` `CATEGORY_CRYPTO_LONG` `keep 1 WT False +2.42 / skip 5 AUGMENT -15.9` `keep 5 filters NEWBORN_PROTECT +0.88 / skip 5 FROZEN_STOP -2.19` |
| `TEMPLATE_0914_CRYPTO_SHORT.xlsx` | 884K | same + `S1/S3/S5` | `3 syms` small sample → keep global order, `keep WT True +9.59 / skip STDEV 0.5 -13.7` |
| `TEMPLATE_0914_STOCKS_LONG.xlsx` | 884K | same + `S1/S3/S5` | `19 syms` **category-specific** `EXIT_STRUCTURAL -0.33 → ENTRY_REVERSAL_BOUNCE +0.23` `keep WT True +6.06 / skip AUGMENT 1.25 -14.6` |
| `TEMPLATE_0914_STOCKS_SHORT.xlsx` | 884K | same + `S1/S3/S5` | `13 syms` category-specific `ENTRY_CONFIRMATION -1.47 → ENTRY_REVERSAL_BOUNCE -0.54` `keep BB_SQUEEZE +1.34 / skip AUGMENT 0.5 -11.0` |
| `V15_AVG_DELTAS.xlsx` | 520K 16 sheets | `SPREADSHEETS/V15_AVG_DELTAS.xlsx` + `S1 SPREADSHEETS/V15_V16_CELL_BY_CELL/V15_AVG_DELTAS.xlsx` + `http://localhost:5077/spreadsheets/V15_AVG_DELTAS.xlsx` | Source of truth 17:31 UTC rebuild `42 syms done≥2800/3043` |
| `v15_pilot_0914.py` | 2935L 182K | `v15_pilot_0914.py` + `S1 /home/niels/binance-sandbox/v15_pilot_0914.py` | `--seq-mode {sequential,cycle} --cycle-on-neg --sheet-order CSV --template` `worst2best` + `cycle schedule 3012/3012 cycles424` |
| `tests/test_v15_0914_sequencing.py` | 2.4K | `tests/test_v15_0914_sequencing.py` | `5 passed` guards flags |

**Do NOT `cp` REV2/CAT → `TEMPLATE.xlsx` until `s1/s3/s5` `30D → 365D confirmation` OOS proves `pool_sharpe` lift.**

---

## 2. V15_AVG_DELTAS 16-sheet — what it says (marginal vs naked fix)

- **Sheets:** `SUMMARY, AVG_SWITCHES 3005, DEFAULT_APPLIED 56, AVG_FILTERS 241, VALIDATION 55, AVG_SWITCH_FILTER_COMBO 1001, YELLOW_SYSTEM_ANALYSIS, CATEGORY_RECOMMENDATIONS 89, SWITCHES_CRYPTO_LONG 101, SWITCHES_CRYPTO_SHORT 101, SWITCHES_STOCKS_LONG 101, SWITCHES_STOCKS_SHORT 101, FILTERS_CRYPTO_LONG 101, FILTERS_CRYPTO_SHORT 101, FILTERS_STOCKS_LONG 101, FILTERS_STOCKS_SHORT 101`
- **Why avg -2.91 but final +10:** Greedy `E`-chain `F=variant_gain - cumulative_before (col G)`, `E_next=E_prev+F iff F>0`, `final=baseline + SUM(F>0)`. `AAPL baseline 0.63 → 5/3012 F>0 sum +8.55 → final 9.18 (+2.73 vs bh)`. `avg_all -2.91 pos137 (0.11%) avg_pos +3.15`. New `marginal_avg = yellow - naked_per_row` restores: `GR_FILTER_VEC 15m shipped -4.02 → marginal +2.47 48%`, `GOLDEN_RULE_ENFORCE 15m -2.30 → +2.30 63%`, `NOLOSS_BYPASS 15m -2.25 → +2.25 70%`. Use `marginal_*`, not `avg_combined`.
- **Defaults 54 that make others look bad:** `FILTER_DICTIONARY_V2 col17 IsDefault=YES` (112 `filter=value` `YES`, usually `15m`). Default already in `cumulative_before` (`+2.3`), testing same `15m` vs cum `~0`, testing `D/4h/OFF → -0.3…-1.6` all negative. `DEFAULT_APPLIED` gold: `GR_FILTER_VEC +2.47, GOLDEN_RULE +2.30, NOLOSS +2.25` — keep defaults.
- **Verdicts:** `ABSOLUTELY KEEP 15 marginal≥1.0 mpos≥30% n≥100`, `OCCASIONALLY KEEP 104 marginal>0 mpos 10-30%`, `USE OTHER SETTINGS 96` (15m best), `DISCARD 25 marginal<0 mpos<5% n≥50`, `REWRITE 0`.
- **Yellow:** `125,377 rows 739,644 yellows avg 5.9/row median2 max50 93,373 combos only 13,630 (14.6%) n≥10` → KEEP per-switch, REDUCE per-base variants `240→~90` (best TF `15m`), EXTEND top 10 `GR/GOLDEN_RULE/NOLOSS`.

---

## 3. Worst-first philosophy — global + per-category

| Scope | Order | Source |
|---|---|---|
| **Global (42 syms, robust)** | `STDEV -4.49 → ENTRY_REVERSAL_BOUNCE -3.76 → ENTRY_BREAKOUT -3.54 → ENTRY_CONFIRMATION -3.48 → EXIT_STRUCTURAL -3.43 → AUGMENT_TREND -3.26 → EXIT_VELOCITY -2.77 → REENTRY_ADAPTIVE -2.76 → REENTRY_WINDOWED -2.70 → AUGMENT_RISK -2.44 → REDUCE_SIGNAL -2.36 → GLOBAL -2.30 → REDUCE_PROFIT -2.30` | `AVG_SWITCHES` `3005` |
| **STOCKS_LONG (19)** | `EXIT_STRUCTURAL -0.33 → ENTRY_CONFIRMATION -0.33 → REENTRY_ADAPTIVE -0.32 → REENTRY_WINDOWED -0.31 → EXIT_VELOCITY -0.28 → ENTRY_BREAKOUT -0.27 → ENTRY_REVERSAL_BOUNCE +0.23` | `SWITCHES_STOCKS_LONG` |
| **STOCKS_SHORT (13)** | `ENTRY_CONFIRMATION -1.47 → EXIT_STRUCTURAL -1.47 → REENTRY_WINDOWED -1.47 → AUGMENT_TREND -1.47 → … → ENTRY_REVERSAL_BOUNCE -0.54` | `SWITCHES_STOCKS_SHORT` |
| **CRYPTO_LONG (7) / SHORT (3)** | Keep **global** (small `n=7/3` noisy, per-cat `0.0` many) | `SWITCHES_CRYPTO_*` small sample |

**Within-sheet:** `AUGMENT_FALLBACK 0.5 -12.95 first → WT True +0.15 last` (global) and per-cat `WT True +6.06` last for `STOCKS_LONG` etc. — applied in REV2/CAT.

**Cycle on NEG:** `deque per sheet cur%len` `round_robin 3012/3012 cycles424` `first10=[ENTRY_BOUNCE False, STDEV True, DC OFF…]` in `v15_pilot_0914.py:1179` — clears `13` worst per tab in `13` steps vs `200` sequential losers. Worst-first `cum` stays low so late good gainers stay `POS` and don't shadow smaller `+0.5`.

---

## 4. Filters — what to keep, what to cut (when in doubt keep)

| Keep (marginal>0.5, correctly `True/15m` in defaults) | Skip (marginal<-1 & yellow_pos<1%, `0/500+` wins) |
|---|---|
| `GOLDEN_RULE_ENFORCE 15m +2.30 63% n1875 IsDefault YES` | `COOLDOWN_LOCKS D -11.95 n1098 pos0` |
| `GR_FILTER_VEC 15m +2.47 48% n2773 YES` | `COOLDOWN_LOCKS 1h -11.78 n773` |
| `NOLOSS_BYPASS 15m +2.25 70% n4035 YES` | `BANDAID 0.5 -11.23 n1154` |
| `BT_WT_CROSS 15m +1.78 30% n4488 YES` | `BAND_ARROW True -11.21 n1154` |
| `MTF_ARMED 15m +1.54 50% n1303 YES` | `CRYPTO_SPIKE_FADE 12.5 -11.11 n773` |
| `BB_RECOVERY 15m +0.77 28% n148 YES` | `FROZEN_STOP 1h -9.09 n2817` |

All `105` `pos0 n>500` worst `-9` to `-11.95` are correctly `False/OFF` in `config.py` (`RED_ZONE_GATE False, VP_GATE False` etc.) — freeze `OFF`, exclude from `84` combos. Keep `10` best at `OFF vs 15m` only. With `300+ syms` refine `96 USE OTHER SETTINGS` → best `15m` variant only (`240→90` yellows `~60%` saving).

---

## 5. Backtest plan (backtest-expert: break, not cherry-pick)

**Hypothesis:** `worst-first sheet + within-sheet worst→best + cycle on NEG` clears `0.11%` positives faster than sequential `ENTRY_BOUNCE→EXIT` and survives `15m` marginal edge vs naked.

**Codified:** `TEMPLATE_0914_{CAT}.xlsx` + `v15_pilot_0914.py --seq-mode cycle --cycle-on-neg --sheet-order <worst-first> --template TEMPLATE_0914_{CAT}.xlsx` — zero discretion.

**Stress (80% of time):** param `±50%` on `WT_15M_BOUNCE/BB_SQUEEZE`, window `30D → 365D confirmation/60d`, friction `1.5-2×` slippage, year `>200 trades`, seek plateaus not `2.13%` spikes.

**Walk-forward OOS:**
1. Train `30d` on `TEMPLATE_0914_{CAT}.xlsx` (worst-first) → validate `30D` holdout same cat
2. `s1/s3/s5` (`S1 157.180.125.52 / s1-int 127.0.0.1:2201`, `S3 htz-v15-s3 10.0.0.5 / 178.104.79.9`, `S5 htz-v15-s5 10.0.0.6 / 178.104.77.34` — all `~3:49 up`, `S1` `6` pilots `BNBUSDC/BTCUSDC/ETHUSDC/SOLUSDC 30d`, `S3`/`S5` idle) — run `AAPL_LONG/NVDA_LONG (stocks_long), BTCUSDC_LONG (crypto_long), AMAT_SHORT/BABA_SHORT (stocks_short), BTCUSDC_SHORT (crypto_short)`
3. Compare `pool_sharpe` + `VECTOR_DELTA` (`F`) + `marginal_avg vs naked` vs `REV2` sequential + `filterless` + `best-first` control (should lose). Require `worst-first + cycle > sequential` on all `4` cats and `marginal_pos` stable.

**Commands to resume after reboot:**
```bash
# Sync templates if reboot cleared ControlMaster
rsync -avz -e "ssh -o StrictHostKeyChecking=no" SPREADSHEETS/TEMPLATE_0914_*.xlsx s1-int:/home/niels/binance-sandbox/SPREADSHEETS/
rsync -avz -e "ssh -o StrictHostKeyChecking=no" SPREADSHEETS/TEMPLATE_0914_*.xlsx s3:/home/niels/binance-sandbox/SPREADSHEETS/
rsync -avz -e "ssh -o StrictHostKeyChecking=no" SPREADSHEETS/TEMPLATE_0914_*.xlsx s5:/home/niels/binance-sandbox/SPREADSHEETS/
rsync -avz -e "ssh -o StrictHostKeyChecking=no" v15_pilot_0914.py s1-int:/home/niels/binance-sandbox/; rsync -avz -e "ssh -o StrictHostKeyChecking=no" v15_pilot_0914.py s3:/home/niels/binance-sandbox/; rsync -avz -e "ssh -o StrictHostKeyChecking=no" v15_pilot_0914.py s5:/home/niels/binance-sandbox/

# S1 STOCKS_LONG worst-first cycle (AAPL)
ssh s1-int ".venv/bin/python -u v15_pilot_0914.py --sym-side AAPL_LONG --window-days 30 --vector-only --seq-mode cycle --cycle-on-neg --sheet-order STDEV_SLOPE_SIZING,ENTRY_REVERSAL_BOUNCE,ENTRY_BREAKOUT_CHANNEL,ENTRY_CONFIRMATION_GATES,EXIT_STRUCTURAL,AUGMENT_TREND,EXIT_VELOCITY,REENTRY_ADAPTIVE,REENTRY_WINDOWED,AUGMENT_RISK_SIZING,REDUCE_SIGNAL_RATER,GLOBAL_RISK_GATES,REDUCE_PROFIT_LOCK --template SPREADSHEETS/TEMPLATE_0914_STOCKS_LONG.xlsx 2>&1 | tee /tmp/AAPL_WF_30d.log"

# S3 CRYPTO_LONG
ssh s3 ".venv/bin/python -u v15_pilot_0914.py --sym-side BTCUSDC_LONG --window-days 30 --vector-only --seq-mode cycle --cycle-on-neg --template SPREADSHEETS/TEMPLATE_0914_CRYPTO_LONG.xlsx 2>&1 | tee /tmp/BTC_WF_30d.log"

# S5 STOCKS_SHORT + CRYPTO_SHORT
ssh s5 ".venv/bin/python -u v15_pilot_0914.py --sym-side AMAT_SHORT --window-days 30 --vector-only --seq-mode cycle --cycle-on-neg --template SPREADSHEETS/TEMPLATE_0914_STOCKS_SHORT.xlsx 2>&1 | tee /tmp/AMAT_WF_30d.log"
ssh s5 ".venv/bin/python -u v15_pilot_0914.py --sym-side BTCUSDC_SHORT --window-days 30 --vector-only --seq-mode cycle --cycle-on-neg --template SPREADSHEETS/TEMPLATE_0914_CRYPTO_SHORT.xlsx 2>&1 | tee /tmp/BTC_SHORT_WF_30d.log"

# Controls (should lose): best-first vs sequential original
# Best-first = reverse order REDUCE_PROFIT first...
# Sequential original = SPREADSHEETS/TEMPLATE.xlsx

# Collect
ssh s1-int 'python3 -c "import json,pathlib; p=pathlib.Path(\"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/AAPL_LONG_30d_progress.json\"); d=json.load(open(p)); print(d.get(\"cumulative_gain\"))"'
```

**Verdict:** `TEMPLATE_0914_{CAT}.xlsx` stays `S1/S3/S5` until `300+` syms deepens — then make better `USE OTHER SETTINGS → 15m` lean `~90` filters. Keep `4` cats separate.

---

## 6. Files to re-check after reboot

- `SPREADSHEETS/V15_AVG_DELTAS.xlsx` `520K` `16 sheets` + `http://localhost:5077/spreadsheets/V15_AVG_DELTAS.xlsx`
- `SPREADSHEETS/TEMPLATE_0914*.xlsx` `5× 861-884K` + `S1/S3/S5 SPREADSHEETS/TEMPLATE_0914*.xlsx`
- `v15_pilot_0914.py` `2935L` `--help` should show `--seq-mode {sequential,cycle,worst2best}` `--cycle-on-neg` `--sheet-order`
- `tests/test_v15_0914_sequencing.py` `5 passed` — `pytest tests/test_v15_0914_sequencing.py -v`
- `data/reports/lifecycle_pilot/*_progress.json` — `cumulative_gain`, `done` `len`, `baseline_gain`
- `SPREADSHEETS/V15_V16_CELL_BY_CELL/*_30d_matrix*.xlsx` — `pool_sharpe` vs `bh`

**SSH:** `s1-int (127.0.0.1:2201 via s1-sftp)`, `s3 (10.0.0.5 via gateway-internal)`, `s5 (10.0.0.6)` — `~/.ssh/config` `ControlMaster auto`. `INFRASTRUCTURE.md` + `BACKTEST_BIBLE.md` canonical.

