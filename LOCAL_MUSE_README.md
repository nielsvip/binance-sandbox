# Muse — Local Mode (Free, No Paid API)

Both **MacBook** and **S1** are now configured to run Muse locally via Ollama,
so you can avoid the paid Meta cloud API for most tasks.

## What was installed

| Host | Muse | Ollama | Model | Proxy |
|------|------|--------|-------|-------|
| MacBook (M1, macOS 26.6) | 1.2.1 (`~/.local/bin/muse`) | 0.33.3 (`/Applications/Ollama.app`) | `muse-glimmer:latest` 27.9B Q4_K_M (18 GB, GPU) | `~/binance/muse_local_proxy.py` → `http://localhost:11435` |
| S1 (aarch64, Ubuntu 6.8, 30 GB) | 1.2.1 (`~/.local/bin/muse`) | 0.34.0 (`/usr/local/bin/ollama`) | `muse-glimmer:latest` pulling (18 GB, CPU-only) | `~/binance/muse_local_proxy.py` → `http://localhost:11435` |

### Why a proxy?

Muse speaks **OpenAI Responses API** (`POST /v1/responses`, streaming SSE).
Ollama speaks **Chat Completions** (`POST /v1/chat/completions`). The proxy
translates between them. Without it `muse --base-url http://localhost:11434/v1` fails
with `transport error for url (/v1/responses)`.

Proxy is pure Python stdlib, no deps, runs in project `.venv`:
`~/binance/.venv/bin/python ~/binance/muse_local_proxy.py --port 11435`

## Quick start

### MacBook

1. Ensure Ollama is running (auto-starts on login if LaunchAgent installed):
   ```
   open -a Ollama
   ollama list          # should show muse-glimmer:latest
   ollama ps            # warm: shows model loaded on GPU
   ```

2. Start proxy (one Terminal tab, keep running):
   ```
   cd ~/binance && .venv/bin/python muse_local_proxy.py --port 11435
   # logs to /tmp/muse_proxy.log
   ```

3. Run Muse locally (another Terminal):
   ```
   # single prompt (headless)
   muse exec --base-url http://localhost:11435/v1 --model muse-glimmer:latest "explain this repo"

   # interactive TUI locally
   muse --base-url http://localhost:11435/v1 --model muse-glimmer:latest
   ```

4. **Cloud (paid) still available** — just omit `--base-url`:
   ```
   muse                  # cloud
   muse exec "prompt"   # cloud
   ```

### S1

```bash
ssh s1-pub
cd ~/binance && .venv/bin/python muse_local_proxy.py --port 11435 &
# then same muse exec command as above, or enable systemd:
systemctl --user enable --now muse-local-proxy
systemctl --user status muse-local-proxy
journalctl --user -u muse-local-proxy -f
```

Ollama on S1 runs as `ollama.service` (CPU-only, no GPU). Model pull:
```bash
ollama pull muse-glimmer:latest   # 18 GB, ~5-10 min on S1, started 2026-09-14 02:08
tail -f /tmp/ollama_pull.log
ollama list
```

## Persistent startup

### MacBook — LaunchAgent
```bash
cp ~/binance/com.muse.local-proxy.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.muse.local-proxy.plist
launchctl list | grep muse
cat /tmp/muse_proxy.log
```

### S1 — systemd user service
```bash
mkdir -p ~/.config/systemd/user
cp ~/binance/muse-local-proxy.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now muse-local-proxy
systemctl --user status muse-local-proxy
```

## Performance notes

* **MacBook** (M-series, Metal): 27.9B Q4_K_M runs fast (~GPU, 16 GB VRAM reported). Preload to keep warm:
  `curl -s http://localhost:11434/api/generate -d '{"model":"muse-glimmer:latest","prompt":"hi","stream":false}'`
  After warm, first token ~1-2 s.

* **S1** (Neoverse-N1, 16 cores, 30 GB, **no GPU**): Same model will be **very slow** (~CPU, ~20-50 tok/s estimated, high RAM). `free -h` shows 23 GB available, model needs 18 GB + overhead — fits but tight. For faster local work on S1 consider a smaller model:
  ```bash
  ollama pull qwen2.5-coder:7b   # ~4.7 GB, much faster on ARM CPU
  muse exec --base-url http://localhost:11435/v1 --model qwen2.5-coder:7b "prompt"
  ```

* Proxy streaming: current proxy handles both `stream=false` (tested OK, returns `LOCAL_OK`) and `stream=true` (used by `muse exec`). If you see `retrying meta model stream` → ensure model is preloaded and proxy is on 11435.

## Verification

```bash
# Ollama direct
curl -s http://localhost:11434/v1/models | jq .
curl -s http://localhost:11435/v1/models | jq .   # via proxy

# Proxy translation (non-stream)
curl -s http://localhost:11435/v1/responses -H "Content-Type: application/json" \
  -d '{"model":"muse-glimmer:latest","input":"Say LOCAL_OK","stream":false}' | jq .

# Muse local (after warm)
muse exec --base-url http://localhost:11435/v1 --model muse-glimmer:latest "Say LOCAL_OK and nothing else"
```

## Files added to repo

* `~/binance/muse_local_proxy.py` — Responses→Chat bridge (project-local, runs in `.venv`)
* `~/binance/.venv/` — project venv (uv, Python 3.11/3.12)
* `~/binance/com.muse.local-proxy.plist` — MacBook LaunchAgent
* `~/binance/muse-local-proxy.service` — S1 systemd user unit
* `~/binance/setup_local_muse.sh` — helper script printing usage

## Limitations & caveats

* Muse session logs still mention `provider: meta` — traffic is local but auth isn't bypassed; no API key needed for `--base-url` local path.
* Tools/function-calling is forwarded but Ollama's tool support for `muse-glimmer` is experimental; code-apply edits work, but complex multi-turn tool loops may differ vs cloud.
* S1 pull requires Ollama ≥0.32.8 — we upgraded S1 from 0.17.7 → 0.34.0 to satisfy `muse-glimmer`.
* Proxy exits when shell session ends (macOS has no `setsid`). Use LaunchAgent/systemd for persistence; otherwise keep a Terminal tab open.
