import json
import subprocess
import sys
from pathlib import Path
import pytest

@pytest.mark.parametrize('operation', ['connect','connect_ex','sendto','getaddrinfo','gethostbyname'])
def test_guard_blocks_before_real_socket_or_dns_io(tmp_path,operation):
    code='''
import json,socket,sys,errno
from pathlib import Path
from tools.replay_network_guard import install,status
install(sys.argv[1],Path(sys.argv[1]).parent)
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM if sys.argv[2]=='sendto' else socket.SOCK_STREAM)
try:
 if sys.argv[2]=='sendto':s.sendto(b'test',('127.0.0.1',6379))
 elif sys.argv[2]=='getaddrinfo':socket.getaddrinfo('example.invalid',6379)
 elif sys.argv[2]=='gethostbyname':socket.gethostbyname('example.invalid')
 else:getattr(s,sys.argv[2])(('127.0.0.1',6379))
except OSError as error:
 assert error.errno==errno.ENETUNREACH
else:raise AssertionError('Network attempt was not blocked')
finally:s.close()
print(json.dumps(status()))
'''
    audit=tmp_path/'network.jsonl'
    result=subprocess.run([sys.executable,'-c',code,str(audit),operation],text=True,capture_output=True,check=True)
    state=json.loads(result.stdout);assert state['installed'] and state['dotenv_disabled'] and state['blocked_attempts']==1
    row=json.loads(audit.read_text());assert row['operation']==operation

def test_install_is_idempotent_and_requires_same_owned_audit(tmp_path):
    code='''
import sys
from pathlib import Path
from tools.replay_network_guard import install,require_installed
try:require_installed()
except RuntimeError:pass
else:raise AssertionError('Missing guard was accepted')
install(sys.argv[1],Path(sys.argv[1]).parent);install(sys.argv[1],Path(sys.argv[1]).parent);assert require_installed()['installed']
try:install(sys.argv[2],Path(sys.argv[1]).parent)
except RuntimeError:pass
else:raise AssertionError('Audit destination changed')
'''
    subprocess.run([sys.executable,'-c',code,str(tmp_path/'a.jsonl'),str(tmp_path/'b.jsonl')],check=True)

def test_modified_guard_hooks_fail_preflight(tmp_path):
    code="""
import socket,sys
from tools.replay_network_guard import install,require_installed
install(sys.argv[1],sys.argv[2])
socket.socket.connect=lambda *args:None
try:require_installed()
except RuntimeError:pass
else:raise AssertionError('Modified guard was accepted')
"""
    subprocess.run([sys.executable,'-c',code,str(tmp_path/'a.jsonl'),str(tmp_path)],check=True)

def test_audit_path_cannot_escape_run_root(tmp_path):
    code="""
import sys
from tools.replay_network_guard import install
try:install(sys.argv[1],sys.argv[2])
except RuntimeError:pass
else:raise AssertionError('Escaping audit accepted')
"""
    run=tmp_path/'run';run.mkdir()
    subprocess.run([sys.executable,'-c',code,str(tmp_path/'a.jsonl'),str(run)],check=True)

def test_install_after_project_import_is_rejected(tmp_path):
    code="""
import sys,types
from tools.replay_network_guard import install
sys.modules['ez_manage']=types.ModuleType('ez_manage')
try:install(sys.argv[1],sys.argv[2])
except RuntimeError as error:assert 'precede project imports' in str(error)
else:raise AssertionError('Late installation accepted')
"""
    subprocess.run([sys.executable,'-c',code,str(tmp_path/'a.jsonl'),str(tmp_path)],check=True)
