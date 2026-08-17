# MSTR_SHORT causal lifecycle results — 2026-08-01

These are vectorized chronological position-lifecycle results, not scalar proxy
fills and not exact V8.  Every signal uses completed-bar availability and a
strictly later execution bar.  Costs are zero commission plus 5 bps adverse
slippage each way; strategy capacity is $16,000 and side-aware B&H deploys
$2,000.

## S1 archive run (preferred evidence)

- Window: 2026-03-20 through 2026-08-01; 21,656 execution bars.
- Source NPZ: `data/matrix_npz/mstr_v7_causal_20260801_s1/MSTR.npz` on S1.
- NPZ SHA256: `e3ade1c17165fe8c4076a4f0eb0febfc404ae2f000b0ca8f4c58f8b389f24929`.
- Data contract: core PASS; ladder PASS; floor PASS; future HTF count 0;
  interpolated 5m 5.965%, parent lag 0–600 seconds.
- Recipes evaluated: 46,656; positive-B&H-delta recipes: 42,367.
- No evaluated recipe landed in the 50–80% TIM preference band.  The mandatory
  exit-price reclaim kept successful short recipes near 98.77% TIM.

### Highest gain/month

- Gain/month: **66.6448%**.
- Side-aware short B&H/month: **7.6713%**.
- Delta: **+58.9736 pp/month**; **8.6876× B&H**.
- Account max drawdown: **61.3865%**.
- Real closes: **13** (3.0374/month); fills 70; augments 61; reduces 5;
  full exits 8; clamps 0; flat-beyond-reclaim bars 0.
- Entry + augment: `DC_15m_N20_B100_R10` — 15m Donchian break/bounce/reclaim,
  lookback 20, bounce 100 bps, reclaim 10 bps, augment 2 units.
- Exit: `E02_4h_N30` — opposite 4h Donchian structural break, lookback 30.
- Reduce: `WT_OUT_4h_D0_P0`, 50%.
- Reentry: mandatory stored exit-price reclaim.
- Result SHA256: `03edddc2091301794dbbbf3cfb0a94bd3e39271fa5306a4f117bc56907aa300a`.

### Weekly-activity alternative (better risk/activity balance)

- Gain/month: **64.1203%**.
- B&H/month: **7.6713%**; delta **+56.4490 pp/month**; **8.3585× B&H**.
- Account max drawdown: **49.8590%**.
- Real closes: **76** (17.7572/month); TIM **98.7708%**.
- Same 15m DC entry/augment and 4h N30 structural exit as above.
- Reduce: `WT_OUT_15m_D0_P0`, 25%.
- Result SHA256: `84abfd4793d6796705b85320e06f01abdd3b8c99e620afb40a398c1af16ba4b2`.

The weekly-activity alternative gives up only 2.52 pp/month while reducing
drawdown by 11.53 percentage points and increasing closes from 13 to 76.  It is
therefore the stronger replay candidate; neither row has been mislabeled exact
or written over an existing V8 matrix cell.

S1 artifact hashes:

- Result: `733a7902e36b4ae5e94769a81e912ffc872eba5adce8d1197cf5d51e4a9f0099`.
- Manifest: `a87ef4f40e3f67317f40ad4a8cb480c62b8a45eadcf4f4cbd468dcb83af1ba04`.
- Raw ledger: `0d3a7fc95d53c84596896c0cbe178dd0f59aa1e2bccfe56b65d839a92ef50d46`.
- Receipt identity: `f010a5f3bf60bc8fbce838ce75c73972a9ddf3237a0d70cc8790d02db4199005`.

## Local recent-window cross-check

- Window: 2026-05-12 through 2026-08-01; 5,126 bars; 46,656 recipes.
- Best: 184.0445%/month versus 18.7108% B&H/month, +165.3337 pp/month,
  9.8363× B&H, 99 closes, 97.4896% TIM, 21.1557% account drawdown.
- Entry + augment: `DC_5m_N5_B10_R0`.
- Exit: `WTDC_X45_N4_K85_DC0.85`.
- Reentry: mandatory stored exit-price reclaim; no reduce.

The local run is a useful short-window cross-check, but the longer S1 archive
run is the preferred robustness evidence because it uses four times as many
execution bars and only 5.965% interpolated 5m data.
