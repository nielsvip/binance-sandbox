import os
import json
import glob
from functools import wraps
from flask import Flask, jsonify, request, Response
from flask_cors import CORS

app = Flask(__name__)
# Enable CORS for all domains on all routes
CORS(app, resources={r"/*": {"origins": "*"}})

# ==========================================
# CONFIGURATION
# ==========================================

# 1. AUTHENTICATION
# Set these on your server as environment variables:
# export BRIDGE_USERNAME=niels
# export BRIDGE_PASSWORD=your_secure_password
BRIDGE_USERNAME = os.getenv("BRIDGE_USERNAME", "niels")
BRIDGE_PASSWORD = os.getenv("BRIDGE_PASSWORD") # Will fail if not set

def check_auth(username, password):
    """Check if a username/password combination is valid."""
    if not BRIDGE_PASSWORD:
        # If no password is set in environment, allow access but log warning
        # This is for initial setup safety
        return True
    return username == BRIDGE_USERNAME and password == BRIDGE_PASSWORD

def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# 2. SPECIFIC PATH REQUESTED BY USER
# We try this specific path first.
USER_PATH = "/home/niels/Documents/binance"

# 2. BOT DEFINITIONS
CRYPTO_BOTS = ['ang', 'inf', 'flz', 'men', 'fin']
STOCK_BOTS = ['trb', 'trc']
ALL_BOTS = CRYPTO_BOTS + STOCK_BOTS

# Determine working directory
if os.path.exists(USER_PATH):
    BASE_PATH = USER_PATH
    print(f"✅ Using User Path: {BASE_PATH}")
else:
    BASE_PATH = os.getcwd()
    print(f"⚠️ User path not found. Using current folder: {BASE_PATH}")

# ==========================================

def safe_read(filepath):
    """Reads a file if it exists, returns empty string if not."""
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read() + "\n"
        except Exception as e:
            print(f"Error reading {filepath}: {e}")
            return ""
    return ""

def load_json_safe(filepath):
    """Loads a JSON file if it exists, returns empty list if not."""
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                content = f.read().strip()
                if not content: return []
                data = json.loads(content)
                # Handle case where JSON is a dict of positions vs array
                if isinstance(data, dict): return list(data.values())
                return data
        except Exception as e:
            print(f"Error parsing JSON {filepath}: {e}")
            return []
    return []

@app.route('/')
@requires_auth
def index():
    """Health check route."""
    found_bots = [b for b in ALL_BOTS if os.path.isdir(os.path.join(BASE_PATH, b))]
    return jsonify({
        "status": "TradeLog Bridge Online",
        "working_directory": BASE_PATH,
        "bots_detected": found_bots,
        "message": "Bridge is running. Go to TradeLog Pro in your browser."
    })

@app.route('/api/positions')
@requires_auth
def get_positions():
    """Reads long_positions.json and short_positions.json for each bot."""
    data = {}
    
    for bot in ALL_BOTS:
        bot_dir = os.path.join(BASE_PATH, bot)
        data[bot] = {'long': [], 'short': []}
        
        if os.path.isdir(bot_dir):
            # Check for standard names
            long_path = os.path.join(bot_dir, "long_positions.json")
            short_path = os.path.join(bot_dir, "short_positions.json")
            
            # Fallback for some setups that might rename them
            if not os.path.exists(long_path): long_path = os.path.join(bot_dir, "positions.json")
            
            data[bot]['long'] = load_json_safe(long_path)
            data[bot]['short'] = load_json_safe(short_path)
            
    return jsonify(data)

@app.route('/api/logs')
@requires_auth
def get_logs():
    """Aggregates log files from root and subdirectories."""
    data = {'stock': '', 'crypto': ''}
    
    # --- 1. CRYPTO LOGS ---
    crypto_content = ""
    # Master logs in root
    crypto_content += safe_read(os.path.join(BASE_PATH, "actions.log"))
    crypto_content += safe_read(os.path.join(BASE_PATH, "crypto_actions.log"))
    
    # Subfolder logs
    for bot in CRYPTO_BOTS:
        crypto_content += safe_read(os.path.join(BASE_PATH, bot, "actions.log"))
        
    data['crypto'] = crypto_content
    
    # --- 2. STOCK LOGS ---
    stock_content = ""
    # Master logs in root
    stock_content += safe_read(os.path.join(BASE_PATH, "tradier.log"))
    
    # Subfolder logs
    for bot in STOCK_BOTS:
        stock_content += safe_read(os.path.join(BASE_PATH, bot, "tradier.log"))
        stock_content += safe_read(os.path.join(BASE_PATH, bot, "actions.log"))
        
    data['stock'] = stock_content
    
    return jsonify(data)

@app.route('/api/history')
@requires_auth
def list_history():
    """Lists all available history files in the data/history directory."""
    history_base = os.path.join(BASE_PATH, "data", "history")
    if not os.path.exists(history_base):
        return jsonify({"error": "History directory not found", "path": history_base}), 404
    
    structure = {}
    for acc in os.listdir(history_base):
        acc_path = os.path.join(history_base, acc)
        if os.path.isdir(acc_path):
            files = [f for f in os.listdir(acc_path) if f.endswith(('.jsonl', '.json'))]
            if files:
                structure[acc] = sorted(files)
                
    return jsonify({
        "status": "success",
        "base_path": history_base,
        "accounts": structure
    })

@app.route('/api/history/<account>/<filename>')
@requires_auth
def get_history_file(account, filename):
    """Serves a specific history file."""
    # Security check: prevent directory traversal
    if '..' in account or '..' in filename or filename.startswith('/'):
        return jsonify({"error": "Invalid path"}), 400
        
    file_path = os.path.join(BASE_PATH, "data", "history", account, filename)
    
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # If it's jsonl, we might want to return it as a list of objects or raw text
            if filename.endswith('.jsonl'):
                lines = f.readlines()
                data = [json.loads(line) for line in lines if line.strip()]
                return jsonify(data)
            else:
                return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

if __name__ == '__main__':
    print("-" * 50)
    print(f"   TRADELOG BRIDGE V5.0")
    print("-" * 50)
    print(f"1. Serving from: {BASE_PATH}")
    
    # Check explicitly for user requested path
    if BASE_PATH == USER_PATH and not os.path.isdir(BASE_PATH):
        print(f"❌ ERROR: The path {USER_PATH} does not exist!")
    
    # Pre-check folders
    found = [b for b in ALL_BOTS if os.path.isdir(os.path.join(BASE_PATH, b))]
    if found:
        print(f"2. FOUND folders: {', '.join(found)}")
    else:
        print(f"2. WARNING: No bot folders (ang, flz, etc) found in {BASE_PATH}")
        
    print("-" * 50)
    print("3. LISTENING ON ALL INTERFACES (0.0.0.0:5000)")
    # IMPORTANT: host='0.0.0.0' allows access from local network and sometimes fixes localhost issues
    app.run(host='0.0.0.0', port=5000)