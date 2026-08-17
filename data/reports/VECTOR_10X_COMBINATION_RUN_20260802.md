# Vector 10× combination frontier — S1 receipt

Observed on S1 at `2026-08-01T20:19:16Z` (UTC; S1 host `157.180.125.52`).

- Active vector worker: `vec_screen_daemon.py`, PID `1911778`
- Command: `--tag vector_10x_combo_frontier --all-tiers --include-diagnostics --workers 4 --batch 1 --only MU,NVDA,VT,TTD,ACN,LAC`
- Scope: all manifest tiers for the six pilot sides, with vector diagnostics enabled.
- Recent output: 28 new VEC rows in the preceding ten minutes; latest row timestamp `2026-08-01T20:19:16Z`.
- S1 `param_cells`: **93,670** at the observation.
- S1 vector rows from `vec_screen/*`: **83,632**.
- Current grouped-combination fleet: PID `1889445`, continuous, two low-priority workers.
- The grouped-combination queue is active, but MU/NVDA/TTD/ACN currently fail the exact safe-baseline capacity/re-entry contract; those failures are retained as diagnostics and do not erase amber/vector work.

Amber results from this run are valid provisional results and must be retained and ranked for interacting ENTRY/AUGMENT/REDUCE/EXIT/REENTER combinations. They remain separate from exact ENGINE/live credit. The strict escalation target is `>10.0×` side-aware `$2,000` B&H; lower results remain in the frontier and are not discarded.
