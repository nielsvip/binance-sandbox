# CLOUD_SESSIONS.md — Running Claude Cloud Sessions on Git

The Mac repo mirrors to GitHub (`origin`, `nielsvip/binance-sandbox.git`) every
5 minutes via `autosave_15min.py` (commit + push of everything not ignored).
Cloud sessions work entirely from that mirror.

## Bootstrap a cloud session

1. `git clone https://github.com/nielsvip/binance-sandbox.git && cd binance-sandbox`
2. `pip install -r requirements.txt` (full live stack needs the `binance_env`
   conda env + exchange keys; cloud sessions are code/analysis-only).
3. Read `CLAUDE.md`, then `BACKTEST_BIBLE.md` before touching anything.
4. Market data / NPZ caches are NOT in git (GBs, ignored by design). Regenerate
   via the documented fetch scripts or mount a worker cache — never commit them.

## Rules

- Work on a branch (`git checkout -b cloud/<topic>`), push the branch. NEVER
  push to `main` — the Mac autosave owns `main` and commits every 5 minutes.
- No secrets in commits, ever: `.env`, `auth-profiles.json`, raw API keys and
  private keys are ignored. Verified clean 2026-10-07; keep it that way.
- `LOCKED_FILES.md` binds cloud sessions exactly like local ones.
- Small, reviewable commits. The Mac merges cloud branches explicitly and
  manually — describe what you changed and how you verified it.

## Getting cloud work onto the Mac (manual, user-only)

The Mac never auto-pulls (live-trading source of truth). To land a reviewed
cloud branch: on the Mac, `git fetch origin <branch>` into a scratch worktree,
diff against `main`, then apply. Autosave commits the result within 5 minutes.
