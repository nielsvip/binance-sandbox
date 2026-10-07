import json, pathlib

def test_hao_finished_broken():
    p = pathlib.Path("data/reports/lifecycle_pilot/HAO_LONG_v14_progress.json")
    if not p.exists():
        return
    j = json.loads(p.read_text())
    # symside is HAO_LONG, done should contain real deltas, not sentinel -207 for every row
    # If final_gain is 0.00 and baseline is -207, indicates cumulative never advanced (broken)
    baseline = j.get("baseline_gain", 0)
    done = j.get("done", {})
    # Count how many entries have delta == -baseline (i.e., vec_gain -207 vs cum 0)
    bad = 0
    total = len(done)
    for k, v in done.items():
        if abs(v.get("delta", 0) + baseline) < 1e-6 and abs(v.get("cumulative_before", -1)) < 1e-9:
            # delta == -baseline means vec_gain -207 vs cum 0, but cumulative_before should have been baseline? Indicates sheet never left 0
            # Actually for GLOBAL_RISK_GATES, cumulative_before 0 is initial E, delta -207 is vec_gain -207 vs 0, but should be vs baseline -207? Check.
            # Simpler: if final_gain is 0.00 while baseline is -207 and done>0, sheet is broken (never promoted any positive, but also never set E to baseline)
            pass
    # The file's xlsx counterpart should have E not 0: check if finished but broken flag exists
    # We assert that finished with 0.00 gain and >1000 done is suspicious — test will fail if broken
    done_cnt = len(done)
    # The progress shows done 1249 but file says ~30 entries visible (truncated read), real file has 1249? Check final_gain handling: script marks finished with final_gain 0.00 when cumulative_after stays 0
    # This test captures broken: done large but cumulative_after stays 0
    cumulative_afters = [v.get("cumulative_after", 0) for v in done.values()]
    # If max cumulative_after is 0 while baseline is -207, sheet never advanced from 0 — broken
    max_after = max(cumulative_afters) if cumulative_afters else 0
    assert not (done_cnt > 500 and abs(max_after) < 1e-9 and abs(baseline + 207) < 10), f"HAO_LONG finished={done_cnt} but max cumulative_after={max_after} with baseline {baseline}: sheet broken NT FINISHED (needs refill or rerun) final_gain 0.00 is placeholder"
