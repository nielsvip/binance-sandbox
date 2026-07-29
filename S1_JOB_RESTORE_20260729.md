# S1 job restoration after matrix-only mode — 2026-07-29

The pre/post crontabs are preserved on S1 under
`data/reports/matrix_precedence_repair_20260729T1610Z/`.

- before SHA-256:
  `b03bd2d482698e3613cb8d761b5c2b77c7ba8a59766675ae22e5715289c204af`
- after SHA-256:
  `4fb40c6b3390d8a7a1b843c4d860d6e9104894f8bc97f01f291cd490aea4412f`

Restored jobs are limited to live safety/monitoring, market-data retention,
reporting, research, and operator inbox support:

- crypto live watchdog, Binance ban watchdog, hedge safety/daily monitor,
  breakout hunter, tradeable refresh, and scalper monitor;
- Tradier premarket, OI history, discrepancy and account-comparison monitors;
- trader research, stock scanner, opinion causality, local advisory, agent
  snapshot and Gmail-draft inbox poller;
- symbol report, sweep duplicate monitor, git autosave, idle NPZ regeneration,
  and central result ingestion.

The already-active hourly matrix report/export, twice-daily result digest
email, and five-minute repaired matrix watchdog were preserved. The latest
email log before restoration recorded a successful STARTTLS delivery at
2026-07-29 12:01 UTC after the SSL endpoint timed out.

The following matrix-only disabled jobs deliberately remain off:

| job family | reason kept off |
|---|---|
| `nightly_lab`, old `lab_matrix`, `ofat_monitor`, `testdrivers_watchdog`, old PSC arrow/grinder | obsolete or blind grinders superseded by the repaired exact gate and coherent-bundle pipeline |
| FLZ/Tradier hourly reconfiguration, reopt, ZEC search/baseline | writes strategy configuration; restoration of monitoring is not authorization to change live parameters |
| per-symbol promotion/deployment and crypto per-symbol writer | promotion requires current exact parity/holdout gates; no automatic live mutation during repair |
| blanket 20:00 Tradier `pkill` line | broad process kill is unsafe and conflicts with ownership-aware watchdogs |
| old framework monitor, replay/culprit hunts, autoresearch, vector baseline campaign, pilot watchdog | nonessential CPU-heavy research while the frozen-fingerprint exact smoke is establishing authority |
| periodic per-symbol legacy agents | superseded or not proven against the repaired matrix contract |

No credentials are included in this receipt.
