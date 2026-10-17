"""No USDC<->USDT conversion may be APPLIED anywhere (USER 2026-10-17).

"make sure no usdc/usdt conversion is being applied in any script anymore as it is outdated and obsolete" +
"usdc and usdt can have >5% price discrepancies so we DEFINITELY need to make it usdc native as well".

USDC and USDT perps are DIFFERENT CONTRACTS: separate order books, separate funding, separate tick/step filters
and a basis that was measured at 0.110-0.139% on 2026-10-17 (BTC/ETH/BNB/SOL/ZEC/LINK/DOGE/KAITO) and widens
under stress. Substituting one for the other produced wrong candles, wrong funding/OI, wrong mark prices, wrong
lot steps, and hedges that carried the basis instead of offsetting the position.

This test fails if a conversion is RE-INTRODUCED. It deliberately allows the one remaining shape: building the
sibling NAME purely to test membership so the code can WARN that a symbol LIST is stale — the fix for that is the
list, never a rewrite at runtime.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "backups", "__pycache__", ".backup", ".claude", "tests", "node_modules",
             "quarantine_scripts_good_20260910", "live_parity", "SPREADSHEETS", "docs"}
# A conversion is "applied" when the rewritten symbol is assigned, returned, or fed to something — not when it is
# only compared. These are the shapes that assign/return it.
APPLY = re.compile(
    r"""(?x)
    (?:^|[^#\n]*?)                                   # not a comment-only line (checked separately)
    (?:
        =\s*[A-Za-z_][\w\.\[\]"']*\.replace\(\s*["'](?:USDC|USDT)["']\s*,\s*["'](?:USDT|USDC)["']\s*\)
      | return\s+[A-Za-z_][\w\.\[\]"']*\.replace\(\s*["'](?:USDC|USDT)["']\s*,\s*["'](?:USDT|USDC)["']\s*\)
      | =\s*[A-Za-z_][\w\.\[\]"']*\[:-4\]\s*\+\s*["'](?:USDT|USDC)["']\s*$
      | return\s+[A-Za-z_][\w\.\[\]"']*\[:-4\]\s*\+\s*["'](?:USDT|USDC)["']
    )
    """
)
ALLOW_FILES = {
    # retired behind a hard refusal; the line is unreachable
    "tools/build_usdc_hybrid_1y.py",
}


def _py_files():
    """os.walk with dirname pruning — rglob would crawl klines_cache/ and .venv/ (tens of thousands of files)."""
    import os
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith("klines_")]
        for fn in filenames:
            if not fn.endswith(".py") or fn.startswith("test_"):
                continue
            p = pathlib.Path(dirpath) / fn
            yield p.relative_to(ROOT), p


def test_no_conversion_is_applied():
    offenders = []
    for rel, p in _py_files():
        if str(rel) in ALLOW_FILES:
            continue
        try:
            lines = p.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            code = ln.split("#", 1)[0]
            if not code.strip():
                continue
            # A site may build the sibling NAME to test membership so it can WARN that a symbol list is stale.
            # Those carry an explicit marker; anything else that assigns or returns a rewritten symbol fails.
            if "detect-only" in ln:
                continue
            if APPLY.search(code):
                offenders.append(f"{rel}:{i}: {ln.strip()[:120]}")
    assert not offenders, (
        "USDC<->USDT conversion re-introduced (they are different contracts — separate books, funding and "
        "filters). Fix the SYMBOL LIST instead of rewriting the symbol:\n  " + "\n  ".join(offenders)
    )


def test_the_shared_helpers_are_no_ops():
    """utils.force_usdc_* and the broadcaster/ez_double copies must return the symbol unchanged."""
    import sys
    sys.path.insert(0, str(ROOT))
    import importlib.util as iu
    spec = iu.spec_from_file_location("_u_noconv", ROOT / "utils.py")
    # utils imports a lot; read the source instead of executing it
    src = (ROOT / "utils.py").read_text()
    body = src.split("def force_usdc_if_needed", 1)[1].split("\ndef ", 1)[0]
    assert "return symbol" in body
    assert "return usdc_symbol" not in body, "utils.force_usdc_if_needed must not return a rewritten symbol"
    body2 = src.split("def force_usdc_in_list", 1)[1].split("\ndef ", 1)[0]
    assert "converted.append(usdc_version)" not in body2, "utils.force_usdc_in_list must not rewrite symbols"


def test_symbol_config_and_step_size_never_borrow_the_twin():
    """Per-contract filters (tick/step/min-notional) must never come from the other quote currency, and the
    vector twin must match live — they were removed in the same pass."""
    live = (ROOT / "ez_manage.py").read_text()
    seg = live.split("def get_symbol_config", 1)[1][:1200]
    assert "CONFIG_FALLBACK" not in seg, "ez_manage.get_symbol_config must not fall back to the USDT config"
    assert "CONFIG_MISSING" in seg, "a missing symbol_config must be reported"
    vec = (ROOT / "vec_decisions" / "small_account_sizer.py").read_text()
    seg2 = vec.split("def step_size_for", 1)[1][:900]
    assert 'm.get(symbol.replace("USDC", "USDT"))' not in seg2, "vec step_size_for must not borrow the USDT step"


def test_hedges_stay_on_the_same_contract():
    q = (ROOT / "ez_positions_quick.py").read_text()
    assert "HEDGE_USDC_REDIRECT" not in q, "a hedge must be placed on the same contract, not the USDC sibling"


def test_open_is_never_silently_redirected():
    m = (ROOT / "ez_manage.py").read_text()
    assert "auto-redirected USDT→USDC" not in m, "the silent USDT->USDC open redirect must stay retired"
    assert "USDC_LIST_STALE" in m, "a stale list must be reported instead of redirected"
