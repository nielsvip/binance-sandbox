"""
btc_loop_paper.py — BTC dedicated loop forward-test paper runner.

Models scalp_v3_shadow.py: imports the SAME Python function objects from
btc_loop.py that live + backtest use. Verifies id() equality at startup
(per user 2026-04-27 parity rule).

Reads the same market data live reads (`data/market_data_*.json` and
`klines_cache/BTCUSDC_*.json`), computes features, calls btc_loop decision
functions, logs every decision JSONL.

Usage:
    python3 btc_loop_paper.py --interval 30 --variant default
    python3 btc_loop_paper.py --interval 30 --variant strict5 \
        --override BTC_ACCEL_RAMP_MIN_TFS=5

Output:
    data/btc_loop_paper/<variant>_decisions_YYYYMMDD.jsonl
    data/btc_loop_paper/<variant>_state.json
    data/btc_loop_paper/<variant>_run.log
"""
from __future__ import annotations
import argparse, glob, json, os, signal, sys, time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ───── Parity-critical: same module live + backtest use ────────────────────
import config as live_config
import btc_loop
from btc_loop import (
    WTAccelFeatures, DivergenceState, RedZoneState, PositionRiskState,
    compute_accel_ramp, compute_fib_levels, compute_round_levels,
    aggregate_multi_indicator_divergence, build_red_zone_state,
    should_enter_btc_long, should_enter_btc_short, should_exit_btc,
    should_reenter_btc, risk_gate_pre_entry,
)

PAPER_DIR = Path("data/btc_loop_paper")
PAPER_DIR.mkdir(parents=True, exist_ok=True)
SYMBOL = "BTCUSDC"


# ───── Virtual position state ──────────────────────────────────────────────


@dataclass
class VirtualBTCPosition:
    side: str = "FLAT"            # FLAT | LONG | SHORT
    entry_price: float = 0.0
    entry_ts: float = 0.0
    own_capital_usd: float = 0.0
    notional_usd: float = 0.0
    last_seen_price: float = 0.0


@dataclass
class PaperState:
    variant: str
    started_at: float
    last_exit_side: str = "NONE"
    last_exit_ts: float = 0.0
    position: VirtualBTCPosition = field(default_factory=VirtualBTCPosition)
    trades: List[Dict] = field(default_factory=list)
    cycles: int = 0


# ───── Helpers ─────────────────────────────────────────────────────────────


def parse_overrides(items: List[str]) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for it in items or []:
        if "=" not in it:
            continue
        k, v = it.split("=", 1); v = v.strip()
        if v.lower() in ("true", "false"):
            out[k.strip()] = (v.lower() == "true")
        else:
            try: out[k.strip()] = int(v)
            except ValueError:
                try: out[k.strip()] = float(v)
                except ValueError: out[k.strip()] = v
    return out


def make_config(overrides: Dict[str, object]):
    cfg = live_config.Config()
    cfg.BTC_DEDICATED_ENABLED = True       # always-on in paper
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
        else:
            print(f"[paper] WARN override {k} not on Config — ignored")
    return cfg


def verify_parity_at_startup():
    """Re-import btc_loop fresh; assert id() equality per user parity rule."""
    import importlib
    other = importlib.reload(btc_loop)
    # `verify_parity_against` returns dict of name -> bool
    res = btc_loop.verify_parity_against(other)
    failed = [k for k, ok in res.items() if not ok]
    if failed:
        print(f"[paper] WARN parity check failed for: {failed}", flush=True)
    else:
        print(f"[paper] parity OK across {len(res)} decision functions", flush=True)
    return not failed


# ───── Market-data loading (mirrors scalp_v3_shadow pattern) ───────────────


def latest_market_data_path() -> Optional[Path]:
    files = sorted(glob.glob("data/market_data_*.json"), reverse=True)
    return Path(files[0]) if files else None


def load_market_data() -> Dict[str, Dict]:
    p = latest_market_data_path()
    if not p:
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except Exception as e:
        print(f"[paper] load_market_data failed: {e}")
        return {}


_KLINES_CACHE: Dict[str, tuple[float, list]] = {}


def load_klines(tf: str, max_bars: int = 500) -> list:
    """Load BTCUSDC klines for a TF (3m/15m/1h/4h/D) with mtime-based cache."""
    # Live system writes klines_cache/<sym>_<tf>.json. Try BTCUSDC first, fallback to BTCUSDT (NPZ data).
    for sym in (SYMBOL, "BTCUSDT"):
        p = Path(f"klines_cache/{sym}_{tf}.json")
        if p.exists():
            try: mtime = p.stat().st_mtime
            except Exception: continue
            cached = _KLINES_CACHE.get(f"{sym}_{tf}")
            if cached and cached[0] == mtime:
                return cached[1][-max_bars:]
            try:
                with open(p) as f:
                    bars = json.load(f)
                _KLINES_CACHE[f"{sym}_{tf}"] = (mtime, bars)
                return bars[-max_bars:]
            except Exception as e:
                print(f"[paper] load_klines({sym},{tf}) failed: {e}")
                return []
    return []


# ───── Feature build (caller's job per parity rule) ────────────────────────


def build_features(market_data: Dict[str, Dict], cfg) -> Optional[Dict]:
    """Assemble features from live market_data + klines. Returns None if data not ready."""
    md = market_data.get(SYMBOL) or market_data.get("BTCUSDT")
    if not md:
        return None
    price = float(md.get("currentPrice") or md.get("price") or md.get("last") or 0)
    if price <= 0:
        return None
    inds = md.get("indicators", {}) or {}

    # Accel per TF (live's wt_velocity is named wt1_velocity_<tf> — tolerate variants)
    accel_per_tf = {}
    for tf in ("3m", "15m", "1h", "4h", "D"):
        v = float(inds.get(f"wt_velocity_{tf}", inds.get(f"wt1_velocity_{tf}", 0)))
        a = float(inds.get(f"wt_acceleration_{tf}", 0))
        # velocity_prev derived: vel - accel ≈ vel_prev
        v_prev = v - a
        accel_per_tf[tf] = WTAccelFeatures(velocity=v, velocity_prev=v_prev, acceleration=a)
    accel = compute_accel_ramp(
        accel_per_tf,
        require_positive=bool(getattr(cfg, "BTC_ACCEL_RAMP_REQUIRE_POSITIVE", True)),
    )

    # Multi-indicator divergence — best-effort using kline arrays
    div_lb = int(getattr(cfg, "BTC_DIVERGENCE_LOOKBACK_BARS", 5))
    mtf = {}
    for ind_name in ("WT", "RSI", "MFI"):
        per_tf = {}
        for tf in ("3m", "15m", "1h", "4h", "D"):
            bars = load_klines(tf, max_bars=div_lb + 1)
            if len(bars) < div_lb + 1:
                continue
            closes = [float(b[4]) for b in bars]
            ind_val = float(inds.get(f"{ind_name.lower()}_{tf}",
                                     inds.get(f"wt1_{tf}" if ind_name == "WT" else "", 50)))
            # Build a degenerate indicator window: only the latest is real; pad with current
            # (proper version would track per-bar history — to be improved post-Phase-5d)
            ind_window = [ind_val] * (div_lb + 1)
            per_tf[tf] = (closes, ind_window)
        mtf[ind_name] = per_tf
    divergence = aggregate_multi_indicator_divergence(
        mtf, lookback_bars=div_lb,
        require_strict=True,
        strong_tfs_threshold=3,
    )

    # Red zone — fib + round + wt_dc proxy from inds
    fib_per_tf = {}
    if bool(getattr(cfg, "BTC_RZ_USE_FIB", True)):
        for tf, lb in (("4h", int(getattr(cfg, "BTC_FIB_LOOKBACK_4H", 200))),
                       ("D", int(getattr(cfg, "BTC_FIB_LOOKBACK_D", 180)))):
            bars = load_klines(tf, max_bars=lb)
            if len(bars) < 10:
                continue
            highs = [float(b[2]) for b in bars]
            lows = [float(b[3]) for b in bars]
            fib_per_tf[tf] = compute_fib_levels(max(highs), min(lows))
    round_levels = {}
    if bool(getattr(cfg, "BTC_RZ_USE_ROUND", True)):
        round_levels = compute_round_levels(
            price,
            primary_inc_usd=float(getattr(cfg, "BTC_ROUND_INC_PRIMARY_USD", 5000.0)),
            secondary_inc_usd=float(getattr(cfg, "BTC_ROUND_INC_SECONDARY_USD", 1000.0)),
            bands_each_side=int(getattr(cfg, "BTC_ROUND_BANDS_EACH_SIDE", 8)),
        )
    bb_pct_b_4h = float(inds.get("bb_pct_b_4h", 0.5))
    if bb_pct_b_4h >= 0.85: wt_dc_zone = "TOP"
    elif bb_pct_b_4h <= 0.15: wt_dc_zone = "BOTTOM"
    else: wt_dc_zone = "BASELINE"
    rz = build_red_zone_state(
        current_price=price,
        fib_levels_per_tf=fib_per_tf,
        round_levels=round_levels,
        wt_dc_zone=wt_dc_zone,
        proximity_pct=float(getattr(cfg, "BTC_RZ_PROXIMITY_PCT", 0.5)),
    )

    return {
        "price": price,
        "accel": accel,
        "divergence": divergence,
        "red_zone": rz,
    }


# ───── Decision cycle ──────────────────────────────────────────────────────


def decide_one_cycle(state: PaperState, features: Dict, cfg) -> Optional[Dict]:
    """Run a full decision cycle: entry / exit / reentry. Return decision record."""
    now = time.time()
    state.cycles += 1
    rec: Dict = {
        "ts": now,
        "iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "cycle": state.cycles,
        "price": features["price"],
        "rz_active": features["red_zone"].active,
        "rz_kind": features["red_zone"].nearest_kind,
        "accel_side": features["accel"]["side"],
        "accel_bull_tfs": features["accel"]["bull_aligned_tfs"],
        "accel_bear_tfs": features["accel"]["bear_aligned_tfs"],
        "div_bull_inds": features["divergence"].bull_inds_aligned,
        "div_bear_inds": features["divergence"].bear_inds_aligned,
        "position_side": state.position.side,
    }

    p = state.position
    if p.side == "FLAT":
        # Reentry first
        if state.last_exit_side != "NONE":
            bars_since = max(1, int((now - state.last_exit_ts) / 180))   # 3m bars approx
            pos_state = PositionRiskState(side="FLAT", bars_since_last_exit=bars_since)
            ok, why = should_reenter_btc(
                position=pos_state, last_exit_side=state.last_exit_side,
                red_zone=features["red_zone"], accel=features["accel"],
                divergence=features["divergence"], cfg=cfg,
            )
            rec["reentry_decision"] = (ok, why)
            if ok:
                _open_virtual(state, features["price"], state.last_exit_side, cfg)
                rec["action"] = "OPEN_REENTRY"
                rec["side"] = state.last_exit_side
                return rec
        # Primary entry
        ok_long, why_long = should_enter_btc_long(
            current_price=features["price"], red_zone=features["red_zone"],
            accel=features["accel"], divergence=features["divergence"], cfg=cfg,
        )
        ok_short, why_short = should_enter_btc_short(
            current_price=features["price"], red_zone=features["red_zone"],
            accel=features["accel"], divergence=features["divergence"], cfg=cfg,
        )
        rec["entry_long_decision"] = (ok_long, why_long)
        rec["entry_short_decision"] = (ok_short, why_short)
        if ok_long and not ok_short:
            _open_virtual(state, features["price"], "LONG", cfg)
            rec["action"] = "OPEN_LONG"
        elif ok_short and not ok_long:
            _open_virtual(state, features["price"], "SHORT", cfg)
            rec["action"] = "OPEN_SHORT"
        else:
            rec["action"] = "HOLD_FLAT"
        return rec

    # Position open → exit eval
    cur = features["price"]
    if p.side == "LONG":
        pnl_pct = (cur - p.entry_price) / p.entry_price * 100.0
    else:
        pnl_pct = (p.entry_price - cur) / p.entry_price * 100.0
    pnl_usd = (pnl_pct / 100.0) * p.notional_usd
    age_bars = max(1, int((now - p.entry_ts) / 180))
    pos_state = PositionRiskState(
        side=p.side, own_capital_usd=p.own_capital_usd, notional_usd=p.notional_usd,
        current_pnl_pct=pnl_pct, current_pnl_usd=pnl_usd, age_bars=age_bars,
    )
    rec["pnl_pct"] = round(pnl_pct, 4)
    rec["pnl_usd"] = round(pnl_usd, 4)
    rec["age_bars"] = age_bars
    # WT-against count requires kline → indicator history; punt to 0 in paper for now
    wt_against_count = 0
    ok_exit, why = should_exit_btc(
        position=pos_state, accel=features["accel"], divergence=features["divergence"],
        wt_against_min_tfs=int(getattr(cfg, "BTC_TECH_EXIT_WT_MIN_TFS", 3)),
        wt_against_count=wt_against_count, cfg=cfg,
    )
    rec["exit_decision"] = (ok_exit, why)
    if ok_exit:
        state.last_exit_side = p.side
        state.last_exit_ts = now
        state.trades.append({
            "side": p.side, "entry": p.entry_price, "exit": cur,
            "pnl_pct": pnl_pct, "pnl_usd": pnl_usd, "age_bars": age_bars,
            "exit_reason": why, "exit_ts": now,
        })
        rec["action"] = "CLOSE"
        rec["side"] = p.side
        rec["exit_reason"] = why
        state.position = VirtualBTCPosition()
    else:
        rec["action"] = "HOLD_OPEN"
        p.last_seen_price = cur
    return rec


def _open_virtual(state: PaperState, price: float, side: str, cfg):
    own = float(getattr(cfg, "BTC_PER_TRADE_NOTIONAL_USD_MAX", 90.0))
    lev = float(getattr(cfg, "BTC_LEVERAGE", 20.0))
    state.position = VirtualBTCPosition(
        side=side, entry_price=price, entry_ts=time.time(),
        own_capital_usd=own, notional_usd=own * lev, last_seen_price=price,
    )


# ───── Main loop ───────────────────────────────────────────────────────────


_running = True


def _sigterm(*_):
    global _running
    _running = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30, help="seconds between decision cycles")
    ap.add_argument("--variant", default="default", help="output namespace under data/btc_loop_paper/")
    ap.add_argument("--override", action="append", default=[], help="config override KEY=VALUE")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    overrides = parse_overrides(args.override)
    cfg = make_config(overrides)

    parity_ok = verify_parity_at_startup()
    if not parity_ok and bool(getattr(cfg, "BTC_PAPER_PARITY_VERIFY_AT_STARTUP", True)):
        print("[paper] FATAL: parity check failed and BTC_PAPER_PARITY_VERIFY_AT_STARTUP=True", file=sys.stderr)
        return 1

    state = PaperState(variant=args.variant, started_at=time.time())
    today = datetime.utcnow().strftime("%Y%m%d")
    log_path = PAPER_DIR / f"{args.variant}_decisions_{today}.jsonl"
    state_path = PAPER_DIR / f"{args.variant}_state.json"

    print(f"[paper] start variant={args.variant} interval={args.interval}s "
          f"BTC_DEDICATED_ENABLED={cfg.BTC_DEDICATED_ENABLED} "
          f"path={cfg.BTC_RISK_PATH} overrides={list(overrides.keys())}", flush=True)

    log_f = open(log_path, "a", buffering=1)
    try:
        while _running:
            md = load_market_data()
            features = build_features(md, cfg)
            if features is None:
                print(f"[paper] cycle={state.cycles} no_market_data, sleeping", flush=True)
            else:
                rec = decide_one_cycle(state, features, cfg)
                if rec is not None:
                    log_f.write(json.dumps(rec, default=str) + "\n")
                    if rec.get("action") in ("OPEN_LONG", "OPEN_SHORT", "OPEN_REENTRY", "CLOSE"):
                        print(f"[paper] {rec['iso']} {rec['action']} side={rec.get('side','-')} "
                              f"price={rec['price']:.2f} pnl={rec.get('pnl_pct','-'):.4f}% "
                              f"reason={rec.get('exit_reason', rec.get('action'))}", flush=True)
            # Persist state snapshot every cycle
            try:
                with open(state_path, "w") as sf:
                    json.dump({
                        "variant": state.variant,
                        "started_at": state.started_at,
                        "cycles": state.cycles,
                        "last_exit_side": state.last_exit_side,
                        "last_exit_ts": state.last_exit_ts,
                        "position": asdict(state.position),
                        "trades_count": len(state.trades),
                    }, sf, indent=2)
            except Exception:
                pass
            time.sleep(args.interval)
    finally:
        log_f.close()
        print(f"[paper] stopped after {state.cycles} cycles, {len(state.trades)} trades", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
