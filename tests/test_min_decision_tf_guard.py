from dataclasses import dataclass

import numpy as np

from min_decision_tf_guard import clamp_config, guard_npz


@dataclass
class _Cfg:
    BASE_TF: str = "3m"
    WT_TF_3M_ENABLED: bool = True
    ENTRY_TF: str = "5m"
    EXIT_TFS: list[str] = None

    def __post_init__(self):
        self.EXIT_TFS = self.EXIT_TFS or ["3m", "15m", "1h"]


def test_guard_replaces_or_removes_every_low_tf_array():
    npz = {
        "timestamps": np.arange(3), "wt_3m": np.array([1, 2, 3]),
        "wt_15m": np.array([4, 5, 6]), "only_5m": np.array([7, 8, 9]),
    }
    guarded, receipt = guard_npz(npz)
    assert np.array_equal(guarded["wt_3m"], guarded["wt_15m"])
    assert "only_5m" not in guarded
    assert receipt["replaced_low_tf_arrays"] == ["wt_3m"]
    assert receipt["removed_low_tf_arrays"] == ["only_5m"]


def test_guard_disables_explicit_low_tf_switches_and_clamps_selectors():
    cfg = _Cfg()
    receipt = clamp_config(cfg)
    assert cfg.BASE_TF == "15m"
    assert cfg.WT_TF_3M_ENABLED is False
    assert cfg.ENTRY_TF == "15m"
    assert cfg.EXIT_TFS == ["15m", "15m", "1h"]
    assert receipt["disabled_low_tf_switches"] == ["WT_TF_3M_ENABLED"]


def test_guard_can_clamp_dataclass_defaults_before_fresh_instances():
    # The scalar engine must clamp the class before it imports a decision
    # manager that constructs a fresh config instance.
    clamp_config(_Cfg)
    assert _Cfg.WT_TF_3M_ENABLED is False
    assert _Cfg().WT_TF_3M_ENABLED is False
