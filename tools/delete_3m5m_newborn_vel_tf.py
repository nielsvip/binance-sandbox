#!/usr/bin/env python3
"""Delete 3m (and 5m, the stocks twin) from EVERY vector and live path of NEWBORN_LOSS_KILL_VEL_TF.

OPERATOR 2026-10-28, verbatim:
  "3m (5m for stocks) need to be deleted from all vector and live paths for this switch"

WHY (measured, not inferred):
  * wt_velocity_3m and wt_velocity_5m exist in NO indicator set -- checked AAPL (stocks), BTCUSDC and NMRUSDT (crypto);
    wt_velocity_15m / _1h / _4h are present in all of them.
  * The literal "3m" matched no branch in the resolver, so "", None and "3m" all fell through to
    `else: vel_key = "wt_velocity_5m"` -> vel_used 0.0 -> vel_ok False. With NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST
    defaulting True on both venues, the whole NEWBORN_LOSS_KILL exit was unfireable.
  * This is NOT a dormant switch: QuickConfig.NEWBORN_LOSS_KILL_ENABLED is True, and 124 of 791 per-symbol sets carry
    NEWBORN_LOSS_KILL_ENABLED=True (91 crypto, 33 stocks). per_sym beats the config default, so the exit is armed there.

15m is the replacement fallback: it is the repo's decision grid and already the `filter_default` for the sibling
NEWBORN_LOSS_KILL_FILTER_TF (vec_decisions/twin_yellow_filters.py). Legal set becomes 15m / 1h / 4h.

WHY THIS IS ONE ATOMIC PATCH, applied all-or-nothing
The vector engine resolves the TF itself, and the stocks live twin resolves it separately. Patching only the twins
leaves live evaluating real 15m velocity while the board still asks for a missing 3m array and therefore says the exit
never fires -- a vec/live divergence on 33 live stock sym_sides, which is worse than the bug. An earlier partial pass
was withdrawn for exactly that reason (backups/before_delete_3m5m_newborn_vel_202610280234_*).

LOCKS: v12_quick_engine.py, ez_manage.py, config.py and config_tradier.py are in LOCKED_FILES.md. Run this only with
the operator's unlock in the same message. It refuses unless --i-have-the-unlock is passed.

Dry-run by default. --apply backs every file up, rewrites, byte-compiles, and refuses the whole batch if any anchor is
missing or any file fails to compile.
"""
import argparse
import datetime
import pathlib
import py_compile
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (path, needs_unlock, [(old, new, expected_count)])
TWIN_OLD = '''            vel_tf = str(get("NEWBORN_LOSS_KILL_VEL_TF", "") or "3m").strip().lower()
            if vel_tf in ("5m", "5"):
                vel_key = "wt_velocity_5m"
            elif vel_tf in ("15m", "15"):
                vel_key = "wt_velocity_15m"
            elif vel_tf in ("1h", "1"):
                vel_key = "wt_velocity_1h"
            elif vel_tf in ("4h", "4"):
                vel_key = "wt_velocity_4h"
            else:
                vel_key = "wt_velocity_5m"'''
TWIN_NEW = '''            # USER 2026-10-28: "3m (5m for stocks) need to be deleted from all vector and live paths for this
            # switch". wt_velocity_3m/_5m exist in no indicator set, and "3m" matched no branch, so "", None and "3m"
            # all fell into else -> wt_velocity_5m -> vel_used 0.0 -> vel_ok False, which made the exit unfireable.
            vel_tf = str(get("NEWBORN_LOSS_KILL_VEL_TF", "") or "15m").strip().lower()
            if vel_tf in ("1h", "1"):
                vel_key = "wt_velocity_1h"
            elif vel_tf in ("4h", "4"):
                vel_key = "wt_velocity_4h"
            else:
                vel_key = "wt_velocity_15m"'''

EDITS = [
    ("vec_decisions/twin_sizing_reduce.py", False, [(TWIN_OLD, TWIN_NEW, 1)]),
    ("vec_paths/newborn_loss_kill.py", False, [
        ('            tf = "5m" if mode == "tradier" else "3m"',
         '            # USER 2026-10-28: no 3m/5m for this switch; neither array exists in any indicator set.\n'
         '            tf = "15m"', 1),
        ('    NEWBORN_LOSS_KILL_VEL_TF: str = ""             # auto: "3m" crypto / "5m" tradier',
         '    NEWBORN_LOSS_KILL_VEL_TF: str = ""             # empty -> 15m on both venues (USER 2026-10-28: no 3m/5m)', 1),
    ]),
    ("vec_paths/v12_exit_reduce_gap_batch3.py", False, [
        ('    vel_tf = str(_cfg(config, "NEWBORN_LOSS_KILL_VEL_TF", "") or "3m")',
         '    # USER 2026-10-28: no 3m/5m for this switch (feeds backtest_v12_engine, the live-faithful verifier).\n'
         '    vel_tf = str(_cfg(config, "NEWBORN_LOSS_KILL_VEL_TF", "") or "15m")', 1),
    ]),
    # stocks LIVE resolves through this twin only -- tradier_manage.py delegates to newborn_vel_key and needs no edit.
    ("tradier_filter_tf_twins.py", False, [
        ('    fam = str(vel_tf_raw or "").strip() or "3m"',
         '    # USER 2026-10-28: no 3m/5m for this switch; this family fallback named an array that never exists.\n'
         '    fam = str(vel_tf_raw or "").strip() or "15m"', 1),
    ]),
    # ---- LOCKED from here down ----
    ("v12_quick_engine.py", True, [
        ('(getattr(cfg, "NEWBORN_LOSS_KILL_VEL_TF", "") or "3m")',
         '(getattr(cfg, "NEWBORN_LOSS_KILL_VEL_TF", "") or "15m")', 2),
    ]),
    ("ez_manage.py", True, [
        ('(getattr(config, "NEWBORN_LOSS_KILL_VEL_TF", "") or "3m")',
         '(getattr(config, "NEWBORN_LOSS_KILL_VEL_TF", "") or "15m")', 2),
    ]),
    ("config.py", True, [
        ('    NEWBORN_LOSS_KILL_VEL_TF: str = ""  # auto: "3m" crypto / "5m" tradier when empty',
         '    NEWBORN_LOSS_KILL_VEL_TF: str = ""  # empty -> 15m both venues (USER 2026-10-28: 3m/5m deleted; neither array exists)', 1),
    ]),
    # test fixtures that pinned the deleted behaviour (wt_velocity_5m / _3m are absent from real data)
    ("test_sizing_reduce_twin.py", False, [
        ('    ind = {"wt_velocity_5m": -1.5,', '    ind = {"wt_velocity_15m": -1.5,', 1),
        ('_nlk_ind(wt_velocity_5m=1.5)', '_nlk_ind(wt_velocity_15m=1.5)', 1),
    ]),
    ("test_v12_exit_reduce_gap_batch3.py", False, [
        ('data["wt_velocity_3m"][-1] = 1.0', 'data["wt_velocity_15m"][-1] = 1.0', 1),
        ('del data["wt_velocity_3m"]', 'del data["wt_velocity_15m"]', 1),
        ('("wt_velocity_3m",)', '("wt_velocity_15m",)', 1),
    ]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--i-have-the-unlock", action="store_true",
                    help="required for the LOCKED files; the operator must have said 'unlock <file>' in the same message")
    a = ap.parse_args()
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M")
    planned, refusals = [], []
    for rel, locked, subs in EDITS:
        p = ROOT / rel
        if not p.exists():
            refusals.append("%s: missing" % rel)
            continue
        if locked and not a.i_have_the_unlock:
            refusals.append("%s: LOCKED -- needs the operator's unlock (--i-have-the-unlock)" % rel)
            continue
        src = p.read_text()
        new = src
        for old, rep, want in subs:
            got = new.count(old)
            if got != want:
                refusals.append("%s: anchor found %d time(s), expected %d -- %r" % (rel, got, want, old[:60]))
                new = None
                break
            new = new.replace(old, rep)
        if new is None or new == src:
            if new == src:
                refusals.append("%s: no change (already patched?)" % rel)
            continue
        planned.append((p, rel, new, len(subs)))
    for r in refusals:
        print("REFUSED  %s" % r)
    for _p, rel, _n, n in planned:
        print("WOULD PATCH  %-46s %d site group(s)" % (rel, n))
    if refusals:
        print("\nnothing written: the batch is all-or-nothing (a half patch is a vec/live divergence)")
        return 1
    if not a.apply:
        print("\ndry-run only -- re-run with --apply")
        return 0
    for p, rel, new, _n in planned:
        shutil.copy2(p, ROOT / "backups" / ("before_3m5m_delete_%s_%s" % (stamp, p.name)))
        p.write_text(new)
    bad = []
    for p, rel, _n, _c in planned:
        if p.suffix == ".py":
            try:
                py_compile.compile(str(p), doraise=True)
            except Exception as exc:
                bad.append("%s: %s" % (rel, exc))
    if bad:
        print("COMPILE FAILED -- restore from backups/before_3m5m_delete_%s_*:" % stamp)
        for b in bad:
            print("   %s" % b)
        return 1
    print("\nPATCHED %d file(s), all compile. Backups: backups/before_3m5m_delete_%s_*" % (len(planned), stamp))
    print("NEXT: pytest test_sizing_reduce_twin.py test_v12_exit_reduce_gap_batch3.py, then deploy ALL patched files")
    print("      together to s1/s2/s5/s6 with md5 verification -- a partial deploy is the same divergence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
