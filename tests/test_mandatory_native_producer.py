import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('path',['DC','WT'])
def test_actual_native_override_producer_queues_expected_open(side,path):
    tree=ast.parse(Path('ez_manage.py').read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_process_single_override_check')
    is_long=side=='LONG';key=f'ang:TEST_{side}';orders=[];logs=[]
    cfg=SimpleNamespace(TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED=True,WT_3M_FORCE_OPEN_ENABLED=path=='WT',WT_3M_FORCE_OPEN_GR_GATE_ENABLED=False,WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS=False,USE_1M_3M_SIGNALS_ENABLED=False)
    indicators={'dc_high_15m':99. if path=='DC' and is_long else 110.,'dc_low_15m':101. if path=='DC' and not is_long else 90.,'k_15m':60. if is_long else 40.,'k_15m_prev':50.,'sma_200_15m':90. if is_long else 110.,'wt1_15m':2. if is_long else -2.,'wt2_15m':0.,'wt1_1h':2. if is_long else -2.,'wt2_1h':0.}
    async def ii(*args):return dict(indicators)
    async def price(*args):return 100.
    async def queue(*args):orders.append((args[3],args[4]))
    def number(value,default=None):
        try:return float(value) if value is not None else default
        except (ValueError,TypeError):return default
    scope={'_vx_native_off':lambda:False,'parse_position_key':lambda key:('ang','TEST',side),'ii':ii,'price':price,'safe_fetch_float':number,'_psym_get':lambda symbol,side,name,default:getattr(cfg,name,default),'_ezm_is_live_side_enabled':lambda *args:(True,'TEST'),'_psym_sps':lambda *args:9.,'_recent_opens':{},'_DUPLICATE_OPEN_COOLDOWN':60.,'time':SimpleNamespace(time=lambda:1000.),'queue_trade_action':queue,'logger':SimpleNamespace(warning=lambda *args:logs.append(str(args)),debug=lambda *args:logs.append(str(args)),error=lambda *args:logs.append(str(args))),'_twin_entry_ports_b':None}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<actual native producer>','exec'),scope)
    manager=SimpleNamespace(tradeable_keys={key})
    asyncio.run(scope[fn.name](manager,'ang',key,{},object(),1000.,cfg))
    expected='TRADEABLE_KEYS_MANDATORY_' if path=='DC' else 'WT_3M_FORCE_OPEN_'
    assert len(orders)==1 and orders[0][0]=='OPEN' and orders[0][1].startswith(expected), (orders,logs)
    cfg.TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED=False;orders.clear()
    asyncio.run(scope[fn.name](manager,'ang',key,{},object(),1000.,cfg))
    assert orders==[]
