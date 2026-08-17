# MU Sol-lane mapping (research-only)

This mapping reserves deterministic matrix identities for the three lane fields
`entry_tf`, `exit_tf`, and `reentry_tf` emitted by
`MU_ALL_TF_WT_PRICE_LADDER_VEC_V2`. It does not add rows to the canonical
ENGINE matrix and does not imply that a result exists.

| source field | matrix identity | group | values |
|---|---|---|---|
| `entry_tf` | `MU_WT_PRICE_LADDER_ENTRY_TF` | ENTRY | 5m, 15m, 1h, 4h, D |
| `exit_tf` | `MU_WT_PRICE_LADDER_EXIT_TF` | EXIT | 5m, 15m, 1h, 4h, D |
| `reentry_tf` | `MU_WT_PRICE_LADDER_REENTRY_TF` | REENTRY | 5m, 15m, 1h, 4h, D |

Each combo is owned by the full `(lane, three timeframes, ladder settings,
target units, ledger SHA)` key. An exact-owned logical cell suppresses any amber
vector display. A candidate must carry the `$2,000` base-unit / `$16,000`
capacity contract, a capital-normalized P&L, source NPZ/code hashes, and an
immutable event-ledger hash before it can be queued for exact V8 replay.

Current state: no authoritative S1 result or owner receipt is present locally;
therefore there are zero mapped result rows. Any eventual workbook rendering
uses amber fill `FFF2CC`, font `9A6B00`, italic, and an explanatory comment;
canonical CSV cells and completion counters remain blank.
