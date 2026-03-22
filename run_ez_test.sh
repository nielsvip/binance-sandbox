#!/bin/bash
export PYTHONPATH="/Users/niels/Documents/binance:$PYTHONPATH"
echo "Starting Python Tracer"
/opt/anaconda3/envs/binance_env/bin/python3 -c "
import sys, logging
logging.basicConfig(level=logging.INFO)
sys.path.append('/Users/niels/Documents/binance')
from ez_indicators import IndicatorOrchestrator
print('Imported')
orc = IndicatorOrchestrator()
print('Started')
"
EXIT_CODE=$?
echo "Python exited with code: $EXIT_CODE"
if [ $EXIT_CODE -eq 137 ]; then
    echo "137 = OOM Killed by OS"
elif [ $EXIT_CODE -eq 139 ]; then
    echo "139 = Segmentation Fault"
fi
