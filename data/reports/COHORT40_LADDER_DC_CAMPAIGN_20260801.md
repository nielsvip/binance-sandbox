# 40-key ladder + 5m DC-bounce campaign

`tools/run_40_symbol_ladder_dc_campaign.py` now orchestrates the existing
causal vector adapters for 20 long and 20 short TRB keys.  Each key runs:

- D/4h/1h ladder sizing `10/6`, `6/4`, `4/1` with the stored reclaim rule;
- `DC_BREAK_BOUNCE_RECLAIM` and `ENTRY_BOUNCE_5M_LOW` entry overlays;
- side-specific B&H comparison, capacity/clamp telemetry, and a hard
  `>=1 close/week` gate;
- an exact-V8 queue containing only rows that beat side-aware B&H, meet the
  weekly activity floor, and have zero capacity clamps.

The runner is research-only: it writes no matrix, result DB, or live config.
This is deliberate because vector evidence cannot be promoted as exact V8
evidence under the current Bible.  The local c2 NPZ bundle contains only
MU/NVDA/VT; the full 40-key run therefore must execute on S1’s complete NPZ
bundle.  No live promotion is claimed until those S1 rows produce exact
receipts and satisfy the same activity gate.
