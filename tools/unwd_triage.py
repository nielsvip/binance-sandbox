#!/usr/bin/env python3
"""UNW-D triage of dead-in-both rows + *_FILTER_TF families (evidence from code, 2026-10-01)."""
import ast,csv,json,glob,re,collections,os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
rep=list(csv.DictReader(open('data/wiring/unwired_report_20261001.csv')))
dead=[r for r in rep if r['class'].startswith('NEITHER')]
fam=[r for r in rep if r['class']=='FILTER_TF_FAMILY']
cov=list(csv.DictReader(open('data/rowcoverage/latest.csv')))
tabs=collections.defaultdict(lambda:{'tabs':set(),'templates':set()})
for r in cov:
    if r['status'] in('NOT_WIRED_VEC','NEEDS_LIVE'):
        tabs[r['switch']]['tabs'].add(r['tab']); tabs[r['switch']]['templates'].add(r['template'])
LIVEF=[f for f in glob.glob('ez_*.py')+glob.glob('tradier_*.py')+glob.glob('live_*.py') if not f.endswith('_sandbox.py')]
VECF=['v12_quick_engine.py','backtest_v12_engine.py']+glob.glob('vec_decisions/*.py')
src={f:open(f,errors='ignore').read().split('\n') for f in set(LIVEF+VECF)}
fmap={}
for f in LIVEF:
    try: t=ast.parse('\n'.join(src[f]))
    except Exception: continue
    fmap[f]=[(n.lineno,n.end_lineno,n.name) for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
def enc(f,i):
    c=[s for s in fmap.get(f,[]) if s[0]<=i<=s[1]]
    return min(c,key=lambda s:s[1]-s[0])[2] if c else '<module>'
FARM_FN={'_ensure_ez_all','_ensure_tradier_all','_v12_b11_reentry_allowed'}
DISABLED_FN={'_batch1_template_live_gate','_batch1_template_live_gate_tradier'}
QUICK_FN={'rate','_scalp_v3_protective_exits','_scalp_v3_attempt_be_stops','_btc_build_features_from_indicators','_btc_dedicated_exit_decision','_btc_dedicated_account_blocked','check_stdev_breakout_exit','process_single_exit','process_single_reentry_evaluation_epq','evaluate_reentry_epq','reentry_enforcement_loop_epq','scan_movers','worker','execute_trade_wrapper','get_regime_params','evaluate_obligatory_reentry','refresh_rankings','global_ranker_loop','check_entry_candidates_for_account','check_exit_candidates_for_account'}
REAL_FN={'_compose_reentry_mult','evaluate_reentry','process_single_reentry_evaluation','process_position','execute_now','execute_trade_action','calculate_final_order_quantity','check_reentry_delta_tolerant','_reentry_queue_consumer_loop','band_arrow_score','crypto_spike_fade_loop','_golden_rule_loop','enforce_price_cross_reentry','<module>','send_webhook'}
def site_cat(f,i,line,name):
    s=line.strip(); fn=enc(f,i)
    if f.startswith(('config','v12_','backtest_','vec_decisions')): return None
    if re.fullmatch(r"['\"]"+re.escape(name)+r"['\"],?\s*(#.*)?",s) or (s.count('"')>=6 or s.count("'")>=6): return 'KEYLIST'
    if s.startswith('#') : return 'COMMENT'
    if fn.startswith(('_wire_weak_','_batch','_ensure_')) or fn in FARM_FN: return 'FARM'
    if fn in DISABLED_FN: return 'DISABLED'
    _nxt=''
    for _j in range(i,min(i+3,len(src[f]))):
        if src[f][_j].strip(): _nxt=src[f][_j].strip(); break
    _touchif = bool(re.match(r"if .*getattr\(config(_tradier)?,\s*['\"]"+re.escape(name),s)) and bool(re.match(r"_\s*=",_nxt))
    if re.search(r"\band False\b",s) or re.match(r"_\s*=",s) or re.search(r":\s*_\s*=\s*1\b",s) or _touchif or '_=_' in s.replace(' ','') : return 'STUB'
    if fn in QUICK_FN: return 'QUICK_ABLATED'
    if fn in REAL_FN: return 'REAL'
    return 'UNKNOWN:'+fn
GATE_OF={'rate':'ABLATION_DISABLE_QUICK_ENTRY / QUICK_EXIT / ENTRY_RANKING = True (config.py:2845-2854): rate() is called only from process_single_exit, check_entry_candidates worker, RatingRegistry.refresh_rankings, global_ranker_loop',
'process_single_exit':'ABLATION_DISABLE_QUICK_EXIT = True (ez_positions_quick.py:13786 early return)','worker':'ABLATION_DISABLE_QUICK_ENTRY = True (ez_positions_quick.py:15276 early return)',
'check_stdev_breakout_exit':'called from process_single_exit: ABLATION_DISABLE_QUICK_EXIT = True','_scalp_v3_protective_exits':'SCALP_V3 pass inside the quick-entry loop (ABLATION_DISABLE_QUICK_ENTRY / SCALP_GUARD = True) and needs 1m/3m data',
'_scalp_v3_attempt_be_stops':'same SCALP_V3 pass (ABLATION_DISABLE_QUICK_ENTRY / SCALP_GUARD = True)','_btc_dedicated_exit_decision':'BTC dedicated loop override of rate(): needs BTC_DEDICATED_ENABLED and quick path (ablated)',
'_btc_build_features_from_indicators':'BTC dedicated loop (quick path, ablated)','_btc_dedicated_account_blocked':'BTC dedicated loop (quick path, ablated)','get_regime_params':'ez_regime.get_regime_params is called from rate() only',
'evaluate_obligatory_reentry':'called only from rate()/reentry_enforcement_loop_epq: ABLATION_DISABLE_QUICK_ENTRY / REENTRY_ENFORCE = True','evaluate_reentry_epq':'ABLATION_DISABLE_REENTRY_ENFORCE = True (epq loop)',
'reentry_enforcement_loop_epq':'ABLATION_DISABLE_REENTRY_ENFORCE = True','process_single_reentry_evaluation_epq':'ABLATION_DISABLE_REENTRY_ENFORCE = True','scan_movers':'global_ranker_loop: ABLATION_DISABLE_ENTRY_RANKING = True',
'execute_trade_wrapper':'ez_positions_quick wrapper used by HedgeEngine/quick paths only (ABLATION_DISABLE_HEDGE / QUICK_* = True); live crypto opens go through ez_manage.execute_now, which has no call to it'}
rows=[]
def vec_info(name):
    hits=[]
    for f in VECF:
        for i,l in enumerate(src.get(f,[]),1):
            if re.search(r'\b'+re.escape(name)+r'\b',l) and not re.match(r"\s*"+re.escape(name)+r"\s*:\s*\w+\s*=",l): hits.append(f+':'+str(i))
    return hits
for r in dead:
    n=r['name']; sites=[]
    for f in LIVEF:
        for i,l in enumerate(src[f],1):
            if re.search(r'\b'+re.escape(n)+r'\b',l):
                c=site_cat(f,i,l,n)
                if c and c not in('KEYLIST','COMMENT'): sites.append((c,f,i,enc(f,i),l.strip()[:140]))
    cats=collections.Counter(s[0] for s in sites)
    real=[s for s in sites if s[0]=='REAL']; quick=[s for s in sites if s[0]=='QUICK_ABLATED']; unk=[s for s in sites if s[0].startswith('UNKNOWN')]
    vh=vec_info(n)
    if real:
        cl='RECLASSIFY_LIVE_REAL_VEC_TWIN_MISSING'; ev=real[0]; act='move to LIVE_REAL_VEC_MISSING worker: build vector twin (read at '+f'{ev[1]}:{ev[2]} in {ev[3]})'
    elif quick:
        cl='b_LIVE_FEATURE_EXISTS_BUT_UNREACHABLE'; ev=quick[0]; act='gate: '+GATE_OF.get(ev[3],'quick path')+' | '+('live reads only inside quick/ranking/scalp paths gated OFF in config.py (ABLATION_DISABLE_QUICK_ENTRY/QUICK_EXIT/ENTRY_RANKING/REENTRY_ENFORCE=True); also needs 3m/1m data where marked: decide: keep dead => retire row, or enable ablation flag => vector twin')
    elif unk:
        cl='UNKNOWN_REVIEW'; ev=unk[0]; act='manual review of '+ev[3]
    elif sites:
        cl='c_NO_FEATURE'; ev=sites[0]; act='ghost key: only touch/stub/disabled reads; recommend retire row (user decides)'
    else:
        cl='c_NO_FEATURE'; ev=('NONE','','','',''); act='no live read at all; vector default only; recommend retire row (user decides)'
    rows.append([n,cl,f'{ev[1]}:{ev[2]}' if ev[1] else '', ev[3], ev[4], act, r['rows'], '/'.join(sorted(tabs[n]['templates'])), '/'.join(sorted(tabs[n]['tabs'])), dict(cats), len(vh)])

OVR={'MI_ENTRY_ENABLED':('a_REAL_IN_LIVE_UNDER_ANOTHER_NAME','stocks: live+vector read MI_ENTRY_ENABLED_TRADIER; template row key lacks the _TRADIER suffix: alias for tradier sweeps queued as queue/UNWD/001 (crypto: ghost key, live never reads it)'),
'ULTIMATE_DC_4H_STOP_ENABLED':('e_REAL_FEATURE_ALWAYS_ON_NO_SWITCH','live ULTIMATE_DC hard stop is real and unconditional (ez_manage.py:46716, tradier_manage.py:10680) but never reads this flag: wiring the flag into those two sites is a live change (user go) then a vector twin switch')}
for r in rows:
    if r[0] in OVR: r[1],r[5]=OVR[r[0]]
# FILTER_TF families
BIB=json.load(open('data/SWITCH_BIBLE.json'))['switches']
def master_status(stem):
    keys=[k for k in BIB if (k==stem or k.startswith(stem+'_')) and not k.endswith('_FILTER_TF')]
    out=[]
    for k in keys:
        lreal=0
        pat=re.compile(r'\b'+re.escape(k)+r'\b')
        for f in LIVEF:
            for i,l in enumerate(src[f],1):
                if pat.search(l):
                    c=site_cat(f,i,l,k)
                    if c=='REAL' or (c or '').startswith('UNKNOWN'): lreal+=1
        e=BIB.get(k) or {}
        vreal=(e.get('vec_read_count') or {}).get('reachable',0) or 0
        out.append((k,lreal,vreal))
    return out
famrows=[]
for r in fam:
    n=r['name']; stem=n[:-len('_FILTER_TF')] if n.endswith('_FILTER_TF') else n
    ms=master_status(stem)
    lreal=sum(1 for m in ms if m[1]); vreal=sum(1 for m in ms if m[2])
    _pat=re.compile(re.escape(stem),re.I)
    lcount=sum(len(_pat.findall('\n'.join(src[f]))) for f in LIVEF); vcount=sum(len(_pat.findall('\n'.join(src[f]))) for f in VECF if f in src)
    lreal=lreal or (1 if lcount>=30 else 0); vreal=vreal or (1 if vcount>=30 else 0)
    if lreal and vreal: cl='d_NEEDS_USER_DEFINITION'; act='master module real in live+vector: define what TF filter adds (or retire); do not invent'
    elif lreal: cl='d_NEEDS_USER_DEFINITION'; act='master real in live only: vector twin of master first, then define TF semantics (or retire)'
    else: cl='c_NO_FEATURE'; act='master module has no reachable live read: ghost; recommend retire row'
    if n in('MTF_ATR_TRAIL_FILTER_TF','MTF_DC_REJECT_FILTER_TF'): cl='a_ALIAS_DONE_JSN2'; act='alias to existing TF key deployed in engine 3c5810e7 (JSN2)'
    famrows.append([n,cl,'module keys with real live read: '+str([k for k,l,v in ms if l][:6])+' | with reachable vector read: '+str([k for k,l,v in ms if v][:6])+' | total keys '+str(len(ms))+f' | stem mentions live={lcount} vec={vcount}','','',act,r['rows'],'/'.join(sorted(tabs[n]['templates'])),'/'.join(sorted(tabs[n]['tabs'])),{},0])
allrows=rows+famrows
os.makedirs('data/wiring/unw',exist_ok=True)
with open('data/wiring/unw/DEAD_TRIAGE.csv','w',newline='') as f:
    w=csv.writer(f); w.writerow(['name','class','evidence_file_line','enclosing_function','evidence_line','proposed_action','rows_affected','templates','tabs','site_categories','vec_hits']); w.writerows(allrows)
c=collections.Counter(); rr=collections.Counter()
for a in allrows: c[a[1]]+=1; rr[a[1]]+=int(a[6])
print(c); print(rr)
json.dump([a for a in allrows],open('/tmp/unwd_final.json','w'),default=str)
