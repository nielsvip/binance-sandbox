"""Forward-running paper-trading shadow runner for Tradier options.

Each cycle, for each variant config, captures what make_decisions() AND
_daily_find_opportunities() WOULD have decided — without firing any orders.
End-of-day comparison via tradier_options_shadow_compare.py reveals which
variant performs best.

Reuses live decision functions verbatim (no reimplementation):
  - tradier_options_agent.assess_market()
  - tradier_options_agent.make_decisions()
  - tradier_options_agent._daily_find_opportunities()
  - tradier_options_agent.analyze_diversification()
  - tradier_options_agent._check_portfolio_limits()
  - tradier_options_state.snapshot_once()

Outputs:
  data/options_shadow/variants.json                    — variant overrides
  data/options_shadow/<variant>/decisions_<YYYYMMDD>.jsonl

CLI:
  python3 tradier_options_shadow_runner.py                          # one-shot all variants
  python3 tradier_options_shadow_runner.py --loop 300                # every 5min
  python3 tradier_options_shadow_runner.py --variant tight_wtdc_80 --loop 300
"""
import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

BASE_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from tradier_options_agent import (
    MarketAssessment, assess_market, make_decisions, analyze_diversification,
    _check_portfolio_limits, _daily_find_opportunities, MAX_PER_ORDER, MAX_TOTAL_OPTIONS,
)
from tradier_options_analyzer import _load_gtc_orders
from tradier_options_state import snapshot_once
from tradier_manage import is_regular_trading_hours

SHADOW_DIR = BASE_PATH / "data" / "options_shadow"
SHADOW_DIR.mkdir(parents=True, exist_ok=True)
VARIANTS_PATH = SHADOW_DIR / "variants.json"

DEFAULT_VARIANTS: Dict[str, Dict[str, Any]] = {
    "live": {},
    "tight_wtdc_80": {"OPTIONS_BUY_MIN_WT_DC_SCORE": 80.0},
    "loose_wtdc_60": {"OPTIONS_BUY_MIN_WT_DC_SCORE": 60.0},
    "wt_dc_off": {"OPTIONS_BUY_WT_DC_GATE_ENABLED": False},
    "tighter_caps": {"OPTIONS_MAX_PER_SYMBOL": 0.15, "OPTIONS_MAX_PER_SECTOR": 0.30},
    "aggressive_dte_30": {"OPTIONS_BUY_MIN_DTE": 30},
    "conservative_delta_50": {"OPTIONS_BUY_MIN_ABS_DELTA": 0.50},
}


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(path)


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _load_variants() -> Dict[str, Dict[str, Any]]:
    if not VARIANTS_PATH.exists():
        _atomic_write_json(VARIANTS_PATH, DEFAULT_VARIANTS)
        return dict(DEFAULT_VARIANTS)
    try:
        return json.loads(VARIANTS_PATH.read_text())
    except Exception as e:
        print(f"[WARN] variants.json parse error {e}; using defaults", file=sys.stderr)
        return dict(DEFAULT_VARIANTS)


def _build_variant_config(overrides: Dict[str, Any]) -> TradierConfig:
    cfg = TradierConfig()
    for k, v in (overrides or {}).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
        else:
            setattr(cfg, k, v)
    return cfg


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f"[WARN] {path.name}: {e}", file=sys.stderr)
        return {}


def _decision_to_dict(d) -> dict:
    if hasattr(d, "__dict__"):
        try:
            return asdict(d)
        except Exception:
            return dict(d.__dict__)
    return dict(d) if isinstance(d, dict) else {"value": str(d)}


def _summarize(decisions: List[Any], opportunities: List[dict]) -> dict:
    n_calls = n_puts = n_skip = n_buy = n_sell = n_hold = 0
    total_premium = 0.0
    wt_scores: List[float] = []
    for d in decisions:
        action = getattr(d, "action", None) or (d.get("action") if isinstance(d, dict) else "")
        otype = getattr(d, "option_type", None) or (d.get("option_type") if isinstance(d, dict) else "")
        bud = float(getattr(d, "budget_used", 0) or (d.get("budget_used", 0) if isinstance(d, dict) else 0) or 0)
        if action == "BUY":
            n_buy += 1
            total_premium += max(bud, 0)
            if otype == "call":
                n_calls += 1
            elif otype == "put":
                n_puts += 1
        elif action == "SELL":
            n_sell += 1
        elif action == "HOLD":
            n_hold += 1
        elif action == "SKIP":
            n_skip += 1
    n_opp_calls = sum(1 for o in opportunities if o.get("type") == "call")
    n_opp_puts = sum(1 for o in opportunities if o.get("type") == "put")
    n_opp_spread = sum(1 for o in opportunities if o.get("type") == "spread")
    n_opp_csp = sum(1 for o in opportunities if o.get("type") == "csp")
    for o in opportunities:
        try:
            if "score" in o:
                wt_scores.append(float(o.get("score", 0) or 0))
        except Exception:
            pass
    return {
        "n_decisions": len(decisions),
        "n_buy": n_buy,
        "n_sell": n_sell,
        "n_hold": n_hold,
        "n_skip": n_skip,
        "n_calls_proposed": n_calls,
        "n_puts_proposed": n_puts,
        "total_proposed_premium": round(total_premium, 2),
        "n_opportunities": len(opportunities),
        "n_opp_calls": n_opp_calls,
        "n_opp_puts": n_opp_puts,
        "n_opp_spread": n_opp_spread,
        "n_opp_csp": n_opp_csp,
        "avg_opp_score": round(sum(wt_scores) / len(wt_scores), 2) if wt_scores else 0.0,
    }


async def _run_one_variant(
    variant_name: str,
    overrides: Dict[str, Any],
    market: Optional[MarketAssessment],
    scan_results: dict,
    indicators: dict,
    rankings: dict,
    positions: List[dict],
    client: TradierAPIClient,
    skip_chain_fetch: bool,
) -> dict:
    cfg = _build_variant_config(overrides)
    ts = datetime.now(timezone.utc).isoformat()
    decisions: List[Any] = []
    opportunities: List[dict] = []
    error: Optional[str] = None
    try:
        if market is not None:
            div = analyze_diversification(positions, cfg)
            current_exposure = sum(abs(float(p.get("cost_basis", 0) or 0)) for p in positions)
            decisions = make_decisions(
                market=market,
                scan_results=scan_results,
                existing_positions=positions,
                current_exposure=current_exposure,
                max_per_order=MAX_PER_ORDER,
                max_total=MAX_TOTAL_OPTIONS,
                diversification=div,
                config=cfg,
            )
        else:
            decisions = []
        if not skip_chain_fetch:
            held_symbols = set()
            for p in positions:
                sym = p.get("symbol") or ""
                if sym:
                    held_symbols.add(sym)
            gtc_orders = _load_gtc_orders(cfg)
            limits = _check_portfolio_limits(positions, gtc_orders)
            needs_puts = limits["call_cost"] > limits["put_cost"] * 1.5
            try:
                opportunities = await _daily_find_opportunities(
                    client, cfg, indicators, rankings, held_symbols, needs_puts, limits
                )
            except Exception as e:
                error = f"_daily_find_opportunities: {e}"
                opportunities = []
    except Exception as e:
        error = f"variant_run: {e}"
    summary = _summarize(decisions, opportunities)
    record = {
        "ts": ts,
        "variant": variant_name,
        "overrides": overrides,
        "market_open": is_regular_trading_hours(),
        "market": _market_to_dict(market) if market else None,
        "decisions": [_decision_to_dict(d) for d in decisions],
        "opportunities": opportunities,
        "summary": summary,
        "error": error,
    }
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = SHADOW_DIR / variant_name / f"decisions_{today}.jsonl"
    _append_jsonl(out, record)
    return record


def _market_to_dict(m: MarketAssessment) -> dict:
    try:
        return asdict(m)
    except Exception:
        return dict(getattr(m, "__dict__", {}))


async def _gather_shared_inputs(
    client: TradierAPIClient,
    config: TradierConfig,
) -> Tuple[Optional[MarketAssessment], dict, dict, dict, List[dict]]:
    market: Optional[MarketAssessment] = None
    try:
        market = await assess_market(client, config)
    except Exception as e:
        print(f"[WARN] assess_market error: {e}", file=sys.stderr)
    indicators = _load_json(config.DATA_DIR / "tradier_indicators_latest.json")
    rankings = _load_json(config.DATA_DIR / "tradier_rankings.json")
    scan_results = _load_json(config.DATA_DIR / "options_analysis_latest.json")
    positions: List[dict] = []
    try:
        snap = await snapshot_once(["trb", "trc"])
        positions = snap.get("positions", []) or []
    except Exception as e:
        print(f"[WARN] snapshot_once error: {e}", file=sys.stderr)
    return market, indicators, rankings, scan_results, positions


async def run_cycle(
    variants: Dict[str, Dict[str, Any]],
    only_variant: Optional[str] = None,
    skip_chain_fetch: bool = False,
) -> List[dict]:
    config = TradierConfig()
    client = TradierAPIClient(config, account_key="trb")
    try:
        await client.connect()
        market, indicators, rankings, scan_results, positions = await _gather_shared_inputs(client, config)
        results: List[dict] = []
        names = [only_variant] if only_variant else list(variants.keys())
        for name in names:
            if name not in variants:
                print(f"[WARN] variant '{name}' not in variants.json", file=sys.stderr)
                continue
            overrides = variants[name]
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] variant={name} overrides={overrides}")
            rec = await _run_one_variant(
                name, overrides, market, scan_results, indicators, rankings,
                positions, client, skip_chain_fetch,
            )
            s = rec["summary"]
            err = f" ERROR={rec['error']}" if rec.get("error") else ""
            print(f"  → decisions n_buy={s['n_buy']} n_sell={s['n_sell']} n_skip={s['n_skip']} "
                  f"calls={s['n_calls_proposed']} puts={s['n_puts_proposed']} "
                  f"premium=${s['total_proposed_premium']:.0f} opps={s['n_opportunities']}"
                  f" avg_score={s['avg_opp_score']:.1f}{err}")
            results.append(rec)
        return results
    finally:
        await client.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, default=0,
                    help="seconds between cycles (default 0 = one-shot)")
    ap.add_argument("--variant", default=None,
                    help="run only one variant (must exist in variants.json)")
    ap.add_argument("--skip-chain-fetch", action="store_true",
                    help="skip _daily_find_opportunities (no Tradier chain calls); "
                         "make_decisions still runs")
    args = ap.parse_args()
    variants = _load_variants()
    if not variants:
        print("[ERR] no variants defined", file=sys.stderr)
        sys.exit(1)
    if args.loop <= 0:
        await run_cycle(variants, only_variant=args.variant, skip_chain_fetch=args.skip_chain_fetch)
        return
    print(f"loop mode every {args.loop}s — variants={list(variants.keys()) if not args.variant else [args.variant]}")
    while True:
        t0 = time.time()
        try:
            await run_cycle(variants, only_variant=args.variant, skip_chain_fetch=args.skip_chain_fetch)
        except Exception as e:
            print(f"[ERR] cycle: {e}", file=sys.stderr)
        elapsed = time.time() - t0
        sleep_s = max(1.0, args.loop - elapsed)
        await asyncio.sleep(sleep_s)


if __name__ == "__main__":
    asyncio.run(main())
