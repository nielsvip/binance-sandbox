# Param Workbook — `data/param_workbook.csv`

One spreadsheet covering **every config parameter on both trading systems** (crypto
`config.Config` = 2001 params + stocks `config_tradier.TradierConfig` = 1759 params =
**3760 rows**). It is BOTH the agent-readable assessment AND the control surface the
backtest screen runners read. Edit it → push edits into the manifests → next sweep uses them.

## How an agent reads it (assessment columns)

| column | meaning |
|---|---|
| `system` | `crypto` or `tradier` |
| `param` / `current_value` / `type` | the live config field + its current default |
| `sweep_tier` | `VEC_SCREEN` (read by fast Tier-1 vec) · `ENGINE_SCREEN` (real engine only) · `LIVE_ONLY` (no backtest path) · `DEAD` (no consumer) |
| `consumed_vec/tier2/live` | 1 = the param's token appears in that engine's source (ground-truth classification) |
| `test_status` | **TESTED** (run in history) · **UNTESTED** (sweepable, never run) · **LIVE_ONLY** (forward-test only) · **OBSOLETE** (dead knob) · **BLOCKED** (sweepable but no range) |
| `n_runs_obs` | how many historical backtest runs touched this param |
| `distinct_values_tested` | which values were actually run |
| `best_value_seen` / `best_mean_pool_sharpe` | best value by mean pooled Sharpe across history — **DIAGNOSTIC, not a promotion** |
| `priority_rank` | 0 = top churn/exit/entry/sizing lever; higher = lower leverage |
| `suggested_action` | `SCREEN_HIGH` · `SCREEN` · `RETEST_ADOPT` · `CONFIRMED_DEFAULT` · `FORWARD_TEST` · `OBSOLETE` · `BLOCKED` |
| `suggested_path` | the reasoning + the gain/mo + Sharpe next step |

### What the assessment says today
- **crypto**: 7 params ever tested, 327 untested-but-sweepable, 1063 live-only, 574 obsolete. Crypto is barely screened at the param level (218 real + 996 vec runs total).
- **tradier**: 23 tested, 242 untested-sweepable, 598 live-only, 864 obsolete (47.5k real + 251k vec runs — but concentrated on ~23 knobs).
- **`SCREEN_HIGH` (163 levers)** = untested churn/exit/entry/sizing knobs = the biggest honest path to gain/mo + Sharpe. Start here.
- **`RETEST_ADOPT` (13)** = history already disagrees with the live value (e.g. tradier `WT_DC_ENTRY_THRESHOLD` 45→55, `EXIT_SCORER_MIN_CONDITIONS` 5→2). Re-confirm Tier-2 at sample floor, then promote — never adopt on history alone.

## How you edit it (control columns)

Edit these 4 columns in the CSV (Excel / Numbers / any editor), then run the converter:

| column | effect |
|---|---|
| `sweep_enabled` | `Y` = include in the next OFAT sweep, `N` = skip |
| `test_values` | pipe-separated grid the sweep tries, e.g. `35|45|55|75` |
| `user_set_value` | pin the baseline to this value while sweeping other params |
| `notes` | free text; preserved across refreshes |

```bash
# 1) refresh assessment + test-history from the results DB (keeps your edits):
python3 derive_param_ranges.py --mode crypto  --db archive_from_s2/results_db.sqlite --out data/param_baseline_spec_crypto.json
python3 derive_param_ranges.py --mode tradier --db archive_from_s2/results_db.sqlite --out data/param_baseline_spec_tradier.json
python3 build_sweep_manifest.py --mode crypto
python3 build_sweep_manifest.py --mode tradier
python3 build_param_workbook.py            # --reset-edits to wipe manual edits

# 2) after editing the CSV, push edits into the manifests the runners read:
python3 workbook_to_manifest.py            # --dry-run to preview

# 3) the OFAT screen runners (on S1) then consume the updated manifest:
#    engine_ofat_screen.py --manifest data/param_sweep_manifest_<mode>.json   (Tier-2, faithful)
#    ofat_screen.py        --manifest data/param_sweep_manifest_<mode>.json   (vec, VEC_SCREEN only)
```

`workbook_to_manifest.py` writes `sweepable` + `test_values` back into
`data/param_sweep_manifest_<mode>.json`, and any `user_set_value` cells into
`data/param_user_overrides_<mode>.json` (a baseline override the runner can merge).

## No-lies guardrails baked in
- `best_mean_pool_sharpe` is the mean pooled Sharpe across historical runs — labelled diagnostic, never a promotion. Every adopt-suggestion says **"verify Tier-2 at sample floor first."**
- `DEAD` knobs are never marked sweepable (sweeping them = 0-effect lying rows, banned by CLAUDE.md).
- `LIVE_ONLY` knobs have no backtest path → flagged forward-test, not swept.
- Classification is by direct source-token presence (ground truth), not guesswork.

## Caveat (crypto classification)
`v8_quick_engine.py` and `wt_composite.py` are symlinked on S1 and absent on the Mac, so
crypto `ENGINE_SCREEN` may slightly undercount. Re-run `build_sweep_manifest.py --mode crypto`
on S1 for full-fidelity crypto tier classification.
