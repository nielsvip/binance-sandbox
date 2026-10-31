"""Deploy isolated paper runtime/specs without replacing trading or backtest code."""
import argparse
import hashlib
import json
import shlex
import subprocess
import tempfile
from pathlib import Path

HOSTS = {"s1": "s1-int", "s2": "s2", "s5": "s5", "s6": "s6"}

def run(arguments):
    return subprocess.run(arguments, check=True, text=True, capture_output=True).stdout

def unit(command, description, memory="2G"):
    return f"[Unit]\nDescription={description}\nAfter=network.target\n\n[Service]\nType=simple\nExecStart={command}\nWorkingDirectory=/home/niels/binance-sandbox\nEnvironment=OPENBLAS_NUM_THREADS=1\nEnvironment=OMP_NUM_THREADS=1\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nNice=19\nCPUSchedulingPolicy=idle\nCPUWeight=1\nCPUQuota=25%\nIOWeight=1\nMemoryHigh=1G\nMemoryMax={memory}\nOOMScoreAdjust=500\nRestart=on-failure\nRestartSec=60\nTimeoutStopSec=45\n\n[Install]\nWantedBy=default.target\n"

def deploy(batch, runtime, remote_root, host, activate=False):
    batch, runtime, remote_root = Path(batch).resolve(), Path(runtime).resolve(), Path(remote_root)
    alias = HOSTS[host]
    remote_runtime = remote_root / "data/paper_forward_lab/runtime" / runtime.name
    remote_batch = remote_root / "data/paper_forward_lab/independent" / batch.name
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", alias]
    run(ssh + ["mkdir -p " + " ".join(shlex.quote(str(path)) for path in (remote_runtime, remote_batch / host, remote_root / "data/paper_forward_lab"))])
    for local, remote in ((runtime, remote_runtime), (batch / host, remote_batch / host)):
        run(["rsync", "-az", "--timeout=120", "--bwlimit=8192", str(local) + "/", f"{alias}:{remote}/"])
    verifier = "import hashlib,json,pathlib; p=pathlib.Path(" + repr(str(remote_runtime)) + "); m=json.loads((p/'runtime_manifest.json').read_text()); bad=[k for k,v in m['file_sha256'].items() if hashlib.sha256((p/k).read_bytes()).hexdigest()!=v]; assert not bad,bad; print(len(m['file_sha256']))"
    verified = run(ssh + ["python3 -c " + shlex.quote(verifier)]).strip()
    batch_verifier = "import hashlib,json,pathlib; p=pathlib.Path(" + repr(str(remote_batch / host)) + "); m=json.loads((p/'bundle_manifest.json').read_text()); bad=[k for k,v in m['file_sha256'].items() if hashlib.sha256((p/k).read_bytes()).hexdigest()!=v]; assert not bad,bad; print(len(m['file_sha256']))"
    verified_specs = run(ssh + ["python3 -c " + shlex.quote(batch_verifier)]).strip()
    # Main worker entrypoint is kept in the runtime. Its explicit workspace root
    # is supplied by the coordinator rather than by mutating any production file.
    interpreter = run(ssh + ["if test -x /home/niels/.conda/envs/binance_env_mac/bin/python; then echo /home/niels/.conda/envs/binance_env_mac/bin/python; elif test -x /home/niels/binance-sandbox/.venv/bin/python; then echo /home/niels/binance-sandbox/.venv/bin/python; else echo /usr/bin/python3; fi"]).strip()
    run(ssh + [shlex.quote(interpreter) + " -c " + shlex.quote("import numpy,pandas")])
    coordinator = remote_runtime / "tools/paper_forward_fleet.py"
    command = f"{interpreter} {coordinator} --host {host} --root {remote_root} --specs-dir {remote_batch / host / 'specs'} --state-db {remote_root / 'data/paper_forward_lab' / ('fleet_' + host + '.sqlite3')} --max-workers 1 --poll-seconds 60"
    archive_command = f"{interpreter} {remote_runtime / 'tools/paper_native_journal.py'} --host {host} --base {remote_root / 'data/paper_forward_lab'} --timeframes 1m --poll-seconds 60"
    with tempfile.TemporaryDirectory(prefix="paper-unit-") as directory:
        path = Path(directory) / "paper-independent-forward.service"
        path.write_text(unit(command, "Independent paper scenarios; idle CPU and admission gated"))
        run(ssh + ["mkdir -p ~/.config/systemd/user; if test -f ~/.config/systemd/user/paper-independent-forward.service; then cp ~/.config/systemd/user/paper-independent-forward.service ~/.config/systemd/user/paper-independent-forward.service.before.$(date -u +%Y%m%d%H%M%S); fi"])
        run(["rsync", "-az", str(path), f"{alias}:.config/systemd/user/"])
        archive_path = Path(directory) / "paper-native-journal.service"
        archive_path.write_text(unit(archive_command, "Bounded native observations for independent paper scenarios", "512M").replace("MemoryHigh=1G", "MemoryHigh=256M"))
        run(ssh + ["if test -f ~/.config/systemd/user/paper-native-journal.service; then cp ~/.config/systemd/user/paper-native-journal.service ~/.config/systemd/user/paper-native-journal.service.before.$(date -u +%Y%m%d%H%M%S); fi"])
        run(["rsync", "-az", str(archive_path), f"{alias}:.config/systemd/user/"])
    run(ssh + ["systemctl --user daemon-reload"])
    if activate:
        run(ssh + ["systemctl --user enable --now paper-native-journal.service paper-independent-forward.service"])
    return {"host": host, "runtime": str(remote_runtime), "batch": str(remote_batch), "verified_files": verified, "verified_spec_files": verified_specs, "activated": activate}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--remote-root", default="/home/niels/binance-sandbox")
    parser.add_argument("--host", choices=HOSTS, required=True)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    print(json.dumps(deploy(args.batch, args.runtime, args.remote_root, args.host, args.activate)))

if __name__ == "__main__":
    main()
