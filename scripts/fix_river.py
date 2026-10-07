import os

TARGET_FILE = "ez_positions_service.py"

def apply_patch():
    if not os.path.exists(TARGET_FILE):
        print(f"❌ Error: {TARGET_FILE} not found!")
        return

    with open(TARGET_FILE, "r") as f:
        lines = f.readlines()

    new_lines = []
    patched = False
    inside_function = False

    for line in lines:
        new_lines.append(line)
        
        # Detect the start of the problematic function
        if "async def restore_position_from_backups(" in line:
            inside_function = True
            print("✅ Found function: restore_position_from_backups")
            continue

        # Inject the fix right after the function definition (and docstring if present)
        if inside_function and "try:" in line and not patched:
            # We insert the block before the 'try:'
            indent = line.split("try:")[0] # Capture indentation
            
            injection = [
                f"{indent}# --- HOTFIX: Stop RIVERUSDT Restore Loop ---\n",
                f"{indent}if symbol and 'RIVER' in symbol.upper():\n",
                f"{indent}    return None\n",
                f"{indent}# -----------------------------------------\n"
            ]
            
            # Insert our injection BEFORE the current 'try:' line
            new_lines.pop() # Remove the 'try:' line we just added
            new_lines.extend(injection)
            new_lines.append(line) # Add the 'try:' line back
            
            patched = True
            print("✅ Injected RIVERUSDT block logic.")

    if patched:
        with open(TARGET_FILE, "w") as f:
            f.writelines(new_lines)
        print(f"🚀 Success! {TARGET_FILE} has been patched.")
        print("🔄 Please restart your bot now.")
    else:
        print("⚠️ Warning: Could not find the injection point. Logic might already be present or file structure changed.")

if __name__ == "__main__":
    apply_patch()