# ALL DISASTERS 2026-10-19 — live inventory (user-ordered, nothing dropped)

Legend: ✅ fixed+deployed · 🔄 fix shipped, rollout pending · 🆕 open · ⏳ blocked on time/lease.
S1 live = `~/binance` on niels. Mac = `/Users/niels/Documents/binance`. Times UTC.

## P0 — venue / trading now
1. 🆕 **MAC FENCED, S1 HOLDS LEASE — user: Mac ALWAYS first.** Mac wrappers log `[FENCED] S1 holds the lease` every 15s. Failback to Mac pending Mac-health proof (broker 200 OK 20:05, data file fresh 20:05, but klines breaker was holding till ~19:44 and indicators re-warming). Next: prove Mac data fresh → quiesce S1 engines → flip lease → Mac spawns → verify Mac SUCCESS.
2. ⏳ **S1 .52 BANNED till ~01:23 UTC Oct 20 (-1003, escalating all day).** Rotation to .216/.233 with REAL durations deployed (was: every ban marked 900s, re-banned in 15 min). Trading on S1 possible via clean IPs once writers/engines fully bounced.
3. 🆕 **Mac IP banned ~19:26 (~18 min) by klines startup burst** (`fetch_all_max_historical` re-downloaded years for 197 syms × 5 TFs on every restart). Lifts ~19:44. Same burst would re-ban on every klines restart → gated (see 7).
4. 🔄 **ENTRY TIMEOUT STORM.** 34.5k/26.9k/19.2k `BLOCKED_DISPATCH_TIMEOUT` (Mac), 58/58 (S1), zero successes. Causes: 120s budget vs 44.7s serialized twin evals; owned-poisoning (`owned_drop` zero callers); held keys burning serial budget ahead of flat keys. Fixed: `take_plus` (1 acquisition), `owned_fresh`, ENTRY flat-first, **timeout_s=None (entries NEVER time out)**. Deployed S1, fleet bounced (fin landing).
5. 🆕 **ORCHESTRATOR shared-memory connect failure** (reported 20:06, investigating next).

## P1 — data pipeline (wrong data = wrong trades)
- ✅ **15GB sync skip REMOVED** (`scripts/rsync_helpers.sh`: remote skip → always-sync warn-only; local size-cleaner that DELETED oldest-first → warn-only. Backup `backups/before_always_sync_202610192025_rsync_helpers.sh`). The cleaner was a prime delete-loop suspect.
6. ✅ **S1 live missing `published_selection.json`** → all S1 entries refused fail-closed. Backfilled sandbox→live md5-OK.
7. ✅ **`fetch_klines_api`: TOP-UP ONLY** (`startTime`+small limit; full 1500 cold-start only). Was: 1500 bars for a 1-bar gap.
8. ✅ **Startup max-history gated** to below-MIN-depth files + breaker-aware (was: unconditional multi-year redownload every restart).
9. 🆕 **"Marked maxed (1 bars), won't retry 168h".** Thin files wrongly maxed → never repaired. Fix: mark maxed ONLY at/above MIN depth; thin files retry (capped), never 168h silence.
10. 🆕 **ez_prices CLIP loop** (`CRITICAL_SHRINK 899->540`, 4h floor≥1800 violated). Rules: S1 NEVER clips; Mac 1800→≥1200 only. Badly clipped files rebuilt FROM S1 klines, never refetched. Find the delete/refetch loop.
11. 🆕 **15GB sync skip** (`Remote ... exceeds 15GB, skipping sync`). User: ALWAYS SYNC. Find guard, remove skip (keep warning).
12. 🆕 **Stale indicators blocking entries** (`STALE_INDICATORS 91m`). Feeds re-bounced; watch warmup.

## P2 — supervision / infra
13. 🆕 **Engines dying on their own** (flz+fin died ~18:12 uncommanded; stocks trb/trc crash-loop every ~10 min). OOM? ban-crash? NOT investigated yet. My own bounces are logged separately — do not confuse.
14. 🆕 **Supervisor loop starves crypto**: synchronous `s1_failover_start.sh` (up to 240s gate) blocks all later accounts; stocks crash-loop makes it chronic. Single-flight added (active next supervisor start — never signal it: TERM trap kills all traders; HUP restarts unit via cgroup kill).
15. 🆕 **S1 lease file 3.5h stale** (14:39) + Mac `lease.json` empty; tunnel/SSH flapping (banner timeouts on all new handshakes — use `cm-pulse-s1` master, split pgrep patterns or supervisor sees phantoms).
16. ⏳ **`klines_append --all` PAUSED** (`#BANPAUSE_20261019`, backup kept). UNPAUSE after .52 lift (~01:23). Gate loops already banked the ban once — do not unpause early.
17. ⏳ **Post-lift verification**: `OPEN -> SUCCESS`, zero `BLOCKED_DISPATCH_TIMEOUT`, `fixed_stop_audit.py` 0 findings (now: 5/5 BROKER_UNREACHABLE = unverifiable, not missing-stop).

## Shipped this session (deployed S1, tests green)
- `take_plus`, `owned_fresh`, ENTRY flat-first + no-timeout, board backfill, holders live-mirror, ban real durations, klines top-up + history gate. Tests: +4 owned, +2 take_plus, +3 dispatch (incl. no-timeout), +4 ban-cooldown = 19 green; parity file has only its 2 pre-existing `ez_manage`-text failures (file untouched).
- **SELF-INFLICTED**: `kill -HUP` on supervisor restarted the unit → cgroup stop killed 5/5 engines ~4 min (during ban; stops persisted; rebuilt 5/5).
- Backups: `backups/before_*` + `backups/LOCKED_FILES_row_20261019_entry_dispatch_rescue.md` (LOCKED_FILES.md itself FS-locked, row staged).
- Reverted: socket-bind pin (fought the sanctioned 3-IP rotation; rotation + real durations is the fix).

## Post-lift checklist (~01:23 UTC)
- [ ] S1 .52 unbanned (no fresh `banned until` lines) · [ ] unpause klines_append · [ ] audit 0 findings · [ ] SUCCESS lines flowing · [ ] Mac failback if healthy · [ ] close 9/10/11/13/14/15.
