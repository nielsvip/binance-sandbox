"""Entry-ports-A live-twin parity (wiring mandate 2026-10-04).

Proves vec_decisions.twin_entry_ports_a reproduces the v12_quick_engine
signal cores exactly (BACKTEST_BIBLE _43 scalar parity):
  B_BOUNCE_DEEP_TURN  v12:8440-8449  (deep + turn + |px-dc|/dc <= dist)
  B_BOUNCE_DONCHIAN   v12:8458-8465  (near channel + recovery k-zone)
  B_STOCH_HHHL_DIRECT v12:8517-8536  (per-TF hh/hl + zone + turn, votes>=req)
  B_STOCH_PARENT_DIRECT v12:8545-8569 (1h turn zone+turn / 4h deep zone)
plus agreement with the stocks-live oracles where they coincide, and
pins the three known stocks-live divergences as documented (not copied):
  D1 tradier_manage.py:13154/13158 bounce OR-clause deletes the bounce leg
  D2 bounce_donchian_contract.py:174 signed proximity (no lower bound LONG)
  D3 stoch contracts add episode-start/new-parent pulse gating
"""
import json
import random

import vec_decisions.twin_entry_ports_a as T
from stoch_hhhl_contract import CompletedParentSnapshot as _HHHLParent
from stoch_hhhl_contract import evaluate_stoch_hhhl_direct as _hhhl_eval
from stoch_parent_contract import CompletedStochParent as _StochParent
from stoch_parent_contract import evaluate_completed_stoch_direct as _parent_eval
from bounce_donchian_contract import BounceInputs as _BounceInputs
from bounce_donchian_contract import CausalScalar as _BounceScalar
from bounce_donchian_contract import evaluate_bounce_donchian_direct as _bounce_eval


def _get(d):
    return lambda k, default: d.get(k, default)


# ── 1. inert by default ──────────────────────────────────────────────

def test_inert_defaults():
    ind = {"k_4h": 10.0, "k_1h": 10.0, "k_1h_prev": 5.0, "dc_low_3m": 100.0,
           "high_1h": 2.0, "high_1h_prev": 1.0, "low_1h": 2.0, "low_1h_prev": 1.0,
           "d_1h": 5.0}
    g = _get({})
    assert T.evaluate_bounce_deep_turn(g, "BTCUSDC", "LONG", ind, 100.0) == (False, "")
    assert T.evaluate_bounce_donchian(g, "BTCUSDC", "LONG", ind, 100.0) == (False, "")
    assert T.evaluate_stoch_hhhl(g, "BTCUSDC", "LONG", ind) == (False, "")
    assert T.evaluate_stoch_parent(g, "BTCUSDC", "LONG", ind) == (False, "")


def test_fires_when_enabled():
    ind = {"k_4h": 10.0, "k_1h": 10.0, "k_1h_prev": 5.0, "d_1h": 5.0,
           "dc_low_3m": 100.0, "dc_high_3m": 110.0,
           "high_1h": 2.0, "high_1h_prev": 1.0, "low_1h": 2.0, "low_1h_prev": 1.0,
           "k_3m": 30.0}
    g = _get({"ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED": True,
              "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SYMBOLS": (),
              "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SIDE": "BOTH",
              "ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED": True,
              "ENTRY_STOCH_HHHL_DIRECT_ENABLED": True,
              "ENTRY_STOCH_PARENT_DIRECT_ENABLED": True})
    fire, reason = T.evaluate_bounce_deep_turn(g, "BTCUSDC", "LONG", ind, 100.5)
    assert fire and reason.startswith("ENTRY_BOUNCE_DEEP_TURN_V1_L_")
    fire, reason = T.evaluate_bounce_donchian(g, "BTCUSDC", "LONG", ind, 100.5)
    assert fire and reason.startswith("ENTRY_BOUNCE_DONCHIAN_L_")
    fire, reason = T.evaluate_stoch_hhhl(g, "BTCUSDC", "LONG", ind)
    assert fire and reason == "STOCH_HHHL_DIRECT_1h"
    fire, reason = T.evaluate_stoch_parent(g, "BTCUSDC", "LONG", ind)
    assert fire and reason == "STOCH_PARENT_DIRECT_ENTRY_1H_TURN_UP"


# ── 2. boundaries (exact operators from the vec lines) ───────────────

def test_deep_turn_boundaries():
    base = {"k_4h": 49.9, "k_1h": 39.9, "k_1h_prev": 39.8, "dc_low_15m": 100.0}
    assert T.bounce_deep_turn_signal(base, True, bounce_timeframe="15m", price=101.5)[0] is True
    assert T.bounce_deep_turn_signal({**base, "k_4h": 50.0}, True, bounce_timeframe="15m", price=101.5)[0] is False
    assert T.bounce_deep_turn_signal({**base, "k_1h": 40.0}, True, bounce_timeframe="15m", price=101.5)[0] is False
    assert T.bounce_deep_turn_signal({**base, "k_1h_prev": 39.9}, True, bounce_timeframe="15m", price=101.5)[0] is False
    assert T.bounce_deep_turn_signal(base, True, bounce_timeframe="15m", price=101.5)[0] is True
    assert T.bounce_deep_turn_signal(base, True, bounce_timeframe="15m", price=101.51)[0] is False
    sbase = {"k_4h": 50.1, "k_1h": 60.1, "k_1h_prev": 60.2, "dc_high_15m": 100.0}
    assert T.bounce_deep_turn_signal(sbase, False, bounce_timeframe="15m", price=98.5)[0] is True
    assert T.bounce_deep_turn_signal({**sbase, "k_4h": 50.0}, False, bounce_timeframe="15m", price=98.5)[0] is False
    assert T.bounce_deep_turn_signal({**sbase, "k_1h": 60.0}, False, bounce_timeframe="15m", price=98.5)[0] is False


def test_donchian_boundaries():
    assert T.bounce_donchian_signal({"dc_low_15m": 100.0, "k_15m": 30.0}, True, timeframe="15m", price=100.8)[0] is True
    assert T.bounce_donchian_signal({"dc_low_15m": 100.0, "k_15m": 30.0}, True, timeframe="15m", price=100.81)[0] is False
    assert T.bounce_donchian_signal({"dc_low_15m": 100.0, "k_15m": 30.0}, True, timeframe="15m", recovery_only=True, price=100.8)[0] is True
    assert T.bounce_donchian_signal({"dc_low_15m": 100.0, "k_15m": 50.0}, True, timeframe="15m", recovery_only=True, price=100.8)[0] is False
    assert T.bounce_donchian_signal({"dc_low_15m": 100.0, "k_15m": 20.0}, True, timeframe="15m", recovery_only=True, price=100.8)[0] is False
    assert T.bounce_donchian_signal({"dc_high_15m": 100.0, "k_15m": 60.0}, False, timeframe="15m", recovery_only=True, price=99.2)[0] is True
    assert T.bounce_donchian_signal({"dc_high_15m": 100.0, "k_15m": 80.0}, False, timeframe="15m", recovery_only=True, price=99.2)[0] is False
    assert T.bounce_donchian_signal({"dc_high_15m": 100.0, "k_15m": 50.0}, False, timeframe="15m", recovery_only=True, price=99.2)[0] is False


def test_hhhl_boundaries():
    good = {"high_1h": 2.0, "high_1h_prev": 1.0, "low_1h": 2.0, "low_1h_prev": 1.0, "k_1h": 20.0, "k_1h_prev": 19.0}
    assert T.stoch_hhhl_signal(good, True)[0] is True
    assert T.stoch_hhhl_signal({**good, "k_1h": 20.01}, True)[0] is False
    assert T.stoch_hhhl_signal({**good, "k_1h_prev": 20.0}, True)[0] is False
    assert T.stoch_hhhl_signal({**good, "high_1h": 1.0}, True)[0] is False
    assert T.stoch_hhhl_signal(good, True, min_confirming_tfs=2)[0] is False
    assert T.stoch_hhhl_signal(good, True, tfs=("15m",))[0] is False
    sgood = {"high_1h": 1.0, "high_1h_prev": 2.0, "low_1h": 1.0, "low_1h_prev": 2.0, "k_1h": 80.0, "k_1h_prev": 81.0}
    assert T.stoch_hhhl_signal(sgood, False)[0] is True
    assert T.stoch_hhhl_signal({**sgood, "k_1h": 79.99}, False)[0] is False


def test_parent_boundaries():
    assert T.stoch_parent_signal({"k_1h": 39.9, "k_1h_prev": 39.8, "d_1h": 50.0}, True)[0] is True
    assert T.stoch_parent_signal({"k_1h": 40.0, "k_1h_prev": 39.8, "d_1h": 50.0}, True)[0] is False
    assert T.stoch_parent_signal({"k_1h": 39.9, "k_1h_prev": 39.9, "d_1h": 50.0}, True)[0] is False
    assert T.stoch_parent_signal({"k_1h": 39.9, "k_1h_prev": 39.9, "d_1h": 30.0}, True, turn_definition="cross-d")[0] is True
    assert T.stoch_parent_signal({"k_1h": 39.9, "k_1h_prev": 39.9, "d_1h": 39.9}, True, turn_definition="cross-d")[0] is False
    assert T.stoch_parent_signal({"k_1h": 39.9, "k_1h_prev": 39.9, "d_1h": 30.0}, True, turn_definition="either")[0] is True
    assert T.stoch_parent_signal({"k_1h": 39.9, "k_1h_prev": 39.9, "d_1h": 39.9}, True, turn_definition="either")[0] is False
    assert T.stoch_parent_signal({"k_4h": 39.9}, True, family="ENTRY_4H_DEEP_VALUE")[0] is True
    assert T.stoch_parent_signal({"k_4h": 40.0}, True, family="ENTRY_4H_DEEP_VALUE")[0] is False
    assert T.stoch_parent_signal({"k_4h": 60.1}, False, family="ENTRY_4H_DEEP_VALUE")[0] is True
    assert T.stoch_parent_signal({"k_1h": 10.0}, True, family="NOPE")[0] is False


# ── 3. vec-formula agreement (inline transcription of the vec lines) ─

def _vec_deep_turn(is_long, k4, k1, k1p, dc, px, deep, turn, dist):
    if is_long:
        return (k4 < 100 - deep) and (k1 > k1p) and (k1 < turn) and dc > 0 and abs(px - dc) / max(dc, 1e-9) <= dist
    return (k4 > deep) and (k1 < k1p) and (k1 > 100 - turn) and dc > 0 and abs(dc - px) / max(dc, 1e-9) <= dist


def _vec_donchian(is_long, dc, px, dist, recov, k):
    if is_long:
        return dc > 0 and abs(px - dc) / max(dc, 1e-9) <= dist and ((k > 20 and k < 50) if recov else True)
    return dc > 0 and abs(dc - px) / max(dc, 1e-9) <= dist and ((k < 80 and k > 50) if recov else True)


def _vec_hhhl_tf(is_long, hi, hip, lo, lop, k, kp, thr):
    if is_long:
        return hi > hip and lo > lop and k <= thr and k > kp
    return hi < hip and lo < lop and k >= 100.0 - thr and k < kp


def _vec_parent(is_long, fam, k, kp, d, k4, th, tdef):
    if fam == "ENTRY_1H_TURN_UP":
        if is_long:
            zone, rising, cross = k < th, k > kp, k > d
        else:
            zone, rising, cross = k > 100.0 - th, k < kp, k < d
        if tdef == "rising-vs-prior":
            return zone and rising
        if tdef == "cross-d":
            return zone and cross
        return (zone and rising) or (zone and cross)
    if fam == "ENTRY_4H_DEEP_VALUE":
        return k4 < th if is_long else k4 > 100.0 - th
    return False


def test_vec_agreement_grid():
    rng = random.Random(11)
    for is_long in (True, False):
        for _ in range(400):
            k4, k1, k1p = (rng.uniform(0, 100) for _ in range(3))
            d1 = rng.uniform(0, 100)
            dc = rng.uniform(50, 150)
            px = rng.uniform(50, 150)
            deep = rng.choice([30.0, 50.0, 65.0])
            turn = rng.choice([20.0, 40.0, 60.0])
            dist = rng.choice([0.008, 0.015, 0.03])
            leg = "dc_low_15m" if is_long else "dc_high_15m"
            ind = {"k_4h": k4, "k_1h": k1, "k_1h_prev": k1p, "d_1h": d1, leg: dc}
            exp = _vec_deep_turn(is_long, k4, k1, k1p, dc, px, deep, turn, dist)
            got = T.bounce_deep_turn_signal(ind, is_long, bounce_timeframe="15m", bounce_distance=dist, deep_k4h=deep, turn_k1h=turn, price=px)[0]
            assert got == exp, ("deep_turn", is_long, k4, k1, k1p, dc, px)
            recov = rng.random() < 0.5
            kk = rng.uniform(0, 100)
            ind2 = {leg: dc, "k_15m": kk}
            exp2 = _vec_donchian(is_long, dc, px, dist, recov, kk)
            got2 = T.bounce_donchian_signal(ind2, is_long, timeframe="15m", distance=dist, recovery_only=recov, price=px)[0]
            assert got2 == exp2, ("donchian", is_long, dc, px, recov, kk)
            thr = rng.choice([10.0, 20.0, 30.0])
            tfs = rng.choice([("1h",), ("1h", "4h"), ("4h", "D"), ("1h", "4h", "D")])
            req = rng.randint(1, len(tfs))
            ind3, votes = {}, 0
            for tf in tfs:
                hi, hip, lo, lop = (rng.uniform(50, 150) for _ in range(4))
                k, kp = rng.uniform(0, 100), rng.uniform(0, 100)
                ind3.update({f"high_{tf}": hi, f"high_{tf}_prev": hip, f"low_{tf}": lo, f"low_{tf}_prev": lop, f"k_{tf}": k, f"k_{tf}_prev": kp})
                votes += bool(_vec_hhhl_tf(is_long, hi, hip, lo, lop, k, kp, thr))
            assert T.stoch_hhhl_signal(ind3, is_long, tfs=tfs, min_confirming_tfs=req, stoch_threshold=thr)[0] == (votes >= req)
            fam = rng.choice(["ENTRY_1H_TURN_UP", "ENTRY_4H_DEEP_VALUE"])
            th = rng.choice([20.0, 40.0, 60.0])
            tdef = rng.choice(["rising-vs-prior", "cross-d", "either"])
            ind4 = {"k_1h": k1, "k_1h_prev": k1p, "d_1h": d1, "k_4h": k4}
            assert T.stoch_parent_signal(ind4, is_long, family=fam, threshold=th, turn_definition=tdef)[0] == _vec_parent(is_long, fam, k1, k1p, d1, k4, th, tdef)


# ── 4. stocks-live oracle agreement + pinned divergences ─────────────

def test_stocks_donchian_process_position_agreement():
    rng = random.Random(23)
    for is_long in (True, False):
        for _ in range(200):
            dc, px = rng.uniform(50, 150), rng.uniform(50, 150)
            dist = rng.choice([0.008, 0.02])
            recov = rng.random() < 0.5
            kk = rng.uniform(0, 100)
            leg = "dc_low_15m" if is_long else "dc_high_15m"
            ind = {leg: dc, f"close_15m": px, "k_15m": kk}
            fire = T.bounce_donchian_signal(ind, is_long, timeframe="15m", distance=dist, recovery_only=recov, price=px)[0]
            near = dc > 0 and abs(px - dc) / max(dc, 1e-9) <= dist
            if is_long:
                exp = near and ((not recov) or (kk > 20 and kk < 50))
            else:
                exp = near and ((not recov) or (kk < 80 and kk > 50))
            assert fire == exp


def test_stocks_deep_turn_divergence_pinned():
    ind = {"k_4h": 10.0, "k_1h": 10.0, "k_1h_prev": 5.0, "dc_low_15m": 100.0, "low_15m": 90.0}
    fire, _ = T.bounce_deep_turn_signal(ind, True, bounce_timeframe="15m", price=50.0)
    assert fire is False
    stocks_bounce_ok = (100.0 > 0 and (50.0 - 100.0) / 100.0 <= 0.015) or (50.0 > 90.0)
    assert stocks_bounce_ok is True
    ind2 = {"k_4h": 10.0, "k_1h": 10.0, "k_1h_prev": 5.0, "dc_low_15m": 100.0, "low_15m": 90.0}
    fire2, _ = T.bounce_deep_turn_signal(ind2, True, bounce_timeframe="15m", price=110.0)
    assert fire2 is False
    stocks_bounce_ok2 = (100.0 > 0 and (110.0 - 100.0) / 100.0 <= 0.015) or (110.0 > 90.0)
    assert stocks_bounce_ok2 is True


def test_contract_hhhl_eligible_agreement():
    rng = random.Random(31)
    for is_long in (True, False):
        side = "LONG" if is_long else "SHORT"
        for _ in range(100):
            tfs = rng.choice([("1h",), ("1h", "4h"), ("1h", "4h", "D")])
            req = rng.randint(1, len(tfs))
            thr = rng.choice([10.0, 20.0, 30.0])
            ind, snaps = {}, {}
            for tf in tfs:
                hi, hip, lo, lop = (rng.uniform(50, 150) for _ in range(4))
                k, kp = rng.uniform(0, 100), rng.uniform(0, 100)
                ind.update({f"high_{tf}": hi, f"high_{tf}_prev": hip, f"low_{tf}": lo, f"low_{tf}_prev": lop, f"k_{tf}": k, f"k_{tf}_prev": kp})
                snaps[tf] = _HHHLParent(timeframe=tf, source_close_ts=1000, high=hi, high_prev=hip, low=lo, low_prev=lop, stoch_k=k, stoch_k_prev=kp)
            dec = _hhhl_eval(snaps, side=side, enabled_tfs=tfs, min_confirming_tfs=req, stoch_threshold=thr, asof_ts=2000, prior_state={})
            assert T.stoch_hhhl_signal(ind, is_long, tfs=tfs, min_confirming_tfs=req, stoch_threshold=thr)[0] == dec.eligible


def test_contract_parent_eligible_agreement():
    rng = random.Random(37)
    for is_long in (True, False):
        side = "LONG" if is_long else "SHORT"
        for _ in range(100):
            fam = rng.choice(["ENTRY_1H_TURN_UP", "ENTRY_4H_DEEP_VALUE"])
            th = rng.choice([20.0, 40.0, 60.0, 80.0]) if fam == "ENTRY_1H_TURN_UP" else rng.choice([20.0, 35.0, 50.0, 65.0])
            tdef = rng.choice(["rising-vs-prior", "cross-d", "either"])
            k, kp, d, k4 = (rng.uniform(0, 100) for _ in range(4))
            tf = "1h" if fam == "ENTRY_1H_TURN_UP" else "4h"
            parent = _StochParent(timeframe=tf, source_close_ts=1000, stoch_k=k if tf == "1h" else k4, stoch_d=d, stoch_k_prev=kp, previous_source_close_ts=900)
            dec = _parent_eval(parent, family=fam, side=side, stoch_k_threshold=th, turn_definition=(tdef if fam == "ENTRY_1H_TURN_UP" else None), asof_ts=2000, prior_state={})
            ind = {"k_1h": k, "k_1h_prev": kp, "d_1h": d, "k_4h": k4}
            assert T.stoch_parent_signal(ind, is_long, family=fam, threshold=th, turn_definition=tdef)[0] == dec.eligible, (fam, th, tdef)


def test_contract_bounce_divergence_pinned():
    mk = lambda px, ch: _bounce_eval(_BounceInputs(price=_BounceScalar(px, 1000), prior_channel=_BounceScalar(ch, 1000)), side="LONG", timeframe="15m", distance=0.008, recovery_only=False, confirmation="none", asof_ts=2000, prior_state={})
    assert mk(100.5, 100.0).eligible is True
    assert T.bounce_donchian_signal({"dc_low_15m": 100.0}, True, timeframe="15m", price=100.5)[0] is True
    assert mk(50.0, 100.0).eligible is True
    assert T.bounce_donchian_signal({"dc_low_15m": 100.0}, False, timeframe="15m", price=50.0)[0] is False
    assert T.bounce_donchian_signal({"dc_low_15m": 100.0}, True, timeframe="15m", price=50.0)[0] is False


# ── 5. fail-open + alias + scoping ───────────────────────────────────

def test_fail_open():
    junk = {"k_4h": "xx", "k_1h": None, "dc_low_3m": float("nan"), "high_1h": "yy"}
    assert T.bounce_deep_turn_signal(junk, True, price=float("nan"))[0] is False
    assert T.bounce_deep_turn_signal(None, True)[0] is False
    assert T.bounce_donchian_signal(junk, True, timeframe="OFF")[0] is False
    assert T.bounce_donchian_signal(None, False)[0] is False
    assert T.stoch_hhhl_signal(None, True)[0] is False
    assert T.stoch_hhhl_signal({}, True, tfs="1h")[0] is False
    assert T.stoch_parent_signal(None, True)[0] is False
    assert T.stoch_parent_signal({}, True, threshold="xx")[0] is False
    assert T.stoch_parent_signal({"k_1h": 10.0, "k_1h_prev": 5.0, "d_1h": 5.0}, True)[0] is True
    assert T.stoch_parent_signal({}, True, family="ENTRY_4H_DEEP_VALUE", threshold=65.0)[0] is False
    boom = lambda k, d: (_ for _ in ()).throw(RuntimeError("x"))
    assert T.evaluate_bounce_deep_turn(boom, "S", "LONG", {}, 1.0) == (False, "")
    assert T.evaluate_bounce_donchian(boom, "S", "LONG", {}, 1.0) == (False, "")
    assert T.evaluate_stoch_hhhl(boom, "S", "LONG", {}) == (False, "")
    assert T.evaluate_stoch_parent(boom, "S", "LONG", {}) == (False, "")


def test_five_minute_alias_to_3m():
    ind = {"k_4h": 10.0, "k_1h": 10.0, "k_1h_prev": 5.0, "dc_low_3m": 100.0}
    assert T.bounce_deep_turn_signal(ind, True, bounce_timeframe="5m", price=100.5)[0] is True
    assert T.bounce_deep_turn_signal(ind, True, bounce_timeframe="OFF", price=100.5)[0] is False
    assert T.bounce_donchian_signal({"dc_low_3m": 100.0}, True, timeframe="5m", price=100.5)[0] is True


def test_sym_side_scoping():
    assert T.sym_side_allowed("BTCUSDC", "LONG", (), "BOTH") is True
    assert T.sym_side_allowed("BTCUSDC", "LONG", ("WDAY",), "SHORT") is False
    assert T.sym_side_allowed("WDAY", "SHORT", ("WDAY",), "SHORT") is True
    assert T.sym_side_allowed("BTCUSDC", "SHORT", (), "SHORT") is True
    assert T.sym_side_allowed("BTCUSDC", "LONG", (), "SHORT") is False
    g = _get({"ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED": True})
    ind = {"k_4h": 10.0, "k_1h": 10.0, "k_1h_prev": 5.0, "dc_low_3m": 100.0}
    assert T.evaluate_bounce_deep_turn(g, "BTCUSDC", "LONG", ind, 100.5) == (False, "")


# ── 6. hook spec self-validation ─────────────────────────────────────

def test_hook_spec_anchors_unique_and_code_compiles():
    import textwrap
    spec = json.load(open("hook_spec_entry_ports_a.json"))
    assert len(spec["entries"]) == 4
    for e in spec["entries"]:
        assert e["target"] == "ez_manage.py" and e["position"] == "before"
        body = open(e["target"]).read()
        assert body.count(e["anchor"]) == 1, e["anchor"]
        assert "twin_entry_ports_a" in e["code"] and "_psym_get" in e["code"]
        assert "queue_trade_action" in e["code"] and "await queue_trade_action" in e["code"]
        assert " and False" not in e["code"]
        dedented = textwrap.dedent(e["code"])
        src = "async def _hook_check():\n    _x = 1\n    if _x:\n        pass\n" + "".join(("    " + ln + "\n") if ln.strip() else "\n" for ln in dedented.splitlines())
        compile(src, "<hook>", "exec")
