#!/usr/bin/env python3
"""evaluate_vec shim — re-exports tools.opt.evaluate_vec for top-level import."""

from tools.opt.evaluate_vec import CONTROL_ONLY, banned_reason, is_banned

__all__ = ["CONTROL_ONLY", "banned_reason", "is_banned"]
