"""
╔══════════════════════════════════════════════════════════════════════════╗
║  FAKE_ENGINE — THIS IS NOT A REAL BACKTEST ENGINE                       ║
║                                                                          ║
║  v8_quick_engine is a vectorized shortcut (~85% parity with live).      ║
║  It does NOT call check_entry_candidates / check_exit_candidates /       ║
║  process_position / hedge_engine / MultiAccountTradeManager.            ║
║                                                                          ║
║  Results from this engine are DIAGNOSTIC ONLY and MUST NEVER be used    ║
║  to promote configs to live, compare strategy variants, or report        ║
║  Sharpe numbers to the user.                                             ║
║                                                                          ║
║  THE REAL ENGINE IS: backtest_v8_engine.py                              ║
║  Require pool_sharpe ≥ 0.7 from backtest_v8_engine BEFORE per_sym.     ║
║                                                                          ║
║  The real implementation lives in:  old/fake_engine.py                  ║
║  This stub re-exports it for backward compat with locked callers only.  ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import warnings
import sys
import os

warnings.warn(
    "[FAKE_ENGINE] v8_quick_engine is a vectorized shortcut (~85% parity). "
    "Use backtest_v8_engine.py (the REAL engine) for any result you will "
    "act on. old/fake_engine.py contains the implementation.",
    stacklevel=2,
)

# Re-export everything from old/fake_engine.py so locked callers don't break.
_here = os.path.dirname(os.path.abspath(__file__))
_old = os.path.join(_here, "old")
if _old not in sys.path:
    sys.path.insert(0, _old)

from fake_engine import *  # noqa: F401, F403
from fake_engine import (  # noqa: F401
    QuickConfig,
    load_npz,
    simulate,
    iter_npz,
    compute_entry_signals,
    compute_exit_signals,
    _safe,
    _safeb,
)

try:
    from fake_engine import FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER  # noqa: F401
except ImportError:
    pass
