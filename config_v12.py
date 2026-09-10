#!/usr/bin/env python3
"""config_v12.py — compat shim exposing v12 QuickConfig as ConfigV12.

Resolves followup gap: no config_v12 existed while audits referenced
3031/2324 key counts and v12_quick_engine.py (11974L, 719K) was the de-facto
v12 config. This shim re-exports QuickConfig so `import config_v12` works
and `config_v12.Config` / `config_v12.QuickConfig` are identical.
"""
from v12_quick_engine import QuickConfig as Config  # noqa: F401
from v12_quick_engine import QuickConfig  # noqa: F401

__all__ = ["Config", "QuickConfig"]
