# MU exact V8 and matrix handoff criteria

This is a research-only handoff contract. It does not authorize a database,
matrix, live-config, or promotion write.

An MU candidate may enter the exact V8 queue only after untouched validation
and holdout both pass: at least 5× the positive side-aware passive return, or
5× starting-cash wealth when SHORT passive return is non-positive; at least 30
real reduction/close actions; finite drawdown, trade Sharpe, and daily Sharpe;
and immutable NPZ, source, spec, and ledger fingerprints.

Matrix proposal readiness additionally requires the real
`backtest_v8_engine.py` audit to pass schedule execution, recomputed source
signals, accounting, capacity/clamp parity, and ledger/spec fingerprints. A
proposal still requires the matrix owner to perform same-contract baseline,
trade-fingerprint inertness, ownership, and deduplication checks before an
external transaction.

MU-only evidence remains diagnostic (`n_syms=1`) and is never sufficient for
live promotion. `tools/build_mu_exact_handoff.py` writes proposal artifacts
only; it never opens the result database or edits the matrix/live config.
