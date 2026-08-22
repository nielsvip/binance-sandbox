#!/usr/bin/env python3
"""v8_vec_sweep — DEPRECATED alias for v12_wide_engine. Import that instead.

Kept because 54 files still import this name. It re-exports the FULL public
surface plus every private name those files actually use, listed explicitly.

That explicit list is the whole point. The previous attempt at this shim was
`from vector_engine import *`, which supplied 2 of the 16 names its readers
import: `import *` can never carry a name beginning with an underscore, and 8 of
them do. The other 6 did not exist in the target module at all. Both failures are
silent until import time, so nothing looked wrong until 54 files stopped working.

Do not replace the explicit imports below with a star import.
"""
import warnings

warnings.warn(
    "v8_vec_sweep is deprecated — import v12_wide_engine instead",
    DeprecationWarning, stacklevel=2,
)

from v12_wide_engine import *  # noqa: F401,F403  (public surface)
from v12_wide_engine import (  # noqa: F401  (explicit: underscore names + API)
    NPZ_DIR,
    SWEEP_RESULTS_DIR,
    SweepConfig,
    TradeEvent,
    _apply_per_task_overrides,
    _gain_pct,
    _load_active_config_overrides,
    _max_dd_pct,
    _path_scoped_entry_union,
    _resolve_npz_path,
    _tradier_dc4h_boundary_masks_vec,
    _vec_round_trip_cost_for_sym,
    load_npz,
    run_sweep,
    simulate_one_symbol,
    sweep_config_for_mode,
)

# QuickConfig/SweepConfig naming: this engine's config class is SweepConfig and
# stays SweepConfig. v12_quick_engine's QuickConfig is a DIFFERENT class with a
# different default set — aliasing one to the other would silently swap
# strategies, which is how a whole optimisation campaign got invalidated before.
