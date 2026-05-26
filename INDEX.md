# INDEX — Memory Preservation Anchor

This file acts as the primary "continue from last time" index, ensuring 100% accurate session continuity across conversations.

---

## 🔗 Active Memory Anchors

1. **[SCRIPT_STATE.md](file:///Users/niels/Documents/binance/SCRIPT_STATE.md)** — Status, active features, MD5 checksums, and safety overrides of core trading scripts.
2. **[TOPIC_STATE.md](file:///Users/niels/Documents/binance/TOPIC_STATE.md)** — Session-by-session diagnostic logs, recent sweep findings, and strategic goals.
3. **[100.md](file:///Users/niels/Documents/binance/100.md)** — The Master Backtest Audit Document (Parts 1-15).
4. **[CLAUDE.md](file:///Users/niels/Documents/binance/CLAUDE.md)** — Core trading system rules and absolute prohibitions.
5. **[LOCKED_FILES.md](file:///Users/niels/Documents/binance/LOCKED_FILES.md)** — Locked status of system scripts.

---

## 🎯 Immediate Next Steps

1. **Remove Hold File**: Remove `/tmp/REENTRY_DAEMON_HOLD` once you are ready to resume live reentry evaluation.
2. **Restart Services**: Restart the account services to load the new files from disk (e.g. `ez_manage.py`, `tradier_manage.py`, and `ez_reentry_daemon.py`).
3. **Verify Performance**: Watch active logs for any reentry confirmation decisions and ensure they align with the stochastic and WT indicators.
4. **Sweeps**: Ensure S1 continues sweeps correctly now that the codebase matches MacBook perfectly.
