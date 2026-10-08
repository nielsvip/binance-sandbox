"""Compat shim (2026-10-08): cat_side_defaults was renamed to per_sym_settings.

All live imports keep working; the real loader is per_sym_settings.py.
"""
from per_sym_settings import *  # noqa: F401,F403
from per_sym_settings import (  # noqa: F401
    CAT_SIDES,
    CRYPTO_SUFFIXES,
    PATH,
    cat_side_of,
    defaults,
    enabled,
    get,
    get_for,
)
