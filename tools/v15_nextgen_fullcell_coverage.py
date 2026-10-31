"""Full Cartesian diagnostic coverage; real evaluator only, no publication or promotion."""
from collections import Counter
import copy
from datetime import datetime, timezone
import gzip
import json
import multiprocessing
from pathlib import Path
import re
import sqlite3
import time
import os

from tools import v15_nextgen_baseline_guard as guard

PREPARED = None
SETTINGS = None
CONFIG = None
PILOT = None
CONTEXT = None
CAPTURE = None


def sha(path):
    import hashlib
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):
            result.update(block)
    return result.hexdigest()


def catalog(template_sources, output):
    import openpyxl
    counts={}
    factored={}
    compact=str(output).endswith('.json')
    import contextlib
    with (contextlib.nullcontext(None) if compact else gzip.open(output,'wt',compresslevel=1)) as sink:
        for category,source in template_sources.items():
            path=Path(source['path'])
            if path.is_symlink() or sha(path)!=source['sha256']:
                raise ValueError('current template source changed')
            workbook=openpyxl.load_workbook(path,read_only=True,data_only=True)
            rows,headers=[],{}
            try:
                for sheet in workbook:
                    if sheet.title not in ('STDEV_SLOPE_SIZING','ENTRY_REVERSAL_BOUNCE','ENTRY_BREAKOUT_CHANNEL','ENTRY_CONFIRMATION_GATES','EXIT_STRUCTURAL','EXIT_VELOCITY','REENTRY_WINDOWED','REENTRY_ADAPTIVE','AUGMENT_TREND','AUGMENT_RISK_SIZING','REDUCE_PROFIT_LOCK','REDUCE_SIGNAL_RATER','GLOBAL_RISK_GATES'):
                        continue
                    for number,values in enumerate(sheet.iter_rows(values_only=True),1):
                        if number==2:
                            for column,header in enumerate(values,1):
                                if isinstance(header,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*',header.strip()):
                                    headers.setdefault(header.strip(),[]).append(dict(tab=sheet.title,column=column))
                        elif number>=3 and values and values[0] not in (None,''):
                            rows.append(dict(tab=sheet.title,row=number,switch=str(values[0]).strip(),cand=values[1] if len(values)>1 else None))
            finally:
                workbook.close()
            factored[category]=dict(source=source,rows=rows,headers=headers)
            for row in ([] if compact else rows):
                for header,columns in sorted(headers.items()):
                    field,option=header.split('=',1)
                    record=dict(cat_side=category,template_sha256=source['sha256'],**row,filter=field,opt=option,header=header,filter_column_refs=columns,physical_cell_present=any(item['tab']==row['tab'] for item in columns))
                    record['catalog_cell_id']=guard.fingerprint(record)
                    sink.write(json.dumps(record,separators=(',',':'))+'\n')
            counts[category]=dict(rows=len(rows),filter_option_columns=len(headers),requested_cells=len(rows)*len(headers))
            if sha(path)!=source['sha256']:
                raise ValueError('template changed during exhaustive catalog construction')
    if compact:
        Path(output).write_text(json.dumps(dict(schema='factored_every_row_every_filter/1',categories=factored),separators=(',',':')))
    return dict(schema='every_row_every_filter_catalog/1',sources=template_sources,counts=counts,catalog=dict(path=str(output),sha256=sha(output)),yellow_restriction=False,unwired_or_grey_exclusion=False,promotion_min_distinct_proven_results=101,all_cells_measured=False)


def iter_cells(catalog_path,wanted_category=None,ordering='row-major'):
    if str(catalog_path).endswith('.gz'):
        with gzip.open(catalog_path,'rt') as stream:
            for line in stream:
                yield json.loads(line)
        return
    document=json.loads(Path(catalog_path).read_text())
    for category,part in document['categories'].items():
        if wanted_category is not None and category!=wanted_category:
            continue
        headers=sorted(part['headers'].items())
        if ordering=='diagonal':
            combinations=((row,headers[(number+round_index)%len(headers)]) for round_index in range(len(headers)) for number,row in enumerate(part['rows']))
        elif ordering=='row-major':
            combinations=((row,item) for row in part['rows'] for item in headers)
        else:
            raise ValueError('explicit supported catalog ordering required')
        for row,(header,columns) in combinations:
            field,option=header.split('=',1)
            record=dict(cat_side=category,template_sha256=part['source']['sha256'],**row,filter=field,opt=option,header=header,filter_column_refs=columns,physical_cell_present=any(item['tab']==row['tab'] for item in columns))
            record['catalog_cell_id']=guard.fingerprint(record)
            yield record


def work(cell):
    from tools import switch_types
    from tools.v15_nextgen_pilot_consumer import evaluate_fork_worker
    started=time.monotonic()
    engine_started=False
    stop=os.environ.get('V15_COVERAGE_STOP_FILE')
    if (stop and Path(stop).exists()) or (CONTEXT.get('stop_deadline_monotonic') and time.monotonic()>=CONTEXT['stop_deadline_monotonic']):
        return dict(cell=cell,status='deferred_budget_boundary_not_executed',proven_actual_result=False,engine_execution_attempted=False,promotion_allowed=False)
    result=dict(cell=cell,status='not_applied',promotion_allowed=False,phase_status='diagnostic_accepted_baseline_KG_incomplete_no_publication')
    try:
        if cell['cand'] is None:
            raise ValueError('CANDIDATE_MISSING_NOT_IMPUTED')
        switch=cell['switch']
        overrides=PILOT._switch_overrides(switch,PILOT._parse_opt_value(cell['cand'],CONFIG.get(switch)))
        if not overrides:
            raise ValueError('NO_ACTUAL_ROW_OPERATION')
        if cell['filter'] in overrides:
            raise ValueError('SELF_FIELD_CONFLICT_NOT_TWO_VARIABLES')
        requested={**overrides,cell['filter']:cell['opt']}
        typed={}
        for field,value in requested.items():
            if field not in CONFIG:
                raise ValueError('UNPROVEN_NOT_APPLIED: actual engine field absent '+field)
            ok,converted,reason=switch_types.coerce(field,value,'crypto' if cell['cat_side'].startswith('CRYPTO') else 'tradier',strict_unknown=True,root=Path(CONTEXT['runtime_root']))
            if not ok:
                raise ValueError('TYPE_DOMAIN_INAPPLICABLE: '+str(reason))
            typed[field]=converted
        settings=dict(SETTINGS)
        settings.update(typed)
        result['requested_settings_sha256']=guard.fingerprint(settings)
        logical=dict(sym_side=CONTEXT['sym_side'],cat_side=cell['cat_side'],template_sha256=cell['template_sha256'],tab=cell['tab'],row=cell['row'],switch=switch,cand=typed.get(switch,overrides),filter=cell['filter'],opt=typed[cell['filter']],engine_sha256=CONTEXT['engine_sha256'])
        category_logical={key:value for key,value in logical.items() if key!='sym_side'}
        result['category_cell_key']=guard.fingerprint(category_logical)
        result['sym_side_cell_key']=guard.fingerprint(logical)
        result['logical_cell_id']=result['sym_side_cell_key']
        result['trial_id']=guard.fingerprint(dict(logical_cell_id=result['logical_cell_id'],npz_sha256=CONTEXT['npz_sha256'],input_cutoff_sha256=CONTEXT['input_cutoff_sha256'],window=CONTEXT['window'],accepted_chain_sha256=guard.fingerprint(SETTINGS)))
        engine_started=True
        measured=evaluate_fork_worker(copy.deepcopy(PREPARED),settings,CONTEXT['window_days'],CAPTURE)
        proof_source=CAPTURE['last_proof_source']
        if proof_source['encoding']=='cas-v1':
            from tools.v15_nextgen_proof_store import unpack
            source=json.loads(unpack(proof_source['path'],proof_source['store_directory']))
        else:
            source=json.loads(Path(proof_source['path']).read_text())
        proof=source['actual_engine_proof']
        actual=guard.decoded(proof['effective_config_values'])
        if len(actual)!=1:
            raise ValueError('AMBIGUOUS_ACTUAL_ENGINE_CONFIG')
        mismatches={field:dict(requested=guard.normalized(value),effective=guard.normalized(actual[0].get(field))) for field,value in typed.items() if guard.fingerprint(value)!=guard.fingerprint(actual[0].get(field))}
        result.update(actual_proof=proof_source,engine_proof={key:proof[key] for key in ('effective_config_sha256','execution_ledger_sha256','input_cutoff_sha256','result_sha256')},requested_effective_mismatches=mismatches,raw_gain_pct=guard.normalized(measured.get('gain_pct')),raw_valid=measured.get('valid'),invalid_reason=measured.get('invalid_reason'),status='measured')
        gain=measured.get('gain_pct')
        import math
        execution_ok=type(measured.get('valid')) is bool and isinstance(gain,(int,float)) and math.isfinite(gain) and (measured['valid'] or str(measured.get('invalid_reason') or '').startswith(('trades ','TIM ','DD ','max_dd ')))
        if mismatches:
            result['status']='unproven_not_applied_authority_mismatch'
        elif not execution_ok:
            result['status']='invalid_or_failed_execution_not_zero'
        else:
            result['proven_actual_result']=True
            result['ledger_change']=proof['execution_ledger_sha256']!=CONTEXT['baseline_ledger_sha256']
            result['status']='measured_ledger_changed' if result['ledger_change'] else 'measured_no_ledger_change'
            result['eligible_for_promotion']=False
    except BaseException as error:
        result.update(reason=str(error),proven_actual_result=False,status='failed_execution' if engine_started else 'not_applied')
    result['engine_execution_attempted']=engine_started
    result['wall_seconds']=time.monotonic()-started
    return result


def run(prepared,settings,config,pilot,context,capture,catalog_path,output,workers,limit=0,shard=(0,1),maximum_seconds=0,start_index=0,ordering='row-major',skip_attempts=()):
    global PREPARED,SETTINGS,CONFIG,PILOT,CONTEXT,CAPTURE
    PREPARED,SETTINGS,CONFIG,PILOT,CONTEXT,CAPTURE=prepared,settings,config,pilot,dict(context),capture
    if maximum_seconds:
        CONTEXT['stop_deadline_monotonic']=time.monotonic()+maximum_seconds
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    ledger=sqlite3.connect(output/'coverage.sqlite')
    ledger.execute('CREATE TABLE results(trial_id TEXT PRIMARY KEY,logical_cell_id TEXT,status TEXT,proof_path TEXT,record TEXT)')
    counts=Counter()
    catalog_digest=sha(catalog_path)
    receipt={}
    started=time.monotonic()
    tasks_staged=0
    skip_ids=set()
    for source in skip_attempts:
        if sha(source['path'])!=source['sha256']:
            raise ValueError('prior attempt source hash mismatch')
        with Path(source['path']).open() as stream:
            for line in stream:
                old=json.loads(line)
                if not old.get('proven_actual_result') or old.get('cell',{}).get('cat_side')!=context['cat_side']:
                    continue
                expected_trial=guard.fingerprint(dict(logical_cell_id=old['logical_cell_id'],npz_sha256=context['npz_sha256'],input_cutoff_sha256=context['input_cutoff_sha256'],window=context['window'],accepted_chain_sha256=guard.fingerprint(settings)))
                if old.get('trial_id')!=expected_trial:
                    continue
                reference=old['actual_proof']
                if sha(reference['path'])!=reference['sha256']:
                    raise ValueError('prior actual proof envelope changed')
                if reference.get('encoding')=='cas-v1':
                    from tools.v15_nextgen_proof_store import unpack
                    raw=json.loads(unpack(reference['path'],reference['store_directory']))
                else:
                    raw=json.loads(Path(reference['path']).read_text())
                result=guard.decoded(raw['result'])
                if raw['npz_sha256']!=context['npz_sha256'] or raw['engine_sha256']!=context['engine_sha256'] or result.get('symside')!=context['sym_side'] or any(raw['actual_engine_proof'][key]!=value for key,value in old['engine_proof'].items()):
                    raise ValueError('prior actual proof does not bind requested current trial')
                skip_ids.add(old['cell']['catalog_cell_id'])
        if sha(source['path'])!=source['sha256']:
            raise ValueError('prior attempt source changed while validating reuse')
    def tasks():
        index=0
        for ordinal,cell in enumerate(iter_cells(catalog_path,context['cat_side'],ordering=ordering)):
            stop=os.environ.get('V15_COVERAGE_STOP_FILE')
            if (stop and Path(stop).exists()) or (maximum_seconds and time.monotonic()-started>=maximum_seconds):
                break
            if ordinal<start_index:
                continue
            cell['catalog_ordinal']=ordinal
            if cell['catalog_cell_id'] in skip_ids:
                counts['verified_prior_trial_not_reexecuted']+=1
                continue
            if cell['cat_side']!=context['cat_side']:
                continue
            if int(cell['catalog_cell_id'][:16],16)%shard[1]!=shard[0]:
                continue
            if limit and index>=limit:
                break
            index+=1
            yield cell
    with multiprocessing.get_context('fork').Pool(workers) as pool, (output/'cell_attempts.jsonl').open('x') as sink:
        for result in pool.imap_unordered(work,tasks(),chunksize=1):
            counts['accounted_cell_records']+=1
            counts['attempted_cells']+=int(result['status']!='deferred_budget_boundary_not_executed')
            counts['actual_engine_execution_attempts']+=int(result.get('engine_execution_attempted',False))
            counts['last_catalog_ordinal']=max(counts['last_catalog_ordinal'],result['cell']['catalog_ordinal'])
            counts[result['status']]+=1
            if result.get('proven_actual_result'):
                cursor=ledger.execute('INSERT OR IGNORE INTO results VALUES(?,?,?,?,?)',(result['trial_id'],result['logical_cell_id'],result['status'],result['actual_proof']['path'],json.dumps(result,separators=(',',':'))))
                counts['distinct_proven_results']+=cursor.rowcount
                counts['duplicate_proven_trial_not_counted']+=1-cursor.rowcount
            sink.write(json.dumps(result,separators=(',',':'))+'\n')
            sink.flush()
            ledger.commit()
            receipt=dict(schema='fullcell_actual_coverage_progress/1',observed_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),counts=dict(counts),source_context=context,catalog_sha256=catalog_digest,workers=workers,shard_index=shard[0],shard_count=shard[1],catalog_start_index=start_index,ordering=ordering,prior_attempt_sources=list(skip_attempts),partial=True,promotion_threshold=101,promotion_enabled=False,scope='breadth-first single frozen context; repeated same trial never increases evidence; failed/skipped/not-applied never zero')
            (output/'progress.json').write_text(json.dumps(receipt,indent=2))
    ledger.close()
    return receipt
