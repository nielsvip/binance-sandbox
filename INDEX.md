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

1. **Obtain User Approval**: Wait for user review and explicit approval of the master plan in `implementation_plan.md`.
2. **Parity Check**: Run `check_sandbox_parity.py` at the start of the execution phase.
3. **Parameter Tuning**: Ensure `config_tradier.py` and `config.py` reflect the Pareto-optimal parameters (e.g., `ATR_5m_x2.0` stocks trail) without any "Lying Sharpe" or "Suicide Reentry" regressions.
4. **S1 Sweeping**: Ensure S1 is running both crypto and stocks sweeps, alternating CPU shares hourly and maintaining >60-70% system utilization.
