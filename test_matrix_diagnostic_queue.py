import json

from tools import param_matrix_daemon as engine_queue
from tools import vec_screen_daemon as vec_queue


PARAM = "DC_LOW4_STOP_ENABLED"


def test_engine_queue_excludes_dc_low4_diagnostic_unless_explicit(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "params": {
            PARAM: {
                "sweep_tier": "ENGINE_SCREEN",
                "sweepable": True,
                "test_values": [False, True],
            },
        },
    }))
    monkeypatch.setattr(engine_queue.psc, "STOP_PACKS", {})
    monkeypatch.setattr(engine_queue.psc, "TF_EXCLUDE_PACKS", {})
    monkeypatch.setattr(
        engine_queue.psc,
        "load_params",
        lambda _path, _priority: [(PARAM, [False, True])],
    )
    monkeypatch.setattr(engine_queue, "useless_knobs", lambda: set())

    assert not engine_queue.all_cells(manifest)
    assert [row[0] for row in engine_queue.all_cells(
        manifest, include_diagnostics=True
    )] == [PARAM, PARAM]


def test_vec_queue_excludes_dc_low4_diagnostic_unless_explicit(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "params": {
            PARAM: {
                "sweep_tier": "VEC_SCREEN",
                "sweepable": True,
                "test_values": [False, True],
            },
        },
    }))
    monkeypatch.setattr(vec_queue, "implemented_knobs", lambda: {PARAM})
    monkeypatch.setattr(
        vec_queue.psc,
        "load_params",
        lambda _path, _priority: [(PARAM, [False, True])],
    )

    assert not vec_queue.vec_params(manifest)
    assert vec_queue.vec_params(
        manifest, include_diagnostics=True
    ) == [(PARAM, [False, True])]
