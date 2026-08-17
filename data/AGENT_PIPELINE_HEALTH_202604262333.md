# Agent Pipeline Health — 2026-04-26 23:33 UTC

## Component Status

| # | Component | Status | Evidence |
|---|---|---|---|
| 1 | cron: tradeable_refresh_loop */1 | GREEN | last run 23:33:06 UTC (5s ago), refresh ok in 3.9s, fin top=25 / ang top=25 |
| 2 | cron: local_advisory_generator */1 | GREEN | last run 23:33:07 UTC, fin=6 ang=3 advisories, causality_active=True |
| 3 | cron: agent_snapshot_writer */5 | YELLOW | writes snapshot.json fine (mtime 23:30:06) BUT git push to nielsvip/binance-agent-handoff REJECTED (`fetch first`) |
| 4 | cron: agent_inbox_poller */5 | YELLOW | last 23:25:11 — runs and processes 0 drafts, BUT IMAP `Session expired, please login again` errors visible in tail |
| 5 | cron: opinion_causality_scan 0 */6 | GREEN | 22:36:03 scored 687 trades, wrote opinion_causality_report.json (33.5KB) |
| 6 | output: tradeable_refresh.json | GREEN | mtime 23:33:06 (27s old), 55KB |
| 7 | output: fin_advisories.json | GREEN | mtime 23:33:06, 6 active advisories, all expires_at_utc in future |
| 8 | output: ang_advisories.json | GREEN | mtime 23:32:10, 3 active advisories (ADAUSDC/PHBUSDT/UNIUSDC SHORT force_open $50) |
| 9 | output: snapshot.json | GREEN | mtime 23:30:06, 1.4MB |
| 10 | output: tv_enrichment.json | RED | mtime 22:20:14 — STALE 73 min. No cron generator found |
| 11 | consumer reachability | GREEN | `import fin_advisory_consumer; SUPPORTED_ACCOUNTS={ang,fin,inf,flz,men}; check() returns None on no advisory` |
| 12 | consumer multi-account wiring | GREEN | SUPPORTED_ACCOUNTS now 5 accounts (was fin-only). Was the recent edit. |
| 13 | live worker awareness | YELLOW | All 5 ez_manage workers restarted 23:30–23:33 (within last 3 min) → already running NEW consumer code. fin worker (PID 17081) is consuming advisories actively (decision log entries every 3s) |
| 14 | sandbox parity ez_manage.py | GREEN | MD5 8a221b88… matches MB+S1+S2 |
| 15 | sandbox parity fin_advisory_consumer.py | RED | MB=5ec2b0d9… vs S1+S2=7393e84e… DRIFT |
| 16 | decision log activity | GREEN | `agent_decisions_log.jsonl` writing every 3s, fin acting on 6 advisories (force_close 1000FLOKI/CHR LONG, hold IOTX/BNB/FLOKI SHORT) |

## RED failures + fixes

1. **fin_advisory_consumer.py drift MB↔S1/S2** — fix: `rsync -az --existing --update fin_advisory_consumer.py s1-int:/home/niels/binance-sandbox/ && rsync ... s2-int:...` (CLAUDE.md sandbox-parity rule).
2. **tv_enrichment.json stale 73min** — no cron generator found. Either add a cron (e.g. `tv_enrichment_writer.py */5`) or document as on-demand-only. If the consumer/local_advisory_generator reads it, advisories will use stale TV context.
3. **agent_snapshot_writer git push rejected** — remote diverged. Fix: in writer, change `git push` to `git pull --rebase && git push` or `git push --force-with-lease`. Snapshot file write itself works — only the GitHub mirror is broken.

## YELLOW issues

- **inbox_poller IMAP session expired** — Gmail token may need refresh. Currently still completing runs (processes 0 drafts) but failing to fetch new mail.
- **No cron entry for `agent_decisions_log.jsonl` rotation** — file growing unbounded.

## Live worker restart proposal

**No restart required.** All 5 crypto ez_manage workers were restarted between 23:30:09–23:33:26 UTC (within last 3 min, AFTER consumer multi-account edit at 23:32). They are already running the new code (confirmed by `fin` actively force-closing 1000FLOKI/CHR per advisory).

If user later edits `fin_advisory_consumer.py` again, restart per CLAUDE.md rules:
```
pkill -f "python.*ez_manage.py --account fin"   # then ang/inf/flz/men
```
Workers auto-restart via `binance_supervisor` (launchd `com.niels.binance-supervisor` PID 1450, KeepAlive=true) + `run_with_watchdog.sh` wrappers — pkill is sufficient, no manual relaunch.

`ez_positions_quick.py` does NOT import the consumer — only `ez_manage.py` line 19642 does. No quick worker restart needed.

Tradier (trb/trc) not in SUPPORTED_ACCOUNTS — separate `tradier_advisory_consumer.py` exists; out of scope for this verification.
