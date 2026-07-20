# GitHub sync — architecture and the no-revert guarantee

Added 2026-07-20. Read this before touching git remotes, branches, or anything
involving Claude Code web on this repo.

## Why this exists

Claude Code web (claude.ai/code) cannot see the MacBook or S1 filesystem
directly — it only ever sees a GitHub repo. This file documents how this
repo's code gets mirrored to GitHub so Claude Code web has visibility, and
the hard rule that makes it safe: **GitHub is a one-way mirror. Nothing ever
flows from GitHub back into a live machine automatically.**

## What's mirrored

- **Mac (`/Users/niels/Documents/binance`)** — LIVE TRADING, the source of
  truth per CLAUDE.md. Pushes to the `main` branch of the private GitHub
  repo via `autosave_15min.py`'s `git_push()`, every 15 minutes, only after
  a successful local commit. Uses a dedicated deploy-key SSH identity
  (`~/.ssh/id_ed25519_github_binance_main`, git remote alias
  `github-binance-main`) scoped to only this one repo — same pattern already
  used for `binance-agent-handoff`.
- **What's tracked**: flat top-level code/doc files only (this repo's
  existing, deliberate convention — see `.gitignore`). Data caches
  (`data/`, `klines_cache*/`), backups, logs, IDE state, machine-local junk,
  databases, and generated reports are excluded. `.gitignore` also excludes
  anything that scanned positive for live credentials (found and fixed
  2026-07-20: `auth-profiles.json` had a live Anthropic OAuth token +
  OpenRouter/Google keys in plaintext — that file must never be tracked).
- **S1 (`/home/niels/binance-sandbox`)** — backtesting sandbox. Has
  substantial code that has diverged from Mac (hundreds of experiment
  scripts never pushed back). Not yet wired to GitHub as of 2026-07-20 —
  see the open item at the bottom of this file.

## THE RULE: no pull, no fetch-and-merge, no reset-from-remote — ever

1. **Never run `git pull`, `git fetch && git merge`, or
   `git reset --hard origin/*`** on Mac or S1 against this remote, in any
   script, cron job, or manual command. This repo's git history only ever
   moves forward locally (autosave commits) and only ever flows outward
   (push to GitHub). This is the same principle as CLAUDE.md's existing
   Absolute Prohibition on `git reset`/`restore`/`checkout` — extended
   explicitly to the new remote.
2. **If Claude Code web (or anyone) makes edits through GitHub** (a PR,
   a commit on a branch, a suggestion), those changes land on GitHub only.
   Bringing them onto a live machine is a *manual, reviewed* action: read
   the diff (`git show`/`git diff` against the specific commit), decide file
   by file, then apply by hand or with `git cherry-pick` of a specific
   commit — never by pulling/merging a branch wholesale. This mirrors
   CLAUDE.md's Death Penalty rule against reverting live code: the danger
   isn't git per se, it's an old or wrong version silently landing on top
   of current live logic.
3. **`main` should be branch-protected on GitHub** (require PRs, block
   direct pushes from anyone but this repo's own deploy key) once the repo
   exists — open item below, needs the GitHub web UI.
4. **No force pushes, ever**, from any automation. `git_push()` in
   `autosave_15min.py` only ever does `git push github-binance-main
   HEAD:main` — never `--force`.
5. Local git safety (already true, restated): only `git commit` (autosave,
   `--no-verify` for the same reason documented in the 2026-07-20 recovery
   commit — see `git log`), never `reset`/`restore`/`checkout` on this repo.

## Open items (need a human action, not something Claude can do unattended)

1. **Create the private GitHub repo.** Needs either `gh auth login` (browser
   flow) or manually creating it at github.com. Suggested name:
   `binance-trading-system`, **private**.
2. **Add the deploy key** (`~/.ssh/id_ed25519_github_binance_main.pub`,
   printed during the 2026-07-20 session) to that repo under
   Settings → Deploy keys, **with write access**.
3. Once both exist, wire the remote:
   `git remote add github-binance-main git@github-binance-main:<owner>/<repo>.git`
   — after that, `autosave_15min.py`'s next 15-min cycle starts pushing
   automatically (the code already no-ops safely until the remote exists).
4. **Branch-protect `main`** on GitHub (Settings → Branches) once populated.
5. **Decide on S1.** S1's `binance-sandbox` has hundreds of files that never
   existed on Mac (experiment scripts) and zero git history of its own.
   Recommended: `git init` + this same `.gitignore` pattern on S1, commit
   locally for safety, and push to a **separate branch** (e.g.
   `s1-sandbox`) on the same repo — never merge that branch into `main`
   automatically. Not yet done as of 2026-07-20; flagged for the user to
   decide since it requires reviewing ~900 changed files for anything that
   shouldn't leave the sandbox.
