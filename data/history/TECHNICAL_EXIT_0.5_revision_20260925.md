Patch 2026-09-25 02:00 UTC — 0.5 exits=TECHNICAL_EXIT

Clarification: quick exits at 0.5% are TECHNICAL_EXIT (not PPL/BREAKEVEN). Previous fix synced per_sym from BEST (93 sym_sides) and made QUICK_REDUCE_TECHNICAL_ONLY per_sym-aware. Remaining divergence: TECHNICAL_EXIT generic was not in sanctioned list, so even technical 0.5 exits were suppressed as stochastic.

Fix: ez_positions_quick.py _named_tech now includes "TECHNICAL_EXIT" and "TECHNICAL" — 0.5% TECHNICAL_EXIT at small gain will now fire live exactly as chart (vector) does. Per_sym remains BEST-synced (BTCUSDC_LONG 95 keys, DC_RECOVERY 0.5 etc). History revision updated.
