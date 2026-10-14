#!/opt/anaconda3/envs/binance_env/bin/python
"""ez_positions_service_main.py <account> — THE single writer of <account>/{long,short}_positions.json (USER 2026-10-14).
Same fetch (REST every 6 s) + WebSocket machinery as ez_positions_realtime.py, but role=service: always writes and
heartbeats <account>/.positions_service_heartbeat.json. ez_positions_realtime.py stays a warm standby that writes only
while this heartbeat is stale; ez_manage never writes position files."""
import asyncio
import os
os.environ["EZ_POSITION_WRITER_ROLE"] = "service"
import ez_positions_realtime
if __name__ == "__main__":
    asyncio.run(ez_positions_realtime.main())
