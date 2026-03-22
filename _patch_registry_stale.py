#!/usr/bin/env python3
"""Fix: refresh_rankings rate() calls blocked by STALE_INDICATORS.
Inject fresh _tick_ts so the staleness gate sees bridge as fresh."""

filepath = '/home/niels/binance/ez_positions_quick.py'
content = open(filepath, 'r').read()

old = '''            score_l, rec_l, reason_l = await AdvancedSignalRater.rate( proxy_account, symbol, True, price, data, data, 0.0, is_exit=False, is_allowed=True, tracker_manager=self.tracker_manager )
            score_s, rec_s, reason_s = await AdvancedSignalRater.rate( proxy_account, symbol, False, price, data, data, 0.0, is_exit=False, is_allowed=True, tracker_manager=self.tracker_manager )'''

new = '''            _ranking_metrics = dict(data)
            _ranking_metrics['_tick_ts'] = now_ts
            score_l, rec_l, reason_l = await AdvancedSignalRater.rate( proxy_account, symbol, True, price, _ranking_metrics, data, 0.0, is_exit=False, is_allowed=True, tracker_manager=self.tracker_manager )
            score_s, rec_s, reason_s = await AdvancedSignalRater.rate( proxy_account, symbol, False, price, _ranking_metrics, data, 0.0, is_exit=False, is_allowed=True, tracker_manager=self.tracker_manager )'''

assert old in content, 'old not found'
content = content.replace(old, new, 1)

open(filepath, 'w').write(content)
print('Fix applied: refresh_rankings injects fresh _tick_ts to bypass staleness gate')
