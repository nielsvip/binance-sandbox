#!/usr/bin/env python3
"""
Smart Muse router: Mac GPU -> S1 CPU -> Cloud fallback
Listens on :11436, forwards /v1/responses to:
 1) Mac Ollama (localhost:11434) - fast, GPU
 2) S1 Ollama (via SSH tunnel or direct) - slow, CPU, for bundling
 3) Meta Cloud (https://api.meta.ai/v1) - fallback when local fails or prompt is hard

Usage:
  python muse_smart_proxy.py --port 11436
  muse exec --base-url http://localhost:11436/v1 --model muse-glimmer:latest "prompt"
  # router auto-picks backend; add header X-Force-Backend: cloud/mac/s1 to override

Env:
  S1_HOST=s1-pub (or 157.180.125.52)  S1_PORT=11434  CLOUD_BASE=https://api.meta.ai/v1
"""
import json, http.server, urllib.request, urllib.error, sys, os, subprocess, time

MAC_OLLAMA = "http://localhost:11434"
S1_OLLAMA = os.environ.get("S1_OLLAMA", "http://127.0.0.1:11434")  # via ssh tunnel if needed
CLOUD_BASE = os.environ.get("CLOUD_BASE", "https://api.meta.ai/v1")
LISTEN = 11436

def memory_pressure_ok():
    """Returns True if Mac has >800MB free, False if Mac risks crash"""
    try:
        out = subprocess.check_output(["vm_stat"], text=True)
        free = int([l for l in out.splitlines() if "Pages free" in l][0].split()[2].strip("."))
        free_mb = free * 16384 / 1024 / 1024
        return free_mb > 800  # need 800MB free before hitting Mac
    except: return True

def should_go_cloud(data):
    """Heuristic: hard prompts go direct to cloud"""
    text = json.dumps(data)
    # signals of hard task: huge input, many files, reasoning request
    if len(text) > 80000: return True
    hard_keywords = ["complex", "prove", "trade", "backtest", "sweep", "optimize", "sharpe"]
    # not auto-routing on keywords now — just length
    return False

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.proxy(MAC_OLLAMA + self.path, to_mac=True)
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b''
        try: data = json.loads(body) if body else {}
        except: data = {}
        force = self.headers.get("X-Force-Backend", "").lower()

        # decide backends order
        backends = []
        if force == "cloud": backends = [("cloud", CLOUD_BASE)]
        elif force == "s1": backends = [("s1", S1_OLLAMA)]
        elif force == "mac": backends = [("mac", MAC_OLLAMA)]
        else:
            # User wants S1 first for memory/compute, cloud before Mac risks crash
            if should_go_cloud(data):
                backends = [("cloud", CLOUD_BASE), ("s1", S1_OLLAMA), ("mac", MAC_OLLAMA)]
            elif not memory_pressure_ok():
                print(f"[router] Mac memory low ({memory_pressure_ok.__doc__ or ''}), skipping Mac -> S1/cloud", file=sys.stderr)
                backends = [("s1", S1_OLLAMA), ("cloud", CLOUD_BASE)]
            else:
                # Normal: prefer S1 to keep Mac free, Mac as last local, cloud as final fallback
                backends = [("s1", S1_OLLAMA), ("mac", MAC_OLLAMA), ("cloud", CLOUD_BASE)]

        # Try each backend for /v1/responses (translate if needed)
        if self.path.startswith("/v1/responses"):
            for name, base in backends:
                try:
                    print(f"[router] trying {name} -> {base}/v1/responses", file=sys.stderr)
                    if name == "cloud":
                        # forward as-is to cloud (with auth if available)
                        req = urllib.request.Request(base.rstrip("/") + "/v1/responses", data=body, method="POST")
                        for k,v in self.headers.items():
                            if k.lower() not in ('host','content-length','connection'):
                                req.add_header(k, v)
                        # inject auth from keychain if META_API_KEY not set
                        # Muse's own auth is in ~/.config/muse/auth.json keychain — router will fail without it, then caller falls back
                        with urllib.request.urlopen(req, timeout=60) as resp:  # cloud: Muse keeps conversation alive client-side
                            self.send_response(resp.status)
                            for k,v in resp.headers.items():
                                if k.lower() not in ('connection','transfer-encoding'):
                                    self.send_header(k, v)
                            self.end_headers()
                            self.wfile.write(resp.read())
                            print(f"[router] {name} succeeded", file=sys.stderr)
                            return
                    else:
                        # local: translate via existing proxy logic (reuse muse_local_proxy.py handler)
                        # forward to local /v1/responses which our muse_local_proxy already handles
                        # Here we call that proxy directly (port 11435)
                        local_proxy = f"http://localhost:11435/v1/responses"
                        # If trying S1, use its proxy port
                        if name == "s1":
                            local_proxy = S1_OLLAMA.replace("11434","11435") + "/v1/responses"
                        req = urllib.request.Request(local_proxy, data=body, method="POST")
                        req.add_header("Content-Type","application/json")
                        with urllib.request.urlopen(req, timeout=180) as resp:
                            self.send_response(resp.status)
                            for k,v in resp.headers.items():
                                if k.lower() not in ('connection','transfer-encoding'):
                                    self.send_header(k, v)
                            self.end_headers()
                            self.wfile.write(resp.read())
                            print(f"[router] {name} succeeded via proxy", file=sys.stderr)
                            return
                except Exception as e:
                    print(f"[router] {name} failed: {e}", file=sys.stderr)
                    continue
            self.send_error(502, "all backends failed")
        else:
            # generic proxy: try Mac first
            self.proxy(MAC_OLLAMA + self.path, to_mac=True)

    def proxy(self, url, to_mac=False):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=self.command)
        for k,v in self.headers.items():
            if k.lower() not in ('host','content-length','connection'):
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self.send_response(resp.status)
                for k,v in resp.headers.items():
                    if k.lower() not in ('connection','transfer-encoding'):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except Exception as e:
            try: self.send_error(502, str(e))
            except: pass
    def log_message(self, fmt, *args):
        if "Broken pipe" in fmt % args: return
        sys.stderr.write(fmt % args + "\n")

if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=LISTEN)
    p.add_argument("--s1", default=S1_OLLAMA)
    p.add_argument("--cloud", default=CLOUD_BASE)
    a=p.parse_args()
    S1_OLLAMA=a.s1; CLOUD_BASE=a.cloud
    print(f"Smart router :{a.port}  Mac={MAC_OLLAMA}  S1={S1_OLLAMA}  Cloud={CLOUD_BASE}", file=sys.stderr)
    print(f"Use: muse exec --base-url http://localhost:{a.port}/v1 --model muse-glimmer:latest \"prompt\"", file=sys.stderr)
    http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
