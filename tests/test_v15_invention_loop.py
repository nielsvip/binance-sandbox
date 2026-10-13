"""tools/v15_invention_loop: mine/spec/ingest/promote state machine on synthetic dirs (no repo data)."""

import json

from tools import v15_invention_loop as IL


def _mkroot(tmp_path):
    root = tmp_path / "r"
    (root / "data" / "reports" / "v15_missed_trend").mkdir(parents=True)
    (root / "data" / "reports" / "v15_diag_repair").mkdir(parents=True)
    (root / "data" / "reports" / "lifecycle_pilot").mkdir(parents=True)
    (root / "data").mkdir(exist_ok=True)
    (
        root / "data" / "reports" / "v15_missed_trend" / "SUSDT_SHORT_missed_trend.json"
    ).write_text(
        json.dumps(
            {
                "symside": "SUSDT_SHORT",
                "missing": {
                    "status": "PROPOSED_NEW_SWITCH",
                    "trend": {"move_pct": 15.9},
                    "rule": "SHORT entry trigger on the first lower high after the HTF rollover",
                },
            }
        )
    )
    (
        root / "data" / "reports" / "lifecycle_pilot" / "XUSDT_LONG_v14_progress.json"
    ).write_text(
        json.dumps(
            {
                "symside": "XUSDT_LONG",
                "gaps": [
                    {
                        "fault": "TIM_LOW",
                        "metric": "tim",
                        "status": "MISSING_LEVER",
                        "note": "no lever",
                    },
                    {
                        "fault": "DD_HIGH",
                        "metric": "dd",
                        "status": "LEVER_EXISTS_BUT_COSTLY",
                        "best": "STOP_X=1",
                        "move": 2.0,
                        "gain_at": -1.0,
                    },
                ],
            }
        )
    )
    (root / "data" / "reports" / "switch_bible_verify_latest.json").write_text(
        json.dumps(
            {
                "checks": {
                    "COVERAGE": ["COV_SW: wired live+vec (a/b) but in no template"]
                }
            }
        )
    )
    (root / "data" / "SWITCH_BIBLE.json").write_text(
        json.dumps(
            {
                "switches": {
                    "NEW_SW": {
                        "status": {"crypto": "WIRED_BOTH_UNPROVEN"},
                        "in_config": {
                            "QuickConfig": True,
                            "config.py": True,
                            "config_tradier.py": True,
                        },
                        "kind": "entry",
                        "type": "bool",
                        "sides": ["SHORT"],
                        "live_reads": {"crypto": ["a.py:1"]},
                        "vec_reads": ["b.py:2"],
                        "template": {},
                    },
                    "COV_SW": {
                        "status": {"crypto": "WIRED_BOTH_UNPROVEN"},
                        "in_config": {
                            "QuickConfig": True,
                            "config.py": True,
                            "config_tradier.py": True,
                        },
                        "kind": "entry",
                        "type": "bool",
                        "sides": ["LONG", "SHORT"],
                        "live_reads": {"crypto": ["a.py:1"]},
                        "vec_reads": ["b.py:2"],
                        "template": {
                            "CRYPTO_LONG": {"rows": [{"tab": "ENTRY_REVERSAL_BOUNCE"}]}
                        },
                        "agent_c_registry": {
                            "suggested_home_tab": "ENTRY_REVERSAL_BOUNCE"
                        },
                    },
                }
            }
        )
    )
    (root / "empty_autopsy").mkdir()
    return str(root)


def _mine(root, *extra):
    return IL.main(
        [
            "--data-dir",
            root,
            "--autopsy-dirs",
            f"{root}/empty_autopsy",
            "--missed-dirs",
            f"{root}/data/reports/v15_missed_trend",
            "mine",
            "--write",
            "--progress-dir",
            f"{root}/data/reports/lifecycle_pilot",
            *extra,
        ]
    )


def _bible(root):
    return json.load(open(f"{root}/data/SWITCH_BIBLE.json"))


def test_mine_collects_all_sources_and_dedupes(tmp_path):
    root = _mkroot(tmp_path)
    assert _mine(root) == 0
    d = IL.load_backlog(root)
    kinds = sorted(p["kind"] for p in d["proposals"].values())
    assert kinds == ["FORMULA_FIX", "NEW_SWITCH", "NEW_SWITCH", "ROW_ONLY"]
    assert any("LH_TRIGGER" in (p.get("sig") or []) for p in d["proposals"].values())
    n = len(d["proposals"])
    assert _mine(root) == 0
    d2 = IL.load_backlog(root)
    assert len(d2["proposals"]) == n  # dedupe: same sigs merge
    # 2026-10-13: re-mining the SAME files must not duplicate evidence (was a blind append -> 223 dupes)
    assert all(p["n_evidence"] == 1 for p in d2["proposals"].values())
    assert all(len(p["evidence"]) == 1 for p in d2["proposals"].values())


def test_missed_trend_late_capture_emits_trigger(tmp_path):
    root = tmp_path / "r2"
    (root / "x").mkdir(parents=True)
    (root / "x" / "SUSDT_LONG_missed_trend.json").write_text(
        json.dumps(
            {
                "symside": "SUSDT_LONG",
                "revise": True,
                "trends": [
                    {"b0": 10, "b1": 500, "move_pct": 12.0, "late_by_bars": 150}
                ],
            }
        )
    )
    obs = IL.scan_missed_trend([str(root / "x")])
    assert len(obs) == 1 and obs[0]["sig"] == ("NEW_SWITCH", "LONG", "HL_TRIGGER")


def test_spec_renders_work_order_with_anchors(tmp_path):
    root = _mkroot(tmp_path)
    _mine(root)
    d = IL.load_backlog(root)
    pid = next(p for p in d["proposals"] if d["proposals"][p]["kind"] == "NEW_SWITCH")
    assert IL.main(["--data-dir", root, "spec", pid]) == 0
    import os

    out = f"{root}/data/invention/orders/{pid}.md"
    assert os.path.exists(out)
    t = open(out).read()
    assert "71" in t and "unlock" in t and "ingest --proposal" in t


def test_ingest_transitions_and_refusals(tmp_path):
    root = _mkroot(tmp_path)
    _mine(root)
    d = IL.load_backlog(root)
    pid = next(iter(d["proposals"]))
    assert (
        IL.main(["--data-dir", root, "ingest", "--proposal", pid, "--switch", "NOPE"])
        == 1
    )
    assert IL.load_backlog(root)["proposals"][pid]["status"] == "OBSERVED"
    assert (
        IL.main(["--data-dir", root, "ingest", "--proposal", pid, "--switch", "NEW_SW"])
        == 0
    )
    assert IL.load_backlog(root)["proposals"][pid]["status"] == "WIRED"
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "switch": "NEW_SW",
                "npz": "n",
                "gain_before": 1.0,
                "gain_after": 1.0,
                "valid": True,
            }
        )
    )
    assert (
        IL.main(["--data-dir", root, "ingest", "--proposal", pid, "--proof", str(bad)])
        == 1
    )
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            {
                "switch": "NEW_SW",
                "npz": "n",
                "gain_before": 1.0,
                "gain_after": 2.5,
                "valid": True,
            }
        )
    )
    assert (
        IL.main(["--data-dir", root, "ingest", "--proposal", pid, "--proof", str(good)])
        == 0
    )
    assert IL.load_backlog(root)["proposals"][pid]["status"] == "TESTED"


def test_promote_check_rules(tmp_path):
    root = _mkroot(tmp_path)
    _mine(root)
    d = IL.load_backlog(root)
    pid = next(iter(d["proposals"]))
    pdir = tmp_path / "prog"
    pdir.mkdir()
    for i in range(3):
        (pdir / f"S{i}USDT_LONG_v14_progress.json").write_text(
            json.dumps(
                {
                    "symside": f"S{i}USDT_LONG",
                    "done": {f"T!3:NEW_SW=True": {"naked_delta": 0.5}},
                }
            )
        )
    IL.main(["--data-dir", root, "ingest", "--proposal", pid, "--switch", "NEW_SW"])
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            {
                "switch": "NEW_SW",
                "npz": "n",
                "gain_before": 1.0,
                "gain_after": 2.5,
                "valid": True,
            }
        )
    )
    IL.main(["--data-dir", root, "ingest", "--proposal", pid, "--proof", str(good)])
    out = IL.promote_check(root, str(pdir))
    assert out and out[0][1] == "PROMOTED"
    r = IL.report(root)
    assert r["by_status"].get("PROMOTED") == 1


def _autopsy(ss, entry=(), exits=(), mtime=None):
    return {
        "symside": ss,
        "base": {"gain": 1.0, "trades": 50, "valid": True},
        "units": "gain_pp",
        "missing_entry_filters": list(entry),
        "missing_exit_confirmations": list(exits),
    }


def _rule(feature, op, thr, at, net, status="MISSING", family="WT"):
    return {
        "feature": feature,
        "op": op,
        "thr": thr,
        "at": at,
        "bad_removed": 10,
        "bad_total": 20,
        "good_removed": 4,
        "good_total": 20,
        "net_pnl": net,
        "rule": f"hold (do not exit) when {feature} {op} {thr}",
        "family": family,
        "status": status,
    }


def test_scan_autopsy_real_schema_distinct_syms(tmp_path):
    a = tmp_path / "v15_autopsy_first"
    b = tmp_path / "v15_autopsy_20261008"
    a.mkdir()
    b.mkdir()
    for ss, net in (("AUSDT_LONG", 10.0), ("BUSDC_LONG", 20.0), ("CUSDT_LONG", 30.0)):
        (a / f"{ss}_autopsy.json").write_text(
            json.dumps(
                _autopsy(ss, exits=[_rule("wt_velocity_15m", "<", 6.8, "exit", net)])
            )
        )
    # same board in a second dir must not double-count the sym
    (b / "AUSDT_LONG_autopsy.json").write_text(
        json.dumps(
            _autopsy(
                "AUSDT_LONG", exits=[_rule("wt_velocity_15m", "<", 7.0, "exit", 10.0)]
            )
        )
    )
    # stock + entry rule on one sym only -> below min_syms=2
    (a / "NVDA_SHORT_autopsy.json").write_text(
        json.dumps(
            _autopsy(
                "NVDA_SHORT",
                entry=[
                    _rule(
                        "bb_pct_4h",
                        "<",
                        0.2,
                        "entry",
                        1.0,
                        "FAMILY_EXISTS_NO_FIX",
                        "BB",
                    )
                ],
            )
        )
    )
    obs = IL.scan_autopsy([str(tmp_path / "v15_autopsy_*")], min_syms=2)
    assert len(obs) == 1
    o = obs[0]
    assert o["sig"] == ("AUTOPSY_RULE", "EXIT", "CRYPTO_LONG", "wt_velocity_15m", "<")
    assert o["kind"] == "AUTOPSY_NEW_RULE" and o["diagnostic_only"]
    assert o["evidence"]["n_syms"] == 3 and len(o["symsides"]) == 3
    assert abs(o["score"] - 20.0 * 3) < 1e-6  # mean net pp x distinct syms
    assert o["title"].startswith("[DIAGNOSTIC ONLY]")
    obs1 = IL.scan_autopsy([str(a)], min_syms=1)
    st = {x["sig"][2]: x for x in obs1}
    assert st["STOCKS_SHORT"]["kind"] == "AUTOPSY_FAMILY_EXTEND"


def test_cat_side_of():
    assert IL.cat_side_of("1000PEPEUSDC_LONG") == "CRYPTO_LONG"
    assert IL.cat_side_of("AGLDUSDT_SHORT") == "CRYPTO_SHORT"
    assert IL.cat_side_of("AAPL_SHORT") == "STOCKS_SHORT"


def test_upsert_dedupes_and_unions_symsides():
    bl = {"counter": 0, "proposals": {}}
    o1 = {
        "sig": ("NEW_SWITCH", "SHORT", "LH_TRIGGER"),
        "kind": "NEW_SWITCH",
        "title": "t",
        "symside": "AUSDT_SHORT",
        "direction": "SHORT",
        "score": 10.0,
        "evidence": {
            "src": "missed_trend_late",
            "symside": "AUSDT_SHORT",
            "trend": {"b0": 1, "b1": 9, "move_pct": 10.0},
        },
    }
    o2 = dict(
        o1,
        symside="BUSDT_SHORT",
        score=20.0,
        evidence={
            "src": "missed_trend_late",
            "symside": "BUSDT_SHORT",
            "trend": {"b0": 2, "b1": 8, "move_pct": 20.0},
        },
    )
    assert IL.upsert(bl, [o1, o1, o1]) == (1, 0)
    p = next(iter(bl["proposals"].values()))
    assert p["n_evidence"] == 1 and len(p["evidence"]) == 1 and p["score"] == 10.0
    IL.upsert(bl, [o2, o1])
    assert p["n_evidence"] == 2 and p["symsides"] == ["AUSDT_SHORT", "BUSDT_SHORT"]
    assert p["score"] == 20.0 * 2  # max move x distinct sym_sides


def test_normalize_repairs_legacy_duplicate_evidence():
    e = {
        "src": "missed_trend_late",
        "symside": "AGLDUSDT_SHORT",
        "trend": {"b0": 1, "b1": 2, "move_pct": 19.7},
    }
    p = {
        "evidence": [dict(e) for _ in range(20)],
        "n_evidence": 223,
        "symsides": ["AGLDUSDT_SHORT"],
    }
    IL.normalize_proposal(p)
    assert p["n_evidence"] == 1 and len(p["evidence"]) == 1


def test_rows_status_never_claims_installed_without_rows(tmp_path):
    import openpyxl

    root = tmp_path / "rr"
    nd = root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"
    nd.mkdir(parents=True)
    for fn in IL.TEMPLATE_FILES:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ENTRY_REVERSAL_BOUNCE"
        ws.append(["hdr"])
        ws.append(["Switch"])
        ws.append(["OTHER_SW"])
        wb.save(nd / fn)
    (root / "data").mkdir()
    bl = {
        "counter": 1,
        "proposals": {
            "INV-0001": {
                "id": "INV-0001",
                "status": "WIRED",
                "switch": "ENTRY_LH_TRIGGER_ENABLED",
                "switches": ["ENTRY_LH_TRIGGER_ENABLED"],
                "history": [],
            }
        },
    }
    assert IL.rows_check(str(root), bl)["INV-0001"] == "ROWS_MISSING"
    (root / "data" / "template_row_queue.json").write_text(
        json.dumps(
            {
                "queue": [
                    {
                        "switch": "ENTRY_LH_TRIGGER_ENABLED",
                        "tab": "ENTRY_REVERSAL_BOUNCE",
                    }
                ]
            }
        )
    )
    assert IL.rows_check(str(root), bl)["INV-0001"] == "ROWS_QUEUED"
    for fn in IL.TEMPLATE_FILES:
        wb = openpyxl.load_workbook(nd / fn)
        wb["ENTRY_REVERSAL_BOUNCE"].append(["ENTRY_LH_TRIGGER_ENABLED"])
        wb.save(nd / fn)
    assert IL.rows_check(str(root), bl)["INV-0001"] == "ROWS_INSTALLED"
    assert bl["proposals"]["INV-0001"]["template_rows"]
