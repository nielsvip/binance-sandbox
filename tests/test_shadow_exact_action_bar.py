import json
import pytest
from live_twins.vec_driven import shadow_record,native_trades_on_bar

@pytest.mark.parametrize('wanted,native',[('OPEN','AUGMENT'),('AUGMENT','OPEN'),('REDUCE','CLOSE'),('CLOSE','REDUCE')])
def test_same_direction_is_not_action_parity(wanted,native):
    result=shadow_record('ang',{'type':wanted},[{'type':native}],1.,wanted,'')
    assert result['native_matches'] is False

@pytest.mark.parametrize('wanted,native',[('OPEN','QUICK_OPEN'),('AUGMENT','QUICK_AUGMENT'),('REDUCE','QUICK_REDUCE'),('CLOSE','QUICK_CLOSE')])
def test_action_alias_preserves_action_kind(wanted,native):
    assert shadow_record('ang',{'type':wanted},[{'type':native,'qty':999.}],1.,wanted,'')['native_matches'] is True

def test_extra_native_action_does_not_look_like_exact_single_intent():
    assert shadow_record('ang',{'type':'OPEN'},[{'type':'OPEN'},{'type':'CLOSE'}],1.,'OPEN','')['native_matches'] is False

def test_history_window_keeps_only_same_bar(tmp_path):
    p=tmp_path/'history.jsonl';bar=1700000100
    p.write_text('\n'.join(json.dumps({'ts':ts,'type':'OPEN'}) for ts in (bar-1,bar,bar+899,bar+900))+'\n')
    rows=native_trades_on_bar(p,bar,900)
    assert [r['ts'] for r in rows]==[bar,bar+899]
