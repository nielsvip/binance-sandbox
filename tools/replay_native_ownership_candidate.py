import hashlib, importlib.util, json, os, sys, time, traceback
from pathlib import Path

root=Path(__file__).resolve().parent
code=Path(os.environ.get("REPLAY_CODE_ROOT",root/"runtime"/"code")).resolve()
input_dir=root/"input"
out=Path(os.environ.get("REPLAY_OUTPUT_ROOT",root/"output")).resolve()
db_path=Path(os.environ.get("REPLAY_DB_PATH",code/"data"/"hourly_reconfig"/"per_sym_store.db")).resolve()
active_path=input_dir/"1000PEPEUSDC_LONG.json"
npz_path=input_dir/"1000PEPEUSDC.npz"
account=os.environ.get("PAIR_ACCOUNT","ang").lower()
if account not in {"ang","inf"}: raise ValueError(f"unsupported account: {account}")

def sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as stream:
  for block in iter(lambda:stream.read(1024*1024),b""): h.update(block)
 return h.hexdigest()

def load_helper(path,name):
 spec=importlib.util.spec_from_file_location(name,path)
 if spec is None or spec.loader is None: raise RuntimeError(f"cannot load replay helper: {path}")
 module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

os.environ["BASE_PATH"]=str(code)
os.environ["V12_BASE_PATH"]=str(code)
os.environ["V12_NPZ_DIR"]=str(input_dir)
os.environ["PER_SYM_STORE_DB"]=str(db_path)
os.environ["VD_MAC_POSITIONS"]="1"
os.environ["ORDER_DEDUPE_LEDGER_DIR"]=str(out/"order_dedupe")
os.environ["V12_REAL_EXECUTE"]="1"
os.environ["V12_COMPLETED_BAR_SECONDS"]="900"
os.environ["V12_REPLAY_NATIVE_OWNERSHIP"]="1"
os.environ["V12_REPLAY_NATIVE_SETTINGS"]="1"
os.environ["V12_REPLAY_OVERRIDE_CHECK_CADENCE"]="per_bar"
os.environ["V8_RATE_GUARD_DISABLED"]="1"
os.environ["V12_SIM_ACCOUNT"]=account
os.environ["PYTHONDONTWRITEBYTECODE"]="1"
if not code.is_dir() or not active_path.is_file() or not npz_path.is_file(): raise RuntimeError("isolated runtime inputs are incomplete")
sys.path.insert(0,str(code))
redirect=load_helper(code/"tools"/"replay_log_redirect.py","replay_log_redirect")
redirect_result=redirect.install_replay_log_redirect(out/"logs")
guard=load_helper(code/"tools"/"replay_runtime_guard.py","replay_runtime_guard")
prepaths=guard.assert_runtime_paths(code,root,{"BASE_PATH":os.environ["BASE_PATH"],"V12_BASE_PATH":os.environ["V12_BASE_PATH"],"PER_SYM_STORE_DB":os.environ["PER_SYM_STORE_DB"],"V12_NPZ_DIR":input_dir,"active_file":active_path,"log_root":out/"logs","dedupe_root":out/"order_dedupe"})
import per_trade_logger
ptroot=out/"native_state"/"per_trade_logger"
per_trade_logger._BASE=ptroot
per_trade_logger._OUT_DIR=ptroot/"per_trade_returns"
per_trade_logger._ATTEMPT_DIR=ptroot/"per_trade_attempts"
per_trade_logger._IDEM_DIR=ptroot/"per_trade_returns"/"_idem"
for path in (per_trade_logger._BASE,per_trade_logger._OUT_DIR,per_trade_logger._ATTEMPT_DIR,per_trade_logger._IDEM_DIR): Path(path).mkdir(parents=True,exist_ok=True)
import config as config_module
import ez_manage as ez_manage_module
import ez_positions_quick as ez_positions_module
import backtest_v12_engine as engine_module
import per_sym_store as per_sym_store_module
import stale_set_halt as stale_set_halt_module
from live_twins import vec_exact as vec_exact_module
from tools import mac_positions_push_executors as mac_positions_module
if Path(per_sym_store_module.DB_PATH).resolve()!=Path(os.environ["PER_SYM_STORE_DB"]).resolve(): raise RuntimeError("per_sym_store resolved outside run-owned frozen DB")
state_modules={"per_trade_logger":per_trade_logger,"config":config_module,"ez_manage":ez_manage_module}
runtime=guard.assert_native_runtime(code,root,active_path,{"config":config_module,"ez_manage":ez_manage_module,"ez_positions_quick":ez_positions_module,"backtest_v12_engine":engine_module,"per_sym_store":per_sym_store_module,"stale_set_halt":stale_set_halt_module,"live_twins.vec_exact":vec_exact_module,"per_trade_logger":per_trade_logger})
from tools.replay_ownership import configure_replay_ownership,replay_ownership_scope
import asyncio
ownership_probe={}
@replay_ownership_scope
async def _ownership_probe():
 token=ez_manage_module._psym_ctx_set("1000PEPEUSDC","LONG")
 try:
  before_exact=ez_manage_module._vec_exact_mode_on()
  before_sym=ez_manage_module._psym_get("1000PEPEUSDC","LONG","PARITY_VEC_EXACT_MODE",True)
  risk_before=ez_manage_module._psym_get("1000PEPEUSDC","LONG","MIN_GAIN_TO_BUY_AGGRESSIVELY",None)
  layer_before=ez_manage_module._ezm_per_sym_layer("1000PEPEUSDC","LONG") or {}
  report=configure_replay_ownership(ez_manage_module._ezm_base_config,"crypto",ownership_owner=ez_manage_module)
  inside_exact=ez_manage_module._vec_exact_mode_on()
  inside_sym=ez_manage_module._psym_get("1000PEPEUSDC","LONG","PARITY_VEC_EXACT_MODE",True)
  inside_layer=ez_manage_module._ezm_per_sym_layer("1000PEPEUSDC","LONG") or {}
  inside_flags={name:getattr(ez_manage_module.config,name,None) for name in ("VEC_DICTATE_ONLY_ENABLED","VEC_DRIVEN_ENABLED","PARITY_VEC_EXACT_MODE","VEC_BACKSTOP_SYNC_ENABLED","VEC_BACKSTOP_ENABLED")}
  risk_inside=ez_manage_module._psym_get("1000PEPEUSDC","LONG","MIN_GAIN_TO_BUY_AGGRESSIVELY",None)
  if inside_exact is not False or inside_sym is not False or any(inside_layer.get(name,False) for name in ("VEC_DICTATE_ONLY_ENABLED","VEC_DRIVEN_ENABLED","PARITY_VEC_EXACT_MODE","VEC_BACKSTOP_SYNC_ENABLED","VEC_BACKSTOP_ENABLED")) or any(inside_flags.values()) or risk_inside!=risk_before: raise RuntimeError("scoped ownership probe altered strategy gates or left vector ownership active")
  ownership_probe.update({"before_exact_mode":before_exact,"before_per_symbol_exact_mode":before_sym,"before_layer_flags":{k:layer_before.get(k) for k in ("VEC_DICTATE_ONLY_ENABLED","VEC_DRIVEN_ENABLED","PARITY_VEC_EXACT_MODE","VEC_BACKSTOP_SYNC_ENABLED","VEC_BACKSTOP_ENABLED")},"inside_exact_mode":inside_exact,"inside_per_symbol_exact_mode":inside_sym,"inside_layer_flags":{k:inside_layer.get(k) for k in ("VEC_DICTATE_ONLY_ENABLED","VEC_DRIVEN_ENABLED","PARITY_VEC_EXACT_MODE","VEC_BACKSTOP_SYNC_ENABLED","VEC_BACKSTOP_ENABLED")},"inside_config_flags":inside_flags,"risk_before":risk_before,"risk_inside":risk_inside,"report":report})
 finally:
  ez_manage_module._psym_ctx_clear(token)
asyncio.run(_ownership_probe())
if ez_manage_module._vec_exact_mode_on() is not ownership_probe["before_exact_mode"]: raise RuntimeError("native ownership probe failed to restore PARITY_VEC_EXACT_MODE")
board_ok,board_detail=vec_exact_module.latest_board_ok("1000PEPEUSDC","LONG")
if not board_ok: raise RuntimeError(f"published board refused replay: {board_detail}")
if not Path(mac_positions_module.__file__).resolve().is_relative_to(code): raise RuntimeError("MAC positions publisher imported outside runtime source")
mac_positions_module.ROOT=out/"native_position_books"
mac_positions_module.ROOT.mkdir(parents=True,exist_ok=True)
if not Path(mac_positions_module.ROOT).resolve().is_relative_to(root): raise RuntimeError("MAC positions publisher writes outside run root")
active=json.loads(active_path.read_text())
sym_side=str(active["sym_side"]).upper()
if f"{account}:{sym_side}".upper() not in {str(value).upper() for value in active.get("account_admission_keys",[])}: raise RuntimeError("account is not in frozen active-set admission")
if sha(code/"data"/"published_selection.json")!=sha(root/"runtime"/"code"/"data"/"published_selection.json"): raise RuntimeError("published board file changed during runtime setup")
pre={"record":"native_diagnostic_manifest","account":account,"sym_side":sym_side,"source_code_manifest_sha256":"4e6ffeee1a818cd0a1388ec2a0476a7258d0a41dd1f719ca5ae460dc850d6aaa","mac_positions_publisher_root":str(mac_positions_module.ROOT),"ownership_scope_probe":ownership_probe,"npz_sha256":sha(npz_path),"active_set_sha256":sha(active_path),"database_sha256":sha(os.environ["PER_SYM_STORE_DB"]),"published_selection_sha256":sha(code/"data"/"published_selection.json"),"market_mode_sha256":sha(config_module.Config.MARKET_MODE_FILE),"board_check":{"ok":board_ok,"detail":board_detail},"runtime_guard":runtime,"runtime_paths":prepaths,"logger_redirect":redirect_result,"real_execute":"run_one actual execute_now path with backtest-owned broker stub; no external broker client","ownership_mode":"V12_REPLAY_NATIVE_OWNERSHIP=1 scoped override","rate_guard_disabled":"V8_RATE_GUARD_DISABLED=1"}
out.mkdir(parents=True,exist_ok=True)
pre["preflight_completed_epoch"]=time.time()
(out/"manifest.json").write_text(json.dumps(pre,sort_keys=True,default=str)+"\n")
if os.environ.get("REPLAY_PREFLIGHT_ONLY")=="1":
 print(json.dumps({"status":"PREFLIGHT_ONLY_OK","account":account,"runtime_guard":runtime["status"],"board_ok":board_ok,"manifest_path":str(out/"manifest.json")})); raise SystemExit(0)
start=time.time(); pre["started_epoch"]=start
(out/"manifest.json").write_text(json.dumps(pre,sort_keys=True,default=str)+"\n")
try:
 overrides=dict(active["overrides"]); overrides["SIM_ACCOUNT"]=account
 result=engine_module.run_one(sym_side,overrides,window_days=30,offset_days=0)
 selected={"symside":result.get("symside"),"valid":result.get("valid"),"invalid_reason":result.get("invalid_reason"),"trace":result.get("trace"),"execution_ledger":result.get("execution_ledger"),"canonical_order_ledger":result.get("canonical_order_ledger"),"replay_coverage":result.get("replay_coverage"),"replay_ownership":result.get("replay_ownership"),"producer_coverage":result.get("producer_coverage"),"replay_settings_policy":result.get("replay_settings_policy"),"sim_account":result.get("sim_account"),"trade_count":len(result.get("trades_list") or result.get("trades_detail") or [])}
except BaseException as error:
 selected={"valid":False,"invalid_reason":f"runner {type(error).__name__}: {error}","trace":traceback.format_exc()}
finally:
 pre["ended_epoch"]=time.time(); pre["elapsed_seconds"]=pre["ended_epoch"]-start
 pre["runtime_db_sha256_after"]=sha(os.environ["PER_SYM_STORE_DB"])
 tmp=out/"result.json.tmp"; tmp.write_text(json.dumps(selected,sort_keys=True,default=str)+"\n"); tmp.replace(out/"result.json")
 pre["result_file_sha256"]=sha(out/"result.json")
 (out/"manifest.json").write_text(json.dumps(pre,sort_keys=True,default=str)+"\n")
print(json.dumps({"account":account,"valid":selected.get("valid"),"invalid_reason":selected.get("invalid_reason"),"execution_rows":len(selected.get("execution_ledger") or []),"canonical_orders":len(selected.get("canonical_order_ledger") or []),"elapsed_seconds":pre["elapsed_seconds"],"result_path":str(out/"result.json")},sort_keys=True,default=str))
