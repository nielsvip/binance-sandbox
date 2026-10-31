import json

import pytest

from tools import v15_nextgen_proof_store as S


def test_exact_typed_delta_and_original_byte_hash_reconstruction(tmp_path):
    configs=[{'flag':False,'numeric':0,'added':None}]
    record=dict(schema='nextgen_actual_worker_measurement/1',observed_at='2026-01-01T00:00:00.000000Z',actual_engine_proof=dict(effective_config_values=configs,effective_config_sha256=S.guard.fingerprint(configs)),result=dict(execution_ledger=[],ledger=[],valid=True),requested_settings={'flag':False})
    raw=tmp_path/'raw.json'
    raw.write_bytes(S.canonical(record))
    original=raw.read_bytes()
    store=tmp_path/'cas'
    envelope=S.pack(dict(path=str(raw),sha256=S.digest(original)),[{'flag':0,'numeric':False,'removed':'old'}],store)
    assert S.unpack(envelope['path'],store)==original
    doc=json.loads(open(envelope['path']).read())
    assert doc['result_parts']['ledger']==doc['result_parts']['execution_ledger']
    before={str(path):path.read_bytes() for path in store.rglob('*') if path.is_file()}
    S.pack(dict(path=str(raw),sha256=S.digest(original)),[{'flag':0,'numeric':False,'removed':'old'}],store)
    assert before=={str(path):path.read_bytes() for path in store.rglob('*') if path.is_file()}
    assert raw.read_bytes()==original
    changed=json.loads(open(envelope['path']).read())
    changed['proof']['effective_config_sha256']='0'*64
    open(envelope['path'],'w').write(json.dumps(changed))
    with pytest.raises(ValueError,match='typed effective config'):
        S.unpack(envelope['path'],store)
