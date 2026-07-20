# INDEX — Memory Preservation Anchor

This file acts as the primary "continue from last time" index, ensuring 100% accurate session continuity across conversations.

## 🎯 2026-07-08 — GAINMO APPLY (full-system change set)
- **Snapshot backup (ROLLBACK POINT): `backups/SNAPSHOT_BEFORE_GAINMO_APPLY_202607082130/`** — all touched code+data+S1 state pre-change, see its README.md.
- Analysis + suggestion list applied: `GAINMO_MAXIMIZATION_20260708.md`.


---

## 🔗 Active Memory Anchors

1. **[SCRIPT_STATE.md](file:///Users/niels/Documents/binance/SCRIPT_STATE.md)** — Status, active features, MD5 checksums, and safety overrides of core trading scripts.
2. **[TOPIC_STATE.md](file:///Users/niels/Documents/binance/TOPIC_STATE.md)** — Session-by-session diagnostic logs, recent sweep findings, and strategic goals.
3. **[100.md](file:///Users/niels/Documents/binance/100.md)** — The Master Backtest Audit Document (Parts 1-15).
4. **[CLAUDE.md](file:///Users/niels/Documents/binance/CLAUDE.md)** — Core trading system rules and absolute prohibitions.
5. **[LOCKED_FILES.md](file:///Users/niels/Documents/binance/LOCKED_FILES.md)** — Locked status of system scripts.

---

## 🎯 Immediate Next Steps

1. **Monitor Reentry Performance**: Check sweep logs on S1 to ensure the new 0.2% trend-resumption bypass is triggering and correctly re-entering strong runners (long and short) that previously starved in the confirmation gate.
2. **Push to Server**: Once you verify MacBook live trading has logic parity and performs well, push the updated files to your live servers.
3. **Audit Results Digest**: Verify the results digest email correctly displays negative short benchmarks and signed `gain_vs_bh` ratios.

## 📚 TEST_SUMMARY.md — comprehensive catalog of ALL backtests/analyses (Mac + S1): live A/B, per_sym-vs-7D, OAT/sensitivity, sweep_results types, central DBs, data pipeline, regenerate cheat-sheet. (added 2026-06-27)
