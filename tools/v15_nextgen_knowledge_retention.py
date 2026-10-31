"""Retain immutable model associations in a research agenda without merging weights or counts."""
from collections import Counter
import json
from pathlib import Path

from tools import v15_nextgen_interaction_executor as evidence
from tools import v15_nextgen_protection_priority as priority


def source_document(source):
    path = Path(source['path'])
    if path.is_symlink() or not path.is_file() or evidence.sha(path) != source['sha256']:
        raise ValueError('exact immutable regular model source required')
    document = json.loads(path.read_text())
    if evidence.sha(path) != source['sha256']:
        raise ValueError('model source changed while loading')
    return document


def graph_pairs(document):
    metadata = document['_meta']
    columns = metadata['pair_cols']
    if columns[:2] != ['filter', 'row']:
        raise ValueError('unsupported graph pair schema')
    for index, values in enumerate(document['pair']):
        if len(values) != len(columns):
            raise ValueError('graph pair column count differs from declared schema')
        first, second = (document['nodes'][int(values[position])] for position in (0, 1))
        if not first.startswith('F:') or not second.startswith('R:') or '=' not in first or '=' not in second:
            raise ValueError('graph pair endpoints do not match F to template-row schema')
        field_x, value_x = second[2:].split('=', 1)
        field_y, value_y = first[2:].split('=', 1)
        yield dict(cat_side=metadata['cat_side'], x=dict(field=field_x, value=value_x), y=dict(field=field_y, value=value_y), source_kind='exported_graph_association', source_index=index, source_metadata=dict(first_ts=metadata.get('first_ts'), last_ts=metadata.get('last_ts'), at=metadata.get('at')), raw_model_evidence=dict(zip(columns[2:], values[2:])))


def neural_pairs(document):
    metadata = document['meta']
    for index, proposal in enumerate(document.get('template_untried') or []):
        if not proposal.get('filter'):
            continue
        yield dict(cat_side=metadata['cat_side'], x=dict(field=proposal['switch'], value=proposal['cand']), y=dict(field=proposal['filter'], value=proposal['opt']), tab=proposal.get('tab'), source_kind='neural_prediction', source_index=index, source_metadata={key: metadata.get(key) for key in ('schema', 'built_at', 'model_trained_at', 'corpus_last_ts')}, raw_model_evidence=proposal)


def build_catalog(sources, current_domain, sink):
    counters, combined = Counter(), {}
    for source in sources:
        document = source_document(source)
        iterator = graph_pairs(document) if source['kind'] == 'graph' else neural_pairs(document) if source['kind'] == 'neural_proposals' else None
        if iterator is None:
            raise ValueError('explicit supported model source kind required')
        for raw in iterator:
            counters['source_associations'] += 1
            resolved = current_domain(raw)
            if not isinstance(resolved, dict) or resolved.get('eligible') is not True:
                reason = (resolved or {}).get('reason', 'unresolved_current_domain')
                counters['diagnostic:' + reason] += 1
                sink(dict(status='preserved_diagnostic_not_runtime_eligible', source=source, raw=raw, reason=reason))
                continue
            if not resolved.get('evidence_refs') or not resolved.get('x') or not resolved.get('y'):
                raise ValueError('typed exact current-domain bindings and source refs required')
            for reference in resolved['evidence_refs']:
                if evidence.sha(reference['path']) != reference['sha256']:
                    raise ValueError('current-domain binding source changed')
            x, y = resolved['x'], resolved['y']
            if x['field'] == y['field']:
                counters['diagnostic:same_variable_identity'] += 1
                sink(dict(status='preserved_diagnostic_not_distinct_interaction', source=source, raw=raw))
                continue
            identity = evidence.guard.fingerprint(dict(cat_side=raw['cat_side'], pair=evidence.pair_identity(x, y)))
            item = combined.setdefault(identity, dict(id='retained:' + identity, cat_side=raw['cat_side'], x=x, y=y, current_binding=resolved, retained_sources=[], status='MODEL_ASSOCIATION_NOT_VERIFIED_CURRENT_INTERACTION', current_confirmation_verified=False, can_suppress_test=False, baseline_modified=False, requires_four_actual_components=True))
            item['retained_sources'].append(dict(artifact=source, event=raw))
        if evidence.sha(source['path']) != source['sha256']:
            raise ValueError('model artifact changed during catalog build')
    for identity in sorted(combined):
        sink(combined[identity])
    counters['unique_current_domain_pairs'] = len(combined)
    return dict(schema='immutable_knowledge_retention_catalog/1', counts=dict(counters), source_artifacts=sources, semantics='source associations retained verbatim, never summed as independent measured evidence; no model weights or optimizer state changed', all_raw_training_rows_absorbed=False, production_hooked=False)


def allocate_retained(experiments, state, context, evaluation_budget, current_domain, selection_contract, measured=()):
    if any(experiment.get('can_suppress_test') is not False or experiment.get('current_confirmation_verified') is not False for experiment in experiments):
        raise ValueError('retained model associations cannot assert current verified benefit or suppress tests')
    return priority.allocate(experiments, state, context, evaluation_budget, current_domain, selection_contract, measured=measured)
