
"""
TradeLog Pro Server V10
Filename: server.py
Description: Flask API for serving logs and JSONL history to the TradeLog Pro Analyzer.
"""

import os
import sys
import json
import pickle
import glob
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Optional Redis
try:
    import redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

# ==========================================
# CONFIGURATION
# ==========================================

CURRENT_DIR = os.getcwd()
PARENT_DIR = os.path.dirname(CURRENT_DIR)

if os.path.exists(os.path.join(PARENT_DIR, 'ang')):
    PROJECT_ROOT = PARENT_DIR
elif os.path.exists(os.path.join(CURRENT_DIR, 'ang')):
    PROJECT_ROOT = CURRENT_DIR
else:
    PROJECT_ROOT = CURRENT_DIR

# Custom Data Dir
DATA_DIR = os.path.join(PROJECT_ROOT, "DATA_DIR")
HISTORY_DIR = os.path.join(DATA_DIR, "history")

DIST_DIR = os.path.join(CURRENT_DIR, 'dist')
ASSETS_DIR = os.path.join(DIST_DIR, 'assets')

CRYPTO_BOTS = ['ang', 'inf', 'flz', 'men', 'fin']
STOCK_BOTS = ['trb', 'trc']
ALL_BOTS = CRYPTO_BOTS + STOCK_BOTS

REDIS_CONFIG = [
    {"name": "Remote Server", "host": "157.180.125.52", "port": 6381},
    {"name": "Localhost",     "host": "localhost",      "port": 6379}
]

app = Flask(__name__, static_folder='dist', static_url_path='/')
CORS(app)

# ==========================================
# HELPERS
# ==========================================

def load_json_safe(filepath):
    if os.path.isfile(filepath):
        try:
            with open(filepath, 'r') as f:
                content = f.read().strip()
                if not content: return []
                data = json.loads(content)
                if isinstance(data, dict): return list(data.values())
                return data
        except Exception: pass
    return []

def get_redis_client():
    if not HAS_REDIS: return None
    for conf in REDIS_CONFIG:
        try:
            r = redis.Redis(host=conf['host'], port=conf['port'], decode_responses=False, socket_timeout=1)
            if r.ping(): return r
        except Exception: pass
    return None

# ==========================================
# API ROUTES
# ==========================================

@app.route('/api/history')
def get_history():
    """Reads all .jsonl files in DATA_DIR/history subfolders."""
    history = []
    if not os.path.exists(HISTORY_DIR):
        return jsonify([])

    # Scan each bot folder in history
    for bot in ALL_BOTS:
        bot_hist_path = os.path.join(HISTORY_DIR, bot)
        if not os.path.exists(bot_hist_path): continue

        # Find all .jsonl files (e.g. BTCUSDT_LONG.jsonl)
        jsonl_files = glob.glob(os.path.join(bot_hist_path, "*.jsonl"))
        for fpath in jsonl_files:
            fname = os.path.basename(fpath)
            symbol = fname.split('_')[0].replace('.jsonl', '')
            
            try:
                with open(fpath, 'r') as f:
                    for line in f:
                        if not line.strip(): continue
                        try:
                            data = json.loads(line)
                            # Unified mapping
                            history.append({
                                "ts": data.get("ts") or data.get("timestamp"),
                                "type": data.get("type"),
                                "symbol": symbol,
                                "bot": bot,
                                "qty": data.get("qty") or data.get("amount"),
                                "price": data.get("price"),
                                "value": data.get("value") or (data.get("amount", 0) * data.get("price", 0)),
                                "reason": data.get("reason") or data.get("reason_text", "Unknown"),
                                "score": data.get("score") or data.get("tech_score"),
                                "snapshot": data.get("snapshot") or data.get("indicators", {})
                            })
                        except Exception: continue
            except Exception: continue
    
    return jsonify(history)

@app.route('/api/rankings')
def get_rankings():
    paths = [
        os.path.join(PROJECT_ROOT, "ranking_points.json"),
        os.path.join(DATA_DIR, "ranking_points.json")
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, 'r') as f: return jsonify(json.load(f))
    return jsonify({})

@app.route('/api/positions')
def get_positions():
    data = {}
    r = get_redis_client()
    for bot in ALL_BOTS:
        data[bot] = {'long': [], 'short': []}
        bot_dir = os.path.join(PROJECT_ROOT, bot)
        lp, sp = os.path.join(bot_dir, "long_positions.json"), os.path.join(bot_dir, "short_positions.json")
        if os.path.exists(lp): data[bot]['long'] = load_json_safe(lp)
        if os.path.exists(sp): data[bot]['short'] = load_json_safe(sp)

    if r:
        for bot in CRYPTO_BOTS:
            raw = r.get(f"positions:{bot}")
            if raw:
                payload = json.loads(raw.decode('utf-8'))
                if "positions" in payload: data[bot]['long'] = list(payload["positions"].values())
        for bot in STOCK_BOTS:
            raw = r.get(f"tradier:positions:{bot}")
            if raw:
                wrapper = pickle.loads(raw)
                if "positions" in wrapper: data[bot]['long'] = list(wrapper["positions"].values())
    return jsonify(data)

@app.route('/api/logs')
def get_logs():
    res = {'stock': '', 'crypto': ''}
    log_dirs = [os.path.join(PROJECT_ROOT, "logs"), "/logs", "/binance/data/decisions", "/home/niels/logs"]
    
    # Standard log files
    log_files = ["actions.log", "tradier.log", "crypto_actions.log", "tradier_actions.log"]
    
    for d in log_dirs:
        if not os.path.exists(d): continue
        
        # Root logs in this dir
        for f_name in log_files:
            p = os.path.join(d, f_name)
            if os.path.isfile(p):
                with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                    cat = 'stock' if 'tradier' in p else 'crypto'
                    res[cat] += f.read() + "\n"
        
        # JSONL decisions
        jsonl_files = glob.glob(os.path.join(d, "*.jsonl"))
        for fpath in jsonl_files:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                res['crypto'] += f.read() + "\n"

        # Bot subfolder logs
        for bot in ALL_BOTS:
            bot_dir = os.path.join(d, bot)
            if os.path.exists(bot_dir):
                for f_name in log_files:
                    p = os.path.join(bot_dir, f_name)
                    if os.path.isfile(p):
                        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                            cat = 'stock' if 'tradier' in p else 'crypto'
                            res[cat] += f.read() + "\n"
    
    return jsonify(res)

@app.route('/')
def serve_index():
    if os.path.exists(os.path.join(app.static_folder, 'index.html')):
        return send_from_directory(app.static_folder, 'index.html')
    return "<h1>TradeLog Pro Active</h1>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
