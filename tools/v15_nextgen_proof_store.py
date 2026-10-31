"""Lossless review format; original raw worker files are never changed or removed."""
import copy
import hashlib
import json
from pathlib import Path
import zlib
import os
import uuid

from tools import v15_nextgen_baseline_guard as guard


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def put(directory, value):
    payload=canonical(value)
    identity=digest(payload)
    path=Path(directory)/'blobs'/(identity+'.json.z')
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if zlib.decompress(path.read_bytes())!=payload:
            raise ValueError('existing content-addressed proof block differs')
    else:
        temporary=path.with_name(path.name+'.tmp_'+uuid.uuid4().hex)
        with temporary.open('xb') as stream:
            stream.write(zlib.compress(payload,6))
        try:
            os.link(temporary,path)
        except FileExistsError:
            if zlib.decompress(path.read_bytes())!=payload:
                raise ValueError('concurrent content-addressed block differs')
        finally:
            temporary.unlink()
    return dict(sha256=identity,bytes=len(payload))


def get(directory, reference):
    payload=zlib.decompress((Path(directory)/'blobs'/(reference['sha256']+'.json.z')).read_bytes())
    if len(payload)!=reference['bytes'] or digest(payload)!=reference['sha256']:
        raise ValueError('proof content-address/length mismatch')
    return json.loads(payload)


def pack(raw_source, baseline_configs, directory):
    payload=Path(raw_source['path']).read_bytes()
    if digest(payload)!=raw_source['sha256']:
        raise ValueError('raw source proof changed')
    return pack_payload(payload,raw_source['sha256'],baseline_configs,directory)


def pack_payload(payload,raw_sha256,baseline_configs,directory):
    raw_source={'sha256':raw_sha256}
    if digest(payload)!=raw_sha256:
        raise ValueError('raw proof payload identity differs')
    record=json.loads(payload)
    if record.get('schema')!='nextgen_actual_worker_measurement/1' or canonical(record)!=payload:
        raise ValueError('exact canonical raw worker format required for original byte-hash reconstruction')
    configs=record['actual_engine_proof']['effective_config_values']
    if guard.fingerprint(guard.decoded(configs))!=record['actual_engine_proof']['effective_config_sha256']:
        raise ValueError('raw actual config proof mismatch')
    base=put(directory,baseline_configs)
    if len(configs)==len(baseline_configs):
        delta=[]
        for original,current in zip(baseline_configs,configs):
            delta.append(dict(remove=[key for key in original if key not in current],set={key:value for key,value in current.items() if key not in original or canonical(value)!=canonical(original[key])}))
        config=dict(base=base,delta=put(directory,delta))
    else:
        config=dict(full=put(directory,configs))
    remainder=copy.deepcopy(record)
    proof=remainder.pop('actual_engine_proof')
    proof.pop('effective_config_values')
    result=remainder.pop('result')
    requested=remainder.pop('requested_settings')
    envelope=dict(schema='nextgen_lossless_worker_proof/1',original_raw_sha256=raw_source['sha256'],original_raw_bytes=len(payload),record=remainder,proof=proof,config=config,requested=put(directory,requested),result_parts={key:put(directory,value) for key,value in result.items()},semantics='exact typed config reconstruction and original raw byte-hash required; source file preserved')
    path=Path(directory)/'records'/(raw_source['sha256']+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    encoded=canonical(envelope)
    if path.exists():
        if path.read_bytes()!=encoded:
            raise ValueError('existing proof envelope differs')
    else:
        with path.open('xb') as stream:
            stream.write(encoded)
    if unpack(path,directory)!=payload:
        raise ValueError('lossless proof comparison failed')
    return dict(path=str(path),sha256=digest(encoded))


def unpack(path,directory):
    envelope=json.loads(Path(path).read_bytes())
    if envelope.get('schema')!='nextgen_lossless_worker_proof/1':
        raise ValueError('unsupported compact proof format')
    config=envelope['config']
    if 'full' in config:
        configs=get(directory,config['full'])
    else:
        configs=get(directory,config['base'])
        delta=get(directory,config['delta'])
        if len(configs)!=len(delta):
            raise ValueError('config delta dimensions differ')
        for current,changes in zip(configs,delta):
            for key in changes['remove']:
                del current[key]
            current.update(changes['set'])
    if guard.fingerprint(guard.decoded(configs))!=envelope['proof']['effective_config_sha256']:
        raise ValueError('reconstructed typed effective config hash differs')
    record=copy.deepcopy(envelope['record'])
    record['actual_engine_proof']=dict(envelope['proof'],effective_config_values=configs)
    record['requested_settings']=get(directory,envelope['requested'])
    record['result']={key:get(directory,reference) for key,reference in envelope['result_parts'].items()}
    raw=canonical(record)
    if len(raw)!=envelope['original_raw_bytes'] or digest(raw)!=envelope['original_raw_sha256']:
        raise ValueError('reconstructed raw proof original hash differs')
    return raw
