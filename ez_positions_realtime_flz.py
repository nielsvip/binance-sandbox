#!/usr/bin/env python3
"""Realtime position updater for flz account"""
import sys
import os
from pathlib import Path
script_dir = Path(__file__).parent.absolute()
sys.argv = [sys.argv[0], 'flz']
realtime_script = script_dir / 'ez_positions_realtime.py'
exec(compile(open(realtime_script).read(), str(realtime_script), 'exec'))

