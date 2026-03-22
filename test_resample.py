import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import pytz
from tradier_indicators import resample_tf

ET = pytz.timezone("America/New_York")

def test_4h():
    # Create some dummy 1h data
    now = datetime.now(timezone.utc)
    base = now.replace(hour=9, minute=0, second=0, microsecond=0)
    
    data = []
    for i in range(20):
        ts = base + timedelta(hours=i)
        data.append({
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "open": 100 + i,
            "high": 105 + i,
            "low": 95 + i,
            "close": 102 + i,
            "volume": 1000
        })
    
    df = pd.DataFrame(data)
    print("Input 1h data (first 5):")
    print(df.head())
    
    out = resample_tf(df, "4h")
    print("\nResampled 4h data:")
    print(out)

if __name__ == "__main__":
    test_4h()
