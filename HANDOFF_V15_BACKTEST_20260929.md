# HANDOFF — v15 sweep diagnosis + bible rewrite (2026-09-29)

**Author:** Claude (Opus 4.8) session, 2026-09-29. **No engine/config files were edited this session** — only docs (`BACKTEST_BIBLE.md`, `CLAUDE.md`, this file) plus one safety backup and read-only diagnostics. The QuickConfig↔config parity remediation was assigned to a **separate agent** — do not double-edit it.

> RULE 0: never revert. Fix forward. Read `BACKTEST_BIBLE.md` §14–§56 before touching any sweep/engine file (now mandated in `CLAUDE.md`).

---

## 1. WHAT THIS SESSION CHANGED (files)
- `BACKTEST_BIBLE.md` — rewritten +≈750 lines. New §14–§56 encode the operator's corrected model: greedy delta/baseline (bold=default=live; default flip=0; negatives not summed; no fabricated deltas), daytrade-ON DC-channel exits (§15), simple-vs-big same engine (§16), QuickConfig↔config parity + why blind full-sync zeroes the backtest (§17), zero-delta investigation protocol (§18), NO-LIES for sweeps (§19), delta-log audit (§20/§30), integrity checks (§21), timing (§22), proof protocol (§23), engine anatomy (§25), worked example (§27), 13-tab inventory (§28), DC-channel math (§29), state-machine pseudocode (§37), wiring recipe (§39), NPZ inventory (§40), ledger schema (§53), and **§56 = the operator's VERBATIM fill spec (highest authority)**.
- `CLAUDE.md` — added a MANDATORY "read BACKTEST_BIBLE.md before any backtest/sweep work" block under `## BACKTESTS`.
- `backups/before_daytrade_off_baseline_202609290029.v12_quick_engine.py` — safety copy only (engine NOT edited; do not restore it over a newer engine).
- `this file`.

**NOT touched:** `v12_quick_engine.py`, `config.py`, `config_tradier.py`, `v15_pilot.py`, templates, any running herd/pilot. `tools/sync_backtest_bible.sh` was **not** run (would rsync to S1 while another agent works) — run it later when servers are quiescent.

---

## 2. GROUND-TRUTH FINDINGS (all verified this session)
1. **Delta/baseline math is correct.** Determinism (same overrides → identical gain) and idempotency (default re-apply → exactly 0) both PASS on engine `bd804da7`. The greedy accumulation is sound.
2. **Correct baseline = daytrade ON with DC-channel exits** (loss `dc_low−0.25%`, gain `dc_high−0.1%` on 15m/1h/4h, + `wt1_15m` cross). Fixed-% exits were deleted days ago. Do NOT disable daytrade.
3. **Simple and big systems share ONE engine** (`v12_quick_engine.simulate_one`). `tools/dc_simple_8_sweep.py` sweeps a curated set (`TECHNICAL_DC_*`, `ENTRY_DC_TF`/`ENTRY_DC_BUFFER_PCT`, `WT_LOWER_CROSS_EXIT_TF`, `EMA_9_21_FILTER`). `ENTRY_DC_TF`/`ENTRY_DC_BUFFER_PCT` are wired in the engine but not swept by the big template — candidate coverage add.
4. **No handoff-window regression.** GDX_LONG delta-log at 16:05 (real herd run) == 00:15 (isolated re-run): ~2% of naked flips non-zero, same ~11 mover switches, ~17 distinct gains. "Cells filled" = cells *written*; the pilot still writes them and I reproduced it.
5. **The 20:04 engine autosave removed `_batch3_template_wiring`** — pure `getattr` no-op scaffolding (audit-defeating), plus unused `_b4_*` locals. Correct fake-audit cleanup; changed no trade behavior. Do NOT reintroduce read-only "wiring."
6. **QuickConfig↔live parity gap: ~652 crypto / ~365 tradier strategy-field mismatches.** A blind full-sync of all live values → **0 trades** (live-only entry engines forced on). Sync must be curated (bible §17/§31). **Owned by a separate agent now.**
7. **Timing OK:** ~0.07s cached; ~1.7% of evals >0.1s, max 0.23s. No per-cell perf regression.
8. vec_decisions gate families execute and return real masks — no swallowed-exception cascade.

**Do NOT report "N switches unwired."** Zero-delta causes are a mix (bible §18): candidate==effective-baseline (no-op), config-parity entry-gate suppressing entries, symbol-non-binding, or genuine gap — report per-cause from the delta-log, never a blanket count. Never fabricate deltas to hide honest zeros (NO-LIES, §19).

---

## 3. HOW TO CONTINUE (priority)
1. **Config parity (separate agent):** curated sync per bible §17/§31 (strategy fields live→QuickConfig; EXCLUDE live-only entry engines, ABLATION_*, infra/paths, backtest-normalization bases). Verify each batch: compile + mismatch audit drops + `evaluate_sanitized(GDX_LONG/AXTI_LONG/AXTI_SHORT)` baselines DON'T collapse to 0. Deploy only via a coordinated engine cut (§24).
2. **Prove one clean sheet per server** (bible §23): isolated `V15_PROGRESS_DIR=/tmp/proof_*`, `V15_SKIP_LIVE_AT_DONE=1`, 1 symbol, 14 workers; verify F/G + all yellows filled, ≤0.1s median, greedy E monotonic, bh/gain filename + zoomable chart on Mac.
3. **Cross-check big vs simple** (bible §54): big-system DC-channel baseline should reproduce `dc_simple_8_sweep` `base_gain` to the decimal; divergence = parity/coverage, not engine bug.
4. **Add `ENTRY_DC_TF`/`ENTRY_DC_BUFFER_PCT` to the big template** if the operator confirms (§16/§47).

---

## 4. INFRA / SAFETY (do not violate)
- s1 = crypto sweeps, s2 = stocks; both 16c/30GB. **2026-09-29 s2 was ~1GB free** — do NOT launch a max-worker pilot there; it will OOM-kill in-flight fillers. Run stock proofs on s1 (has the NPZs + free RAM) if needed.
- **Never kill an in-flight pilot** to make room (loses its compute). The operator said explicitly: do not stop anything filling cells.
- Edit only on Mac; rsync to S1/S2 is an **engine cut** — announce to peers, archive pre-cut chains, md5+import-check both boxes; never resume a chain across a cut (`engine_mixed_chain`).
- Connect: `ssh -fNT s1-sftp` first; `s1-int` then `s1-pub`.

---

## 5. VERIFICATION COMMANDS (read-only)
See `BACKTEST_BIBLE.md` §45 (cheatsheet) and §30 (delta-log audit). Quick integrity gate before trusting any sweep (§21):
```python
from tools.opt.v12_pilot import evaluate_sanitized as ES
b=ES("GDX_LONG",{},30)["gain_pct"]; assert ES("GDX_LONG",{},30)["gain_pct"]==b           # determinism
assert abs(ES("GDX_LONG",{"WT_15M_BOUNCE_OPEN_ENABLED":False},30)["gain_pct"]-b)<1e-9    # idempotency (default flip = 0)
```

---

## 6. OPEN QUESTIONS FOR OPERATOR (bible §47)
- Default DC-channel exit TF set for the baseline (15m only vs 15m+1h+4h)?
- Add `ENTRY_DC_TF`/`ENTRY_DC_BUFFER_PCT` to the big template's swept switches?
- Beyond the known live-only set, which fields to exclude from the curated QuickConfig↔live sync?
- STDEV: §56 R20 says fill its single T/F switch (13 tabs). Confirm STDEV should be filled (not skipped) now that `compute_regime_sizing_mult`/`stdev_edge_*` availability is settled.
