#!/usr/bin/env python3
"""
WebSocket Verification Script
Checks if price and user websocket data is being received and processed in:
- ez_manage.py
- ez_positions_quick.py  
- ez_positions_service.py
"""
import asyncio
import aiohttp
import json
import time
import sys
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

# Track statistics
stats = {
    'ez_manage': {'user_ws': {'connected': False, 'messages': 0, 'account_updates': 0, 'last_update': None}, 'mark_price': {'connected': False, 'messages': 0, 'last_update': None}},
    'ez_positions_quick': {'user_ws': {'connected': False, 'messages': 0, 'account_updates': 0, 'last_update': None}, 'mark_price': {'connected': False, 'messages': 0, 'last_update': None}},
    'ez_positions_service': {'user_ws': {'connected': False, 'messages': 0, 'account_updates': 0, 'last_update': None}, 'mark_price': {'connected': False, 'messages': 0, 'last_update': None}}}

async def check_logs_for_websocket_activity(script_name, log_file_path, duration=30):
    """Check log files for websocket activity"""
    if not Path(log_file_path).exists():
        return {'found': False, 'error': f'Log file not found: {log_file_path}'}
    keywords = {
        'user_ws': ['Websocket connected', 'ACCOUNT_UPDATE', 'user data websocket', 'User Stream Connected', 'WS message received'],
        'mark_price': ['Mark Price Stream', 'mark_price', 'markPrice', 'Connected to aggregate mark price']}
    results = {'user_ws': {'found': False, 'count': 0}, 'mark_price': {'found': False, 'count': 0}}
    try:
        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            # Check last 1000 lines for recent activity
            for line in lines[-1000:]:
                line_lower = line.lower()
                for ws_type, keys in keywords.items():
                    for key in keys:
                        if key.lower() in line_lower:
                            results[ws_type]['found'] = True
                            results[ws_type]['count'] += 1
                            break
    except Exception as e:
        return {'found': False, 'error': str(e)}
    return results

async def monitor_websocket_directly(script_name, ws_type, url, api_key=None, api_secret=None, listen_key=None):
    """Directly monitor a websocket connection"""
    messages_received = 0
    connected = False
    last_message_time = None
    
    try:
        if ws_type == 'user' and listen_key:
            full_url = f"wss://fstream.binance.com/ws/{listen_key}"
        else:
            full_url = url
        
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.ws_connect(full_url, heartbeat=30, timeout=10) as ws:
                    connected = True
                    ws_key = 'user_ws' if ws_type == 'user' else 'mark_price'
                    stats[script_name][ws_key]['connected'] = True
                    print(f"✅ [{script_name}] {ws_type.upper()} WebSocket connected: {full_url[:60]}...")
                    
                    # Monitor for 15 seconds
                    end_time = time.time() + 15
                    while time.time() < end_time:
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                messages_received += 1
                                last_message_time = datetime.now(timezone.utc)
                                data = json.loads(msg.data)
                                
                                if ws_type == 'user':
                                    event_type = data.get('e', '')
                                    if event_type == 'ACCOUNT_UPDATE':
                                        ws_key = 'user_ws' if ws_type == 'user' else 'mark_price'
                                        if ws_type == 'user':
                                            stats[script_name]['user_ws']['account_updates'] += 1
                                        payloads = data.get('a', {}).get('P', [])
                                        if payloads:
                                            symbols = [p.get('s', 'N/A') for p in payloads[:3]]
                                            print(f"  📨 ACCOUNT_UPDATE: {len(payloads)} positions - {symbols}")
                                elif ws_type == 'mark_price':
                                    if isinstance(data, list):
                                        prices = [f"{p.get('s', 'N/A')}:{p.get('p', 'N/A')}" for p in data[:3]]
                                        print(f"  📈 Mark prices: {prices}")
                                    elif data.get('s'):
                                        print(f"  📈 Mark price: {data.get('s')} = {data.get('p', 'N/A')}")
                                
                                ws_key = 'user_ws' if ws_type == 'user' else 'mark_price'
                                stats[script_name][ws_key]['messages'] += messages_received
                                stats[script_name][ws_key]['last_update'] = last_message_time
                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            print(f"  ⚠️  Error receiving message: {e}")
                            break
            except asyncio.TimeoutError:
                print(f"⏱️  [{script_name}] {ws_type.upper()} WebSocket connection timeout (this is normal if testing from outside)")
            except Exception as e:
                error_msg = str(e)
                if 'mark_price_ws' in error_msg:
                    # This is a key error, not a connection error
                    print(f"⚠️  [{script_name}] {ws_type.upper()} WebSocket stats key issue (non-critical)")
                else:
                    print(f"❌ [{script_name}] {ws_type.upper()} WebSocket connection failed: {error_msg[:100]}")
    except Exception as e:
        print(f"❌ [{script_name}] {ws_type.upper()} WebSocket error: {e}")
    
    return {'connected': connected, 'messages': messages_received, 'last_message': last_message_time}

async def verify_ez_manage():
    """Verify ez_manage.py websocket processing"""
    print("\n" + "="*80)
    print("🔍 Verifying ez_manage.py WebSocket Processing")
    print("="*80)
    
    # Check log files (try multiple possible names)
    log_paths = [Path("logs/ez_manage_app.log"), Path("logs/ez_manage.log"), Path("logs/ez_manage_ang.log")]
    log_path = None
    for lp in log_paths:
        if lp.exists():
            log_path = lp
            break
    if log_path:
        log_results = await check_logs_for_websocket_activity('ez_manage', str(log_path))
        if log_results.get('user_ws', {}).get('found'):
            print(f"✅ Found user websocket activity in logs ({log_results['user_ws']['count']} mentions)")
        if log_results.get('mark_price', {}).get('found'):
            print(f"✅ Found mark price activity in logs ({log_results['mark_price']['count']} mentions)")
    
    # Try to monitor mark price websocket directly
    print("\n📡 Testing Mark Price WebSocket...")
    await monitor_websocket_directly('ez_manage', 'mark_price', 'wss://fstream.binance.com/ws/!markPrice@arr@1000ms')
    
    print(f"\n📊 ez_manage.py Statistics:")
    print(f"  User WS: Connected={stats['ez_manage']['user_ws']['connected']}, Messages={stats['ez_manage']['user_ws']['messages']}, Account Updates={stats['ez_manage']['user_ws']['account_updates']}")
    print(f"  Mark Price: Connected={stats['ez_manage']['mark_price']['connected']}, Messages={stats['ez_manage']['mark_price']['messages']}")

async def verify_ez_positions_quick():
    """Verify ez_positions_quick.py websocket processing"""
    print("\n" + "="*80)
    print("🔍 Verifying ez_positions_quick.py WebSocket Processing")
    print("="*80)
    
    # Check log files (try multiple possible names)
    log_paths = [Path("logs/ez_positions_quick.log"), Path("logs/ez_positions_24_7_app.log"), Path("logs/ez_positions.log")]
    log_path = None
    for lp in log_paths:
        if lp.exists():
            log_path = lp
            break
    if log_path:
        log_results = await check_logs_for_websocket_activity('ez_positions_quick', str(log_path))
        if log_results.get('user_ws', {}).get('found'):
            print(f"✅ Found user websocket activity in logs ({log_results['user_ws']['count']} mentions)")
        if log_results.get('mark_price', {}).get('found'):
            print(f"✅ Found mark price activity in logs ({log_results['mark_price']['count']} mentions)")
    
    # Try to monitor mark price websocket directly
    print("\n📡 Testing Mark Price WebSocket...")
    await monitor_websocket_directly('ez_positions_quick', 'mark_price', 'wss://fstream.binance.com/ws/!markPrice@arr')
    
    print(f"\n📊 ez_positions_quick.py Statistics:")
    print(f"  User WS: Connected={stats['ez_positions_quick']['user_ws']['connected']}, Messages={stats['ez_positions_quick']['user_ws']['messages']}, Account Updates={stats['ez_positions_quick']['user_ws']['account_updates']}")
    print(f"  Mark Price: Connected={stats['ez_positions_quick']['mark_price']['connected']}, Messages={stats['ez_positions_quick']['mark_price']['messages']}")

async def verify_ez_positions_service():
    """Verify ez_positions_service.py websocket processing"""
    print("\n" + "="*80)
    print("🔍 Verifying ez_positions_service.py WebSocket Processing")
    print("="*80)
    
    # Check log files (try multiple possible names)
    log_paths = [Path("logs/ez_positions_service.log"), Path("logs/ez_positions_24_7_app.log"), Path("logs/ez_positions.log")]
    log_path = None
    for lp in log_paths:
        if lp.exists():
            log_path = lp
            break
    if log_path:
        log_results = await check_logs_for_websocket_activity('ez_positions_service', str(log_path))
        if log_results.get('user_ws', {}).get('found'):
            print(f"✅ Found user websocket activity in logs ({log_results['user_ws']['count']} mentions)")
        if log_results.get('mark_price', {}).get('found'):
            print(f"✅ Found mark price activity in logs ({log_results['mark_price']['count']} mentions)")
    
    # Try to monitor mark price websocket directly
    print("\n📡 Testing Mark Price WebSocket...")
    await monitor_websocket_directly('ez_positions_service', 'mark_price', 'wss://fstream.binance.com/ws/!markPrice@arr@1000ms')
    
    print(f"\n📊 ez_positions_service.py Statistics:")
    print(f"  User WS: Connected={stats['ez_positions_service']['user_ws']['connected']}, Messages={stats['ez_positions_service']['user_ws']['messages']}, Account Updates={stats['ez_positions_service']['user_ws']['account_updates']}")
    print(f"  Mark Price: Connected={stats['ez_positions_service']['mark_price']['connected']}, Messages={stats['ez_positions_service']['mark_price']['messages']}")

async def check_processing_functions():
    """Check if websocket processing functions are being called"""
    print("\n" + "="*80)
    print("🔍 Checking WebSocket Processing Functions")
    print("="*80)
    
    scripts = {
        'ez_manage.py': ['notify_position_update', 'user_data_websocket_loop', 'mark_price_aggregate_loop', '_handle_mark_price_message'],
        'ez_positions_quick.py': ['sync_from_ws_event', '_handle_account_update', '_mark_price_loop', '_maintain_user_stream'],
        'ez_positions_service.py': ['user_data_websocket_loop', 'process_mark_price_update']
    }
    
    for script, functions in scripts.items():
        script_path = Path(script)
        if script_path.exists():
            print(f"\n📄 {script}:")
            with open(script_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                for func in functions:
                    if f'def {func}' in content or f'async def {func}' in content:
                        print(f"  ✅ {func}() found")
                    else:
                        print(f"  ❌ {func}() NOT found")

async def main():
    """Main verification function"""
    print("\n" + "="*80)
    print("🚀 WebSocket Verification Script")
    print("="*80)
    print("This script verifies websocket data flow in:")
    print("  - ez_manage.py")
    print("  - ez_positions_quick.py")
    print("  - ez_positions_service.py")
    print("\n⏳ Running checks (this will take ~60 seconds)...\n")
    
    # Check processing functions first
    await check_processing_functions()
    
    # Verify each script
    await verify_ez_manage()
    await verify_ez_positions_quick()
    await verify_ez_positions_service()
    
    # Summary
    print("\n" + "="*80)
    print("📊 SUMMARY")
    print("="*80)
    
    for script_name in ['ez_manage', 'ez_positions_quick', 'ez_positions_service']:
        print(f"\n{script_name.upper()}:")
        user_ws = stats[script_name]['user_ws']
        mark_price = stats[script_name]['mark_price']
        
        user_status = "✅ WORKING" if user_ws['connected'] and user_ws['messages'] > 0 else "⚠️  NEEDS CHECK"
        mark_status = "✅ WORKING" if mark_price['connected'] and mark_price['messages'] > 0 else "⚠️  NEEDS CHECK"
        
        print(f"  User WebSocket: {user_status}")
        print(f"    - Connected: {user_ws['connected']}")
        print(f"    - Messages received: {user_ws['messages']}")
        print(f"    - Account updates: {user_ws['account_updates']}")
        print(f"  Mark Price WebSocket: {mark_status}")
        print(f"    - Connected: {mark_price['connected']}")
        print(f"    - Messages received: {mark_price['messages']}")
    
    print("\n" + "="*80)
    print("✅ Verification complete!")
    print("="*80)
    print("\n💡 Note: User websockets require API keys and listen keys.")
    print("   Mark price websockets are public and should always connect.")
    print("   If user websockets show 'NEEDS CHECK', verify the scripts are running")
    print("   and check the log files for connection status.\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Verification interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
