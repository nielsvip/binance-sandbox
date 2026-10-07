import os
import shutil
import subprocess
import sys

def install_python_deps():
    print("📦 Installing Python dependencies...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "flask-cors", "redis"])
    except:
        print("⚠️  Could not install Python packages automatically. Try: pip install flask flask-cors redis")

def find_project_dir(start_path):
    print(f"🔍 Searching for project files in: {start_path}")
    candidates = []
    
    # Walk to find all folders containing package.json and vite.config.ts
    for root, dirs, files in os.walk(start_path):
        if 'node_modules' in dirs: dirs.remove('node_modules')
        if 'dist' in dirs: dirs.remove('dist')
        if '.git' in dirs: dirs.remove('.git')
        
        if "package.json" in files and "vite.config.ts" in files:
            candidates.append(root)

    if not candidates:
        return None
    
    # If multiple found (e.g. tradelog-pro_1, tradelog-pro_2), pick the most recent one
    # or the one closest to root
    candidates.sort(key=lambda x: (len(x.split(os.sep)), -os.path.getmtime(x)))
    return candidates[0]

def move_files_to_root(src_dir, dest_dir):
    if src_dir == dest_dir:
        return

    print(f"🚀 Moving files from: {src_dir}")
    print(f"                to: {dest_dir}")
    
    items = os.listdir(src_dir)
    for item in items:
        # Avoid overwriting the setup script or server script if they exist in root
        if item in ["setup.py", "server.py", "bridge.py"]:
            continue
            
        src_path = os.path.join(src_dir, item)
        dest_path = os.path.join(dest_dir, item)
        
        try:
            # Clean destination if exists
            if os.path.exists(dest_path):
                if os.path.isdir(dest_path):
                    shutil.rmtree(dest_path)
                else:
                    os.unlink(dest_path)
            
            shutil.move(src_path, dest_path)
            print(f"   - Moved {item}")
        except Exception as e:
            print(f"   ⚠️  Error moving {item}: {e}")

    # Cleanup empty source dir
    try:
        os.rmdir(src_dir)
        print("   - Cleaned up source folder")
    except:
        pass

def main():
    print("="*50)
    print("   TRADELOG PRO - AUTO SETUP")
    print("="*50)
    
    current_dir = os.getcwd()
    print(f"📂 Working Directory: {current_dir}")

    # 1. Python Deps
    install_python_deps()

    # 2. Find Project Files (Handle _1, _2 folders)
    project_dir = find_project_dir(current_dir)
    
    if not project_dir:
        print("\n❌ Could not find project files (package.json).")
        print("   Please ensure you unzipped the project into this folder.")
        return

    if project_dir != current_dir:
        move_files_to_root(project_dir, current_dir)
    else:
        print("✅ Files are already in the root.")

    # 3. NPM Install
    print("\n📦 Installing Node modules...")
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    try:
        subprocess.check_call([npm_cmd, "install"], cwd=current_dir)
    except Exception as e:
        print(f"❌ npm install failed: {e}")
        print("   Do you have Node.js installed?")
        return

    # 4. NPM Build
    print("\n🔨 Building UI...")
    try:
        subprocess.check_call([npm_cmd, "run", "build"], cwd=current_dir)
    except Exception as e:
        print(f"❌ Build failed: {e}")
        return

    # 5. Start Server
    print("\n✅ Setup Complete! Starting Analyzer...")
    print("-" * 50)
    
    # Try to run server.py (or bridge.py if user renamed it)
    script_to_run = "server.py"
    if not os.path.exists(script_to_run) and os.path.exists("bridge.py"):
        script_to_run = "bridge.py"

    if os.path.exists(script_to_run):
        try:
            subprocess.call([sys.executable, script_to_run])
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        print(f"❌ Could not find {script_to_run}. Please ensure it is in this folder.")

if __name__ == "__main__":
    main()
