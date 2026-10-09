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
    return str(root)


def _bible(root):
    return json.load(open(f"{root}/data/SWITCH_BIBLE.json"))


def test_mine_collects_all_sources_and_dedupes(tmp_path):
    root = _mkroot(tmp_path)
    assert (
        IL.main(
            [
                "--data-dir",
                root,
                "mine",
                "--write",
                "--progress-dir",
                f"{root}/data/reports/lifecycle_pilot",
            ]
        )
        == 0
    )
    d = IL.load_backlog(root)
    kinds = sorted(p["kind"] for p in d["proposals"].values())
    assert kinds == ["FORMULA_FIX", "NEW_SWITCH", "NEW_SWITCH", "ROW_ONLY"]
    assert any("LH_TRIGGER" in (p.get("sig") or []) for p in d["proposals"].values())
    n = len(d["proposals"])
    assert (
        IL.main(
            [
                "--data-dir",
                root,
                "mine",
                "--write",
                "--progress-dir",
                f"{root}/data/reports/lifecycle_pilot",
            ]
        )
        == 0
    )
    d2 = IL.load_backlog(root)
    assert len(d2["proposals"]) == n  # dedupe: same sigs merge
    assert all(p["n_evidence"] == 2 for p in d2["proposals"].values())


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
    IL.main(
        [
            "--data-dir",
            root,
            "mine",
            "--write",
            "--progress-dir",
            f"{root}/data/reports/lifecycle_pilot",
        ]
    )
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
    IL.main(
        [
            "--data-dir",
            root,
            "mine",
            "--write",
            "--progress-dir",
            f"{root}/data/reports/lifecycle_pilot",
        ]
    )
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
    IL.main(
        [
            "--data-dir",
            root,
            "mine",
            "--write",
            "--progress-dir",
            f"{root}/data/reports/lifecycle_pilot",
        ]
    )
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
