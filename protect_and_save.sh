#!/bin/bash
# PROTECT AND SAVE — Run after ANY changes to lock files from linter destruction
# Usage: bash protect_and_save.sh

set -e
cd /Users/niels/Documents/binance
TS=$(date -u +%Y%m%d_%H%M%S)
SNAP="backups/snap_${TS}"
mkdir -p "$SNAP"

echo "[$TS] Saving snapshot to $SNAP"

# CRITICAL FILES — copy to snapshot
for f in \
    backtest_v8_engine.py \
    backtest_v8_precompute.py \
    backtest_v8_precompute_tradier.py \
    backtest_v8_harness.py \
    backtest_v8_interpolate_3m.py \
    backtest_v8_interpolate_5m.py \
    backtest_fill_kline_gaps.py \
    ez_manage.py \
    ez_positions_quick.py \
    ez_positions_service.py \
    ez_indicators.py \
    tradier_indicators.py \
    tradier_manage.py \
    config.py \
    config_tradier.py \
    utils.py \
    LOCKED_FILES.md \
    CLAUDE.md
do
    [ -f "$f" ] && cp "$f" "$SNAP/" && echo "  ✓ $f"
done

# Write checksums so we can detect if linter changed anything
md5 -q "$SNAP"/*.py > "$SNAP/checksums.md5" 2>/dev/null || md5sum "$SNAP"/*.py > "$SNAP/checksums.md5" 2>/dev/null
echo "  ✓ checksums.md5"

# Also save to a READONLY backup that linter can't touch
READONLY="backups/READONLY_LATEST"
rm -rf "$READONLY"
cp -r "$SNAP" "$READONLY"
chmod -R a-w "$READONLY"
echo "  ✓ READONLY copy at $READONLY"

# Deploy to S1
echo "Deploying to S1..."
scp "$SNAP"/backtest_v8_*.py "$SNAP"/ez_manage.py "$SNAP"/ez_positions_quick.py "$SNAP"/ez_indicators.py "$SNAP"/tradier_indicators.py "$SNAP"/tradier_manage.py "$SNAP"/utils.py s1-int:/home/niels/binance-sandbox/ 2>/dev/null && echo "  ✓ S1" || echo "  ✗ S1 failed"

# Deploy to S2
echo "Deploying to S2..."
scp "$SNAP"/backtest_v8_*.py "$SNAP"/ez_manage.py "$SNAP"/ez_positions_quick.py "$SNAP"/ez_indicators.py "$SNAP"/tradier_indicators.py "$SNAP"/tradier_manage.py "$SNAP"/utils.py s2-int:/home/niels/binance-sandbox/ 2>/dev/null && echo "  ✓ S2" || echo "  ✗ S2 failed"

echo ""
echo "DONE. Snapshot: $SNAP"
echo "READONLY: $READONLY"
echo ""
echo "To RESTORE after linter destruction:"
echo "  cp backups/READONLY_LATEST/*.py ."
echo "  bash protect_and_save.sh"
