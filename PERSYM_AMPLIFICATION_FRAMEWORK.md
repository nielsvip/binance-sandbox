# Per-Symbol Daily Amplification Framework (DPSA)

_Last updated: 2026-06-06. Source of truth for all per_sym daily optimization._

---

## 1. Architecture — Two Layers

Every account runs two layers, outer to inner:

| Layer | What | Config file read | Updated |
|-------|------|-----------------|---------|
| **4yr Baseline** | Long-term per-symbol parameter sweep | `per_sym_active_config.json` | When a multi-year sweep winner beats baseline by Δwsharpe ≥ 0.1 AND wsharpe ≥ 0.7 AND trades ≥ 30 |
| **7D Overlay** | Recent-window recalibration (trc + fin only) | `{account}/active_config.json` | Daily, before market open |

All other accounts (ang, inf, flz, men, trb) use the 4yr baseline only. Negative-sharpe keys are disabled at load time by `_inject_neg_sharpe_no_trade`.

---

## 2. Daily Schedule — Hard Deadlines

| UTC | ET | Action | Target |
|-----|----|--------|--------|
| 11:00 | 07:00 | `tradier_hourly_reconfig.py --accounts trb` (S1 cron) | `trb/active_config.json` |
| 11:20 | 07:20 | `tradier_hourly_reconfig.py --accounts trc` (S1 cron) | `trc/active_config.json` |
| 11:45 | 07:45 | `dpsa_bridge.py --tradier trc` — merge 20D knob winners into trc | `trc/active_config.json` |
| 12:00 | 08:00 | **CRYPTO DEADLINE** — all per_sym configs in place for crypto | `per_sym_active_config.json` |
| 12:30 | 08:30 | `dpsa_bridge.py --crypto fin` — merge 7D winners for fin (when wired) | `per_sym_active_config.json` |
| 13:00 | 09:00 | **STOCKS DEADLINE** — tradier market open, all configs must be live | — |
| 20:00 | 16:00 | Market close | — |
| 21:00 | 17:00 | `dpsa_feedback.py` — compare per_sym predictions vs actual /history/ | `data/dpsa_feedback/` |
| 00:05 | 20:05 | `watchdog_persym_s1.sh` (continuous, every 20 min) | builds 7D/20D candidates |

---

## 3. Per-Symbol Coverage — All Symbols, Both Sides

Per_sym optimization must cover **every key in tradeable_keys** for each account, split by LONG and SHORT:

- Stocks (`trc`): all symbols in `symbols_tradier.json` with BOTH `{SYM}_LONG` and `{SYM}_SHORT` entries
- Crypto (`per_sym_active_config.json`): all 50+ symbols in `symbols.json`, both sides

Each symbol/side that has at least 30 trades in the 4yr baseline must have an entry. Keys not covered fall through to global config defaults.

**Neg-sharpe gate** (already coded, `_inject_neg_sharpe_no_trade`):
- If ANY source has positive wsharpe → keep enabled
- If ALL sources are negative (or zero) → `LONG_ENABLED=False` / `SHORT_ENABLED=False`
- This is enforced at load time in both ez_positions_quick and tradier_manage

---

## 4. Optimization Goal — 4× Baseline Per Symbol

| Metric | Floor for promotion |
|--------|-------------------|
| `pool_sharpe` per symbol | ≥ 2.0 (4× the ~0.5 pool baseline) |
| `gain_per_mo` | ≥ 4× per-symbol baseline gain/mo |
| `max_dd_pct` | < 30% in 20D window |
| `trades` (4yr) | ≥ 30 |

Short-window (7D/20D) gates are necessarily more lenient — these windows can't achieve 30 trades per symbol in stocks:

| Window | Min trades | Max dd | Min wsharpe | Min delta |
|--------|-----------|--------|-------------|-----------|
| 20D stocks | 4 | 40% | 0.0 | −0.05 |
| 7D crypto | 10 | 30% | 0.0 | −0.05 |
| 4yr full | 30 | any | 0.7 | +0.1 |

The 20D window result is a DIAGNOSTIC OVERLAY, not a standalone promotion. It answers: "are the 4yr params still working in the last 20 days, or does a different toggle improve things?"

---

## 5. Bridge Gap (Current Problem + Fix)

### Problem

The `per_sym_20d_agent_stocks.py` writes to:
- `trb/active_config_20d.json` — tradier_manage DOES NOT READ
- `trc/active_config_20d.json` — tradier_manage DOES NOT READ

`tradier_manage.py` reads `{account}/active_config.json`, written by `tradier_hourly_reconfig.py`.

The per_sym_7d_agent writes to:
- `{acct}/active_config_7d.json` — ez_positions_quick DOES NOT READ

`ez_positions_quick.py` reads only `per_sym_active_config.json` (global).

**Result: the 7D/20D agents burn 60-80% CPU on S1 but their output never reaches live.**

### Fix — `tools/dpsa_bridge.py`

Bridge reads from `active_config_20d.json` → merges qualifying entries into `active_config.json` for stocks.

For crypto, bridge reads from account-specific `active_config_7d.json` → merges qualifying entries into global `per_sym_active_config.json`.

Gate applied by bridge:
- `trades_20d >= 4` AND `max_dd_pct < 40%` AND `wsharpe > 0` AND `delta_wsharpe > -0.05`
- Bridge does NOT override entries where new wsharpe ≤ existing wsharpe + 0.02

```bash
# Run manually or via cron
python3 tools/dpsa_bridge.py [--dry-run] [--account trc] [--verbose]
```

Stale pending entries (from old `flz_hourly_reconfig` format, no wsharpe in _meta) are flushed on every bridge run.

---

## 6. Skill Integration — backtest-expert + signal-postmortem

### backtest-expert (evaluate_backtest.py)

After `dpsa_bridge.py` promotes entries, `dpsa_feedback.py` scores each promoted config using the backtest-expert evaluation script:

```bash
python3 claude-trading-skills/skills/backtest-expert/scripts/evaluate_backtest.py \
  --total-trades <trades_20d from candidate> \
  --win-rate <wr_pct> \
  --avg-win-pct <pool_sharpe * std_est> \
  --avg-loss-pct <loss_est> \
  --max-drawdown-pct <max_dd_pct> \
  --years-tested 0.055 \
  --num-parameters 3 \
  --output-dir data/dpsa_feedback/eval/
```

Verdict: Deploy / Refine / Abandon → fed back to the bridge's confidence tier.

**Key backtest-expert principles applied to our pipeline:**
- "Seek plateaus, not peaks" → a 20D winner that only works with EXACT parameters is a peak; require the winning variant to also beat baseline over the prior 20D window
- "Punish the strategy" → apply 1.5× slippage model to per-sym override (already baked in by the engine's 0.04% round-trip cost)
- "Sample size matters" → 20D stock = ~4-8 trades; this is DIAGNOSTIC, never authoritative on its own
- "Time robustness" → verify the 20D winner also works in the prior 20D window before promoting

### signal-postmortem (nightly)

After market close, `dpsa_feedback.py` runs a per-symbol postmortem:
1. Read today's trades from `data/history/{account}/*.jsonl`
2. For each traded symbol, find its per_sym config `wsharpe`
3. Compare predicted wsharpe vs realized: `(pnl_today / avg_trade_size) / std`
4. Flag symbols where per_sym consistently overestimates (drifted configs)
5. Write to `data/dpsa_feedback/postmortem_{date}.json`

---

## 7. Per_sym Agent Coverage — What Must Run Daily

### Stocks (before 13:00 UTC)

1. `tradier_hourly_reconfig.py --accounts trb` (11:00 UTC) — writes `trb/active_config.json` — **A/B treatment arm (7D tweaks)**
2. `tradier_hourly_reconfig.py --accounts trc` (11:20 UTC) — writes `trc/active_config.json` — **7D overlay account**
3. `dpsa_bridge.py --account trc` (11:45 UTC) — supplements trc with 20D knob optimization
4. `run_persym_promote_gated.sh` (every 6h) — promotes floor-clean improvers from pending to 4yr baseline

### Crypto (before 12:00 UTC)

1. `watchdog_persym_s1.sh` shards (every 20 min, continuous) — builds 7D crypto candidates
2. `dpsa_bridge.py --crypto fin` (11:45 UTC) — when crypto 7D wiring is in place
3. `deploy_persym_live.sh` (every 15 min) — syncs sandbox `per_sym_active_config.json` → live dir

---

## 8. Known Gaps / Next Steps

| Gap | Priority | Action |
|-----|----------|--------|
| Crypto 7D: `ez_positions_quick` doesn't read per-account 7D files | HIGH | Unlock ez_positions_quick, add per-account layer before global read for fin |
| Per_sym_7d_agent covers only flz/ang/men/fin, not inf | MED | Add `inf` to shard list in `watchdog_persym_s1.sh` |
| OLD `MIN_HOLD_BARS=20` candidates (ang/men/fin _candidates/) | LOW | Purge old format — all have `verdict=REJECT_DD`, never promoted |
| 20D agent runs continuous (no guaranteed pre-open finish) | MED | Add explicit pre-open run at 10:30 UTC (before tradier_hourly_reconfig starts) |
| No multi-period robustness check on 20D winners | MED | Implement: verify winner also beats baseline in prior 20D window before promoting |
| signal-postmortem feedback loop not built | MED | Build `tools/dpsa_feedback.py` |

---

## 9. Quick-Check Commands

```bash
# What is currently live in per_sym?
ssh s1-int 'python3 -c "
import json; d=json.load(open(\"/home/niels/binance/data/hourly_reconfig/per_sym_active_config.json\"))
pos=[k for k,v in d.items() if isinstance(v,dict) and v.get(\"wsharpe\",0)>0]
neg=[k for k,v in d.items() if isinstance(v,dict) and v.get(\"wsharpe\",0)<0]
dis=[k for k,v in d.items() if isinstance(v,dict) and v.get(\"overrides\",{}).get(\"LONG_ENABLED\")==False]
print(f\"Active: {len(pos)} positive | {len(neg)} negative-wsharpe | {len(dis)} disabled\")
"'

# Check 20D candidates for trc — how many would bridge promote?
python3 tools/dpsa_bridge.py --dry-run --account trc --verbose

# Run bridge for real (after confirming dry-run looks good)
python3 tools/dpsa_bridge.py --account trc

# Check what tradier_hourly_reconfig last wrote
ssh s1-int 'ls -la /home/niels/binance-sandbox/data/hourly_reconfig/trc/active_config.json && wc -l /home/niels/binance-sandbox/data/hourly_reconfig/trc/active_config.json'

# Per_sym S1 pipeline health
ssh s1-int 'pgrep -afc "per_sym_20d" ; tail -3 /home/niels/logs/persym_cron.log ; tail -5 /home/niels/logs/persym_promote_gated.log'
```
