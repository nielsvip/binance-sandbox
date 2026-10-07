import redis
import sys

# Configure your Redis host here if it's not localhost
r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

def scan_and_delete(pattern):
    print(f"Scanning for pattern: {pattern}")
    keys = []
    cursor = '0'
    while True:
        cursor, results = r.scan(cursor, match=pattern, count=100)
        keys.extend(results)
        if cursor == '0':
            break
            
    if not keys:
        print(f"  No keys found for {pattern}")
        return

    print(f"  Found {len(keys)} keys. Deleting...")
    for key in keys:
        r.delete(key)
        print(f"    Deleted: {key}")

def nuke_all():
    print("WARNING: About to FLUSHALL (Delete entire Redis Database).")
    confirm = input("Type 'NUKE' to confirm: ")
    if confirm == 'NUKE':
        r.flushall()
        print("💥 Redis has been completely emptied.")
    else:
        print("Aborted.")

if __name__ == "__main__":
    try:
        r.ping()
        print("Connected to Redis successfully.")
    except Exception as e:
        print(f"Could not connect to Redis: {e}")
        sys.exit(1)

    print("--- SURGICAL REMOVAL ---")
    # 1. Kill the specific virus keys
    scan_and_delete("direct_high_gain_augmented:*")
    
    # 2. Kill other potential state zombies
    scan_and_delete("augmented_positions:*")
    scan_and_delete("reduced_positions:*")
    
    # 3. Kill any 'execute_now' locks that might be stuck
    scan_and_delete("execute_now:*")

    print("\n----------------------")
    print("If you want to wipe EVERYTHING (Market data, cached prices, etc), run this script with argument 'full'")
    
    if len(sys.argv) > 1 and sys.argv[1] == 'full':
        nuke_all()
        
    print("\n✅ Cleanup Complete.")