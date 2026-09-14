#!/usr/bin/env python3
"""Muse -> Ollama bridge. Translates OpenAI Responses API to Chat Completions."""
import json, http.server, urllib.request, urllib.error, sys
OLLAMA = "http://localhost:11434"
LISTEN_PORT = 11435
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self.proxy_to_ollama("/v1/models")
        else:
            self.send_error(404)
    def do_POST(self):
        if self.path.startswith("/v1/responses"):
            self.handle_responses()
        else:
            self.proxy_to_ollama(self.path)
    def proxy_to_ollama(self, path):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(OLLAMA + path, data=body, method=self.command)
        for k,v in self.headers.items():
            if k.lower() not in ('host','content-length','connection'):
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(resp.status)
                for k,v in resp.headers.items():
                    if k.lower() not in ('connection','transfer-encoding','content-encoding'):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except Exception as e:
            try: self.send_error(502, str(e))
            except: pass
    def handle_responses(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except:
            self.send_error(400, "invalid json"); return
        model = data.get("model", "muse-glimmer:latest")
        inp = data.get("input", data.get("messages", []))
        messages = []
        if isinstance(inp, str):
            messages = [{"role":"user","content":inp}]
        elif isinstance(inp, list):
            for item in inp:
                if isinstance(item, dict):
                    role = item.get("role","user")
                    content = item.get("content","")
                    if isinstance(content, list):
                        content = "".join(c.get("text","") if isinstance(c,dict) else str(c) for c in content)
                    messages.append({"role":role,"content":content})
                elif isinstance(item, str):
                    messages.append({"role":"user","content":item})
        if not messages:
            instr = data.get("instructions","")
            if instr:
                messages = [{"role":"system","content":instr}]
                if isinstance(inp, str) and inp:
                    messages.append({"role":"user","content":inp})
            else:
                messages = [{"role":"user","content":"hello"}]
        stream = bool(data.get("stream", False))
        chat_payload = {"model": model, "messages": messages, "stream": stream}
        if data.get("tools"): chat_payload["tools"] = data["tools"]
        for k in ("temperature","top_p","max_tokens","max_output_tokens"):
            if k in data:
                chat_payload["max_tokens" if k=="max_output_tokens" else k] = data[k]
        body = json.dumps(chat_payload).encode()
        req = urllib.request.Request(OLLAMA + "/v1/chat/completions", data=body, method="POST")
        req.add_header("Content-Type","application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                if not stream:
                    chat_resp = json.loads(resp.read())
                    text = ""
                    if chat_resp.get("choices"):
                        text = chat_resp["choices"][0].get("message",{}).get("content","") or ""
                    out = {"id": chat_resp.get("id","resp_proxy"), "object":"response","model":model,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":text}]}],"status":"completed"}
                    out_b = json.dumps(out).encode()
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type","application/json")
                        self.send_header("Content-Length", str(len(out_b)))
                        self.end_headers()
                        self.wfile.write(out_b)
                    except BrokenPipeError: pass
                else:
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type","text/event-stream")
                        self.send_header("Cache-Control","no-cache")
                        self.send_header("Connection","keep-alive")
                        self.end_headers()
                    except BrokenPipeError: return
                    for line in resp:
                        line = line.decode() if isinstance(line, bytes) else line
                        if not line.strip(): continue
                        if line.startswith("data:"):
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                try:
                                    self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
                                except BrokenPipeError: break
                                break
                            try:
                                chunk = json.loads(payload)
                                delta = chunk.get("choices",[{}])[0].get("delta",{}).get("content","") or ""
                                if delta:
                                    out = json.dumps({"type":"response.output_text.delta","delta":delta})
                                    try:
                                        self.wfile.write(f"data: {out}\n\n".encode()); self.wfile.flush()
                                    except BrokenPipeError: break
                            except: pass
                    try:
                        done = json.dumps({"type":"response.completed","response":{"status":"completed"}})
                        self.wfile.write(f"data: {done}\n\n".encode())
                        self.wfile.write(b"data: [DONE]\n\n")
                    except BrokenPipeError: pass
        except Exception as e:
            try:
                body = str(e).encode()
                self.send_response(502); self.send_header("Content-Type","text/plain"); self.end_headers(); self.wfile.write(body)
            except: pass
    def log_message(self, fmt, *args):
        # suppress broken-pipe spam, keep useful
        if "Broken pipe" in fmt % args: return
        sys.stderr.write(fmt % args + "\n")
if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("--port",type=int,default=LISTEN_PORT); p.add_argument("--ollama",default=OLLAMA); a=p.parse_args()
    OLLAMA=a.ollama; LISTEN_PORT=a.port
    print(f"Muse->Ollama proxy listening on http://localhost:{LISTEN_PORT} -> {OLLAMA}", file=sys.stderr)
    http.server.ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
