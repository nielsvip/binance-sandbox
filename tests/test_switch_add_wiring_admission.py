import copy
import json
from pathlib import Path
import pytest
from tools.v15_switch_add import validate_switch, Refused

SWITCH='TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED'
def entry():
    source=json.loads(Path('data/SWITCH_BIBLE.json').read_text())['switches'][SWITCH]
    source=copy.deepcopy(source)
    source['status']={'crypto':'WIRED_BOTH_UNPROVEN','stocks':'WIRED_BOTH_UNPROVEN'}
    source['live_read_count']={'crypto':1,'stocks':1}
    source['vec_read_count']={'reachable':1,'total':1}
    return source

def validate(source,venue='CRYPTO'):
    return validate_switch(SWITCH,'ENTRY_BREAKOUT_CHANNEL',['False','True'],'False',[venue],['LONG','SHORT'],bible={SWITCH:source})

@pytest.mark.parametrize('counts',[{'reachable':0,'total':0},{'reachable':0,'total':3},{}])
def test_truthy_count_dictionary_cannot_admit_unreachable_twin(counts):
    source=entry();source['vec_read_count']=counts
    with pytest.raises(Refused,match='no reachable vec reads'):validate(source)

@pytest.mark.parametrize('venue',['CRYPTO','STOCKS'])
@pytest.mark.parametrize('status',['DEAD','LIVE_ONLY','VEC_ONLY','NOT_IN_CONFIG',''])
def test_each_requested_venue_requires_actual_both_engine_status(venue,status):
    source=entry();source['status']['crypto' if venue=='CRYPTO' else 'stocks']=status
    with pytest.raises(Refused,match='not wired in both engines'):validate(source,venue)

def test_other_venue_live_read_cannot_stand_in_for_requested_venue():
    source=entry();source['live_read_count']['crypto']=0
    with pytest.raises(Refused,match='no CRYPTO live reads'):validate(source)

def test_unproven_wired_path_can_be_staged_without_fabricating_parity_proof():
    opts,default,sides,_=validate(entry())
    assert opts==[False,True] and default is False and sides==['LONG','SHORT']
