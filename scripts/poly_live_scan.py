#!/usr/bin/env python3
import asyncio, aiohttp, json
from datetime import datetime, timezone, timedelta

GAMMA = 'https://gamma-api.polymarket.com'

CATS = {
    'esports': ['counter-strike','valorant','dota','league of legends','map 1','map 2','bo3','bo5','esl','game 1 winner','game 2 winner','game 3 winner'],
    'soccer': ['premier league','la liga','bundesliga','serie a','ligue 1','champions league','arsenal','chelsea','liverpool','manchester','real madrid','barcelona','napoli','galatasaray','brighton','tottenham','burnley','atletico','psg','ajax','porto','inter milan','roma','lazio','sevilla','villarreal','celtic','rangers fc'],
    'nba': ['nba','lakers','celtics','warriors','nets','knicks','heat','bucks','nuggets','76ers','suns','grizzlies','thunder','hornets','spurs','hawks','pistons','rockets','maverick','clippers','timberwolves','cavaliers','raptors','bulls'],
    'nhl': ['nhl','stanley cup','bruins','maple leafs','rangers','penguins','blackhawks','senators','canucks','oilers','avalanche','lightning','capitals','golden knights','jets','wild','flames','ducks','sharks','sabres'],
    'nfl': ['nfl','super bowl','patriots','cowboys','chiefs','eagles','49ers','packers','ravens','steelers','seahawks','rams','chargers','dolphins','bills','bears','saints','broncos','raiders','cardinals'],
    'politics_us': ['president 2028','senate','governor','republican nominee','democrat nominee','nomination','primary 2026','primary 2028'],
    'economics': ['fed rate','federal reserve','interest rate',' bps ','fomc','ecb rate','bank of england','bank of japan','tariff'],
    'crypto': ['bitcoin','btc ','ethereum','eth ','solana','sol ','bnb','xrp ','ripple','doge'],
    'entertainment': ['oscar','academy award','grammy','emmy','bafta','98th','rotten tomatoes'],
    'spread': ['spread:','(-1','-2','-3','-1.5','-2.5','-3.5'],
    'over_under': ['o/u','over/under','total points','total goals','total runs'],
    'college': ['ncaa','march madness','college','university','flyers','billikens','commodores','gators','wildcats','tar heels','blue devils','jayhawks','longhorns','sooners','wolverines','buckeyes'],
}
def classify(q):
    ql = q.lower()
    for cat, kws in CATS.items():
        if any(k in ql for k in kws):
            return cat
    return 'other'

async def run():
    now = datetime.now(timezone.utc)
    in_24h = now + timedelta(hours=24)

    def parse_dt(s):
        if not s: return None
        try:
            dt = datetime.fromisoformat(s.replace('Z','+00:00').replace('+00','+00:00'))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except: return None

    async with aiohttp.ClientSession() as s:
        all_markets = []
        for off in [0,500,1000,1500,2000,2500,3000,3500]:
            async with s.get(f'{GAMMA}/markets', params={'active':'true','closed':'false','order':'volumeNum','ascending':'false','limit':500,'offset':off}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                batch = await r.json(content_type=None)
            all_markets.extend(batch)
            if len(batch) < 500:
                break
        print(f'Active markets scanned: {len(all_markets)}')

        past_end, expiring_soon, long_dur = [], [], []

        for m in all_markets:
            lp = float(m.get('lastTradePrice',0) or 0)
            if lp < 0.92: continue
            ask = float(m.get('bestAsk',lp) or lp)
            entry = ask if ask and ask > lp*0.95 else lp + 0.001
            if entry >= 1.0: continue
            net_ret = round((1.0 - entry) / entry * 100 * 0.98, 2)
            cat = classify(m.get('question',''))
            vol24 = float(m.get('volume24hr',0) or 0)
            vol = float(m.get('volumeNum',0) or 0)
            liq = float(m.get('liquidityNum',0) or 0)
            h1chg = float(m.get('oneHourPriceChange',0) or 0)
            h24chg = float(m.get('oneDayPriceChange',0) or 0)
            end_dt = parse_dt(m.get('endDateIso',''))
            rec = {'q': m.get('question','')[:80], 'cat': cat, 'lp': lp, 'entry': entry, 'net': net_ret, 'vol': vol, 'vol24': vol24, 'liq': liq, 'h1chg': h1chg, 'h24chg': h24chg, 'end': m.get('endDateIso','')[:10], 'id': m.get('id'), 'toks': m.get('clobTokenIds','[]')}
            if end_dt and end_dt < now:
                hrs = (now - end_dt).total_seconds()/3600
                rec['hrs_past'] = round(hrs,1)
                if h1chg < -0.02 and vol24 > 5000:
                    continue  # informed selling
                past_end.append(rec)
            elif end_dt and end_dt < in_24h:
                hrs = (end_dt - now).total_seconds()/3600
                rec['hrs_left'] = round(hrs,1)
                expiring_soon.append(rec)
            else:
                long_dur.append(rec)

        past_end.sort(key=lambda x: -x['lp'])
        expiring_soon.sort(key=lambda x: x.get('hrs_left',99))

        print(f'\n=== PAST ENDDATE — ORACLE LAG TARGETS ({len(past_end)}) ===')
        print(f'{"Cat":<13} {"Entry":>7} {"Net%":>7} {"Vol24h":>10} {"HrsPast":>9} {"Question"}')
        print('-'*110)
        for r in past_end:
            flag = ' ***' if r['vol24'] > 50000 else ''
            print(f'{r["cat"]:<13} {r["entry"]:>7.4f} {r["net"]:>6.1f}% {r["vol24"]:>10,.0f} {r["hrs_past"]:>8.1f}h {r["q"]}{flag}')

        print(f'\n=== EXPIRING <24H at 92%+ ({len(expiring_soon)}) ===')
        print(f'{"Cat":<13} {"Entry":>7} {"Net%":>7} {"Vol24h":>10} {"HrsLeft":>9} {"Question"}')
        print('-'*110)
        for r in expiring_soon[:50]:
            print(f'{r["cat"]:<13} {r["entry"]:>7.4f} {r["net"]:>6.1f}% {r["vol24"]:>10,.0f} {r["hrs_left"]:>8.1f}h {r["q"]}')

        print(f'\n=== OSCARS BREAKDOWN (ceremony March 15 UTC ~01:00) ===')
        oscars = [r for r in past_end+expiring_soon+long_dur if r['cat']=='entertainment']
        for r in sorted(oscars, key=lambda x: x['entry']):
            hrs = r.get('hrs_left') or -(r.get('hrs_past',0))
            print(f'  entry={r["entry"]:.4f} net={r["net"]:.1f}% vol24={r["vol24"]:,.0f} hrs={hrs:+.1f}h | {r["q"]}')

        print(f'\n=== CATEGORY SUMMARY (active 92%+ markets) ===')
        cat_stats = {}
        for r in past_end+expiring_soon+long_dur:
            c = r['cat']
            if c not in cat_stats: cat_stats[c] = {'n':0,'sum_net':0,'sum_vol24':0}
            cat_stats[c]['n'] += 1; cat_stats[c]['sum_net'] += r['net']; cat_stats[c]['sum_vol24'] += r['vol24']
        print(f'{"Category":<15} {"Count":>6} {"AvgNet":>8} {"TotalVol24h":>14}')
        print('-'*50)
        for cat, st in sorted(cat_stats.items(), key=lambda x: -x[1]['n']):
            avg_net = st['sum_net']/st['n']
            print(f'{cat:<15} {st["n"]:>6} {avg_net:>7.1f}% {st["sum_vol24"]:>14,.0f}')

asyncio.run(run())
