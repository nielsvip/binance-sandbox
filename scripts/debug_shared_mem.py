import sys
from ez_share_ind import get_shared_memory_client
import json

client = get_shared_memory_client()
if not client:
    print("Could not connect to shared memory")
    sys.exit(1)

store = client.get_store()
stats = store.get_stats()
print(f"Stats: {stats}")

symbols = ['BTCUSDC', 'ORDIUSDC', 'ETHUSDC']
for s in symbols:
    data = store.get_symbol(s)
    k = data.get('stoch_k_1m', 'N/A')
    d = data.get('stoch_d_1m', 'N/A')
    ts = data.get('timestamp_1m', 'N/A')
    print(f"{s}: {k} / {d} | ts: {ts}")

if stats['count'] > 0:
    first_symbol = stats['keys'][0]
    print(f"\nFull data for {first_symbol}:")
    print(json.dumps(store.get_symbol(first_symbol), indent=2))
