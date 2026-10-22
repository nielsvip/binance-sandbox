"""tests for tools/v18_brain.py (E8, 2026-10-20): cat_side separation, identity lines never evidence, floor guarantee,
propensity booking, prequential order, export size + schema. Fixtures: a tiny rebuilt-style sqlite, delta logs with identity /
FORWARD / HOLDOUT lines across two cat_sides."""
import json
import math
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import v18_brain as B  # noqa: E402


def _ev(ts, sym, sheet, row, sw, cand, label, delta, cum, cached=False, valid=True, tim=50.0, trades=30, dd=2.0, sh=0.3, wr=60.0, fp=None, **kw):
    d = {"ts": ts, "sym_side": sym, "nav": "sequential", "sheet": sheet, "row": row, "switch": sw, "cand": cand, "label": label,
         "gain_pct": cum + delta, "trades": trades, "tim": tim, "max_dd_pct": dd, "pool_sharpe": sh, "wr_pct": wr, "valid": valid,
         "invalid_reason": "", "cum_before": cum, "delta": delta, "secs": 0.0003 if cached else 0.5, "cached": cached, "err": "", "fp": fp or f"fp{delta:.3f}"}
    d.update(kw)
    return d


def _board_lines(sym, day, cum, promote_cand="1h", alt_pos=False, brain_p=None):
    t = lambda m: f"2026-10-{day:02d}T01:{m:02d}:00Z"  # noqa: E731
    L = [{"ts": t(0), "sym_side": sym, "label": "BOARD_CTX", "cat_side": B.cat_side_of(sym), "tier": "MEDIOCRE", "bh": 5.0, "baseline_gain": cum, "round": f"r{day}"},
         _ev(t(1), sym, "START", None, "BEST_START", "", "BEST_START_SET", 0.0, cum, fp="chain")]
    # row 3: identity naked (cached, delta 0, same fp as the chain) + yellows -> inert map (b alone) + identity settlement
    L.append(_ev(t(2), sym, "ENTRY_REVERSAL_BOUNCE", 3, "HA_WICK_QUALITY_TF", "OFF", "naked", 0.0, cum, cached=True, fp="chain"))
    L.append(_ev(t(2), sym, "ENTRY_REVERSAL_BOUNCE", 3, "HA_WICK_QUALITY_TF", "OFF", "EMA_9_21_FILTER_FILTER_TF=OFF", 0.4, cum))
    L.append(_ev(t(2), sym, "ENTRY_REVERSAL_BOUNCE", 3, "HA_WICK_QUALITY_TF", "OFF", "RSI_FILTER_TF=4h", 0.0, cum, cached=True))
    L.append({"ts": t(2), "sym_side": sym, "sheet": "ENTRY_REVERSAL_BOUNCE", "row": 3, "switch": "HA_WICK_QUALITY_TF", "cand": "OFF", "label": "ROW_DONE", "promoted": False, "cum_after": cum})
    # row 4: switch with a positive naked -> promoted; one yellow rescues nothing, one kills
    alt = {"label_extra": None}
    e = _ev(t(3), sym, "EXIT_VELOCITY", 4, "EXIT_VELOCITY_WT_MIN_TFS", "2", "naked", 1.5, cum)
    if brain_p is not None:
        e["brain_p"] = brain_p
    L.append(e)
    L.append(_ev(t(3), sym, "EXIT_VELOCITY", 4, "EXIT_VELOCITY_WT_MIN_TFS", "2", "EMA_9_21_FILTER_FILTER_TF=OFF", 2.5, cum))
    L.append(_ev(t(3), sym, "EXIT_VELOCITY", 4, "EXIT_VELOCITY_WT_MIN_TFS", "2", "RSI_FILTER_TF=4h", 0.5, cum))
    L.append({"ts": t(3), "sym_side": sym, "sheet": "EXIT_VELOCITY", "row": 4, "switch": "EXIT_VELOCITY_WT_MIN_TFS", "cand": "2", "label": "ROW_DONE", "promoted": True, "cum_after": cum + 2.5})
    # row 5: a different cand of the same switch (forward observation on the next board)
    L.append(_ev(t(4), sym, "EXIT_VELOCITY", 5, "EXIT_VELOCITY_WT_MIN_TFS", "3", "naked", (0.7 if alt_pos else -0.3), cum + 2.5))
    L.append(_ev(t(4), sym, "EXIT_VELOCITY", 5, "EXIT_VELOCITY_WT_MIN_TFS", "3", "RSI_FILTER_TF=4h", 0.2, cum + 2.5))
    L.append({"ts": t(4), "sym_side": sym, "sheet": "EXIT_VELOCITY", "row": 5, "switch": "EXIT_VELOCITY_WT_MIN_TFS", "cand": "3", "label": "ROW_DONE", "promoted": False, "cum_after": cum + 2.5})
    # row 6: non-positive naked rescued by a filter option
    L.append(_ev(t(5), sym, "ENTRY_BREAKOUT_CHANNEL", 6, "DC_BREAK_FILTER_TF", "D", "naked", -1.0, cum + 2.5))
    L.append(_ev(t(5), sym, "ENTRY_BREAKOUT_CHANNEL", 6, "DC_BREAK_FILTER_TF", "D", "EMA_9_21_FILTER_FILTER_TF=OFF", 0.8, cum + 2.5))
    L.append({"ts": t(5), "sym_side": sym, "sheet": "ENTRY_BREAKOUT_CHANNEL", "row": 6, "switch": "DC_BREAK_FILTER_TF", "cand": "D", "label": "ROW_DONE", "promoted": False, "cum_after": cum + 2.5})
    return L


@pytest.fixture()
def corpus(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    lines = []
    for day, alt in ((14, False), (15, True), (16, False), (17, False)):
        lines += _board_lines("BTCUSDT_LONG", day, 10.0, alt_pos=alt, brain_p=0.25 if day == 16 else None)
    lines.append({"ts": "2026-10-17T05:00:00Z", "sym_side": "BTCUSDT_LONG", "label": "FORWARD", "key": "EXIT_VELOCITY_WT_MIN_TFS=2", "d_gain": 0.4, "d_trades": 1})
    lines.append({"ts": "2026-10-17T05:01:00Z", "sym_side": "BTCUSDT_LONG", "label": "HOLDOUT", "final_minus_start": 0.3, "days": 7})
    (logs / "BTCUSDT_LONG_jump.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    lines2 = []
    for day in (14, 15, 16):
        lines2 += _board_lines("AAPL_SHORT", day, 5.0)
    (logs / "AAPL_SHORT_jump.jsonl").write_text("\n".join(json.dumps(x) for x in lines2) + "\n" + '{"ts": "2026-10-16T09:00:00Z", "label": "naked"')  # partial last line
    # rebuilt-style sqlite: src A exists (covered by its log), src B is gone (its rows are used)
    db = tmp_path / "rebuilt.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE evals_compact (ek TEXT PRIMARY KEY, base_id TEXT NOT NULL, symside TEXT, created REAL, switch TEXT, cand TEXT, filter_key TEXT, filter_val TEXT, ctx TEXT, metrics TEXT, prov TEXT)")
    k = 0
    for src, sym in ((str(logs / "BTCUSDT_LONG_jump.jsonl"), "BTCUSDT_LONG"), ("/nonexistent/ETHUSDT_SHORT_jump.jsonl", "ETHUSDT_SHORT")):
        for ln, r in enumerate(_board_lines(sym, 13, 3.0), start=1):
            if r.get("label") in ("BOARD_CTX", "ROW_DONE"):
                continue
            k += 1
            lab = r["label"]
            fk, fv = (lab.split("=", 1) if "=" in lab else (None, None))
            m = {"gain_pct": r["gain_pct"], "trades": r["trades"], "tim_pct": r["tim"], "max_dd_pct": r["max_dd_pct"], "pool_sharpe": r["pool_sharpe"],
                 "wr_pct": r["wr_pct"], "valid": r["valid"], "behavior_fingerprint": r["fp"]}
            pv = {"src": src, "line": ln, "ts": r["ts"], "label": lab, "sheet": r["sheet"], "row": r["row"], "cum_before": r["cum_before"], "delta": r["delta"],
                  "secs": r["secs"], "cached_when_logged": r["cached"]}
            ctx = {"window_days": 30, "bh_pct": -12.0, "tier": "LOSER", "round": "r13"}
            con.execute("INSERT INTO evals_compact VALUES (?,?,?,?,?,?,?,?,?,?,?)", (f"rebuilt1:{k}", "b", sym, 0.0, r["switch"], r["cand"], fk, fv,
                                                                                      json.dumps(ctx), json.dumps(m), json.dumps(pv)))
    con.commit()
    con.close()
    spill = tmp_path / "spill"
    assert B.main(["extract", "--source", "rebuilt", "--from", str(db), "--host", "t", "--spill-dir", str(spill)]) == 0
    assert B.main(["extract", "--source", "logs", "--from", str(logs / "*.jsonl"), "--host", "t", "--spill-dir", str(spill),
                   "--ctx-map", str(spill / "ctxmap_t.json")]) == 0
    # second extract of unchanged files emits nothing new (watermark)
    assert B.main(["extract", "--source", "logs", "--from", str(logs / "*.jsonl"), "--host", "t", "--spill-dir", str(spill)]) == 0
    out = tmp_path / "brain"
    state = tmp_path / "state.pkl"
    rep = tmp_path / "report.json"
    assert B.main(["train", "--spill-dir", str(spill), "--cut", "2026-10-01T00:00:00Z", "--state", str(state), "--out-dir", str(out), "--report", str(rep),
                   "--template-cache", str(tmp_path / "none"), "--keep-sorted"]) == 0
    return {"tmp": tmp_path, "spill": spill, "state": B.load_state(state), "out": out, "report": json.loads(rep.read_text()), "state_path": state}


def _edge(sb, scope, a, b=""):
    V = B.get_view(sb)
    sid = V.sid.get(scope)
    _i, E = V.row(sid, a, b)
    return E


def test_cat_sides_strictly_apart(corpus):
    st = corpus["state"]
    cl = st["brains"]["CRYPTO_LONG"]
    ss = st["brains"]["STOCKS_SHORT"]
    cs_ = st["brains"]["CRYPTO_SHORT"]
    assert set(cl["scopes"][1:]) == {"BTCUSDT_LONG"}
    assert set(ss["scopes"][1:]) == {"AAPL_SHORT"}
    assert set(cs_["scopes"][1:]) == {"ETHUSDT_SHORT"}  # the rebuilt-only src lands in its own cat_side
    assert "GLOBAL" not in cl["scopes"]
    for cs, sb in st["brains"].items():
        for s in sb["scopes"][1:]:
            assert B.cat_side_of(s) == cs
    with pytest.raises(ValueError):
        B.PilotBrainV18(corpus["out"] / "v18_brain_CRYPTO_LONG.json", "AAPL_LONG")


def test_identity_lines_never_evidence(corpus):
    sb = corpus["state"]["brains"]["CRYPTO_LONG"]
    E = _edge(sb, "CS", "R:HA_WICK_QUALITY_TF=OFF")
    assert E is not None
    assert E[B.C["n"]] == 0  # naked was cached on every board -> no evidence
    assert E[B.C["n_ident"]] == 4  # one settlement per row visit (4 boards)
    assert sb["c"]["identity_lines"] == 8  # 2 cached lines per visit, counted once as settlement
    Ef = _edge(sb, "CS", "F:RSI_FILTER_TF=4h", "R:HA_WICK_QUALITY_TF=OFF")
    assert Ef is None or Ef[B.C["n"]] == 0  # the cached yellow is not a cell observation


def test_rebuilt_rows_covered_by_log_are_not_double_counted(corpus):
    reps = [json.loads(p.read_text()) for p in corpus["spill"].glob("extract_rebuilt_*.json")]
    c = reps[0]["counters"]
    assert c["rebuilt_rows_covered_by_log"] > 0 and c["rebuilt_rows_used"] > 0
    sb = corpus["state"]["brains"]["CRYPTO_LONG"]
    E = _edge(sb, "CS", "R:EXIT_VELOCITY_WT_MIN_TFS=2")
    assert E[B.C["n"]] == 4  # the 4 logged boards only; the 13th-day rebuilt copy of this src is covered by the log


def test_floor_is_a_guarantee():
    for fl in (0.0, -5.0, 0.2, 1.0, 5.0):
        pe = B.p_eval(1e-12, 0.002, fl, 100)
        assert pe >= B.HARD_MIN_FLOOR / 1000.0 - 1e-15
        assert pe >= min(1.0, max(fl, B.HARD_MIN_FLOOR) / 1000.0) - 1e-15
    assert B.p_eval(0.0, 0.002, 5.0, 1) == 1.0  # unevidenced -> always computed


def test_pilot_runtime_floor_and_propensity(corpus):
    pb = B.PilotBrainV18(corpus["out"] / "v18_brain_CRYPTO_LONG.json", "BTCUSDT_LONG", floor_per_1000=0.0)
    assert pb.floor >= B.HARD_MIN_FLOOR
    for rnd in range(30):
        d = pb.decide("EXIT_VELOCITY", "EXIT_VELOCITY_WT_MIN_TFS=2", None, f"r{rnd}")
        assert d["p"] >= B.HARD_MIN_FLOOR / 1000.0 and 0.0 <= d["u"] < 1.0 and "p_eval" in d["why"]
        c = pb.decide("EXIT_VELOCITY", "EXIT_VELOCITY_WT_MIN_TFS=2", "RSI_FILTER_TF=4h", f"r{rnd}")
        assert c["p"] >= B.HARD_MIN_FLOOR / 1000.0
    assert sum(pb.booked.values()) == 60
    rv = pb.decide("EXIT_VELOCITY", "X=1", "RSI_FILTER_TF=4h", "r", naked_d=0.5)
    assert rv["keep"] and rv["p"] == 1.0


def test_propensity_weights_are_booked(corpus):
    sb = corpus["state"]["brains"]["CRYPTO_LONG"]
    E = _edge(sb, "CS", "R:EXIT_VELOCITY_WT_MIN_TFS=2")
    assert E[B.C["n"]] == 4  # raw count stays the real number of evals (NO-LIES)
    assert abs(E[B.C["wn"]] - 7.0) < 1e-9  # 3 x w=1 + 1 x 1/0.25
    assert abs(E[B.C["wpos"]] - 7.0) < 1e-9


def test_prequential_order_and_forward(corpus):
    sb = corpus["state"]["brains"]["CRYPTO_LONG"]
    cap = sb["capture"]["R"]
    assert cap["n"] > 0 and cap["evidenced_share"] < 1.0  # the first evals were scored before any evidence existed
    fwd = sb["c"]
    assert fwd["fwd_labels"] >= 2 and fwd["promotions_rowdone"] == 4
    E = _edge(sb, "CS", "R:EXIT_VELOCITY_WT_MIN_TFS=2")
    # board 14 promotion observed on 15 (alt positive -> 0), 15 on 16 (alt negative -> 1), 16 on 17 (-> 1); + the FORWARD line (d>0 -> 1)
    assert E[B.C["fwd_n"]] == 4 and E[B.C["fwd_pos"]] == 3
    assert sb["c"]["holdout_lines"] == 1
    assert sb["c"]["synergy_quartets"] >= 1
    P = _edge(sb, "CS", "F:EMA_9_21_FILTER_FILTER_TF=OFF", "R:DC_BREAK_FILTER_TF=D")
    assert P[B.C["n_rescue"]] == 4
    K = _edge(sb, "CS", "F:RSI_FILTER_TF=4h", "R:EXIT_VELOCITY_WT_MIN_TFS=2")
    assert K[B.C["n_kill"]] == 4


def test_export_size_and_schema(corpus):
    for cs in ("CRYPTO_LONG", "STOCKS_SHORT", "CRYPTO_SHORT"):
        p = corpus["out"] / f"v18_brain_{cs}.json"
        assert p.exists() and p.stat().st_size <= B.MAX_EXPORT_BYTES
        blob = json.loads(p.read_text())
        assert blob["_meta"]["schema"] == B.SCHEMA and blob["_meta"]["cat_side"] == cs
        for k in ("nodes", "heads", "cs", "regime", "pair", "sym", "coverage", "exits", "prequential", "forward_eval"):
            assert k in blob
        assert set(blob["heads"]) >= {"R", "F", "F0", "FWD", "dd:R", "wr:F0"}
        assert all(len(w) == (B.NF + B.NBF if k in B.BIND_HEADS else B.NF) for k, w in blob["heads"].items())
        assert set(B.BIND_HEADS) <= set(blob["heads"])
        assert all(s.endswith(("_LONG", "_SHORT")) and B.cat_side_of(s) == cs for s in blob["sym"])
    full, ftxt = B.export_one(corpus["state"]["brains"]["CRYPTO_LONG"], None, 5.0, 0.001, 0.002)
    blob, txt = B.export_one(corpus["state"]["brains"]["CRYPTO_LONG"], None, 5.0, 0.001, 0.002, max_bytes=len(ftxt) - 200, min_keep=1)
    assert len(txt) < len(ftxt) and blob["_meta"]["trimmed"]


def test_explain_and_suggest(corpus, capsys):
    sb = corpus["state"]["brains"]["CRYPTO_LONG"]
    ex = B.explain_switch(sb, "DC_BREAK_FILTER_TF")
    assert ex["suffocating_filters"] and ex["suffocating_filters"][0]["n_rescue"] == 4
    fam = B.explain_family(sb, "EXIT_VELOCITY_WT")
    assert "EXIT_VELOCITY_WT_MIN_TFS" in fam["switches_by_name"]
    assert B.main(["suggest", "--cat-side", "CRYPTO_LONG", "--metric", "gain", "--model", str(corpus["out"] / "v18_brain_CRYPTO_LONG.json"), "--min-n-sym", "1"]) == 0
    out = capsys.readouterr().out
    assert "R:EXIT_VELOCITY_WT_MIN_TFS=2" in out
    assert B.main(["suggest", "--cat-side", "CRYPTO_LONG", "--metric", "forward", "--model", str(corpus["out"] / "v18_brain_CRYPTO_LONG.json"), "--min-n-sym", "50"]) == 0
    assert "no evidence" in capsys.readouterr().out
    assert B.main(["suggest", "--cat-side", "CRYPTO_LONG", "--sym-side", "AAPL_LONG", "--model", str(corpus["out"] / "v18_brain_CRYPTO_LONG.json")]) == 2


def test_compare_verdict_only(corpus):
    p = corpus["out"] / "v18_brain_CRYPTO_LONG.json"
    srt = corpus["spill"] / "tmp" / "events_sorted.tsv"
    v = B.compare_models(p, p, srt, days=10000, min_pos=1)
    assert v["winner"] in ("tie", "insufficient") and v["promote"] is False


def test_load_pilot_brain_env(corpus):
    assert B.load_pilot_brain("BTCUSDT_LONG", env={}) is None
    pb = B.load_pilot_brain("BTCUSDT_LONG", env={"V18_BRAIN": "1", "V18_BRAIN_DIR": str(corpus["out"])})
    assert pb is not None and pb.cs == "CRYPTO_LONG"
    assert B.load_pilot_brain("NOPE", env={"V18_BRAIN": "1", "V18_BRAIN_DIR": str(corpus["out"])}) is None
