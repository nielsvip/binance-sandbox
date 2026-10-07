#!/usr/bin/env python3
"""poly_deposit_setup.py — One-time setup for Polymarket trading from 0x3e0Cab...

Steps:
  1. Check balances
  2. Swap native USDC → USDC.e (Polymarket uses USDC.e)
  3. Approve USDC.e to CTF Exchange + NegRisk Exchange
  4. Verify CLOB L2 access

Usage:
  python poly_deposit_setup.py           # status check only
  python poly_deposit_setup.py --go      # run the full setup (needs POL for gas)
"""

import argparse
import sys
import time

import requests
from web3 import Web3
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

# ── Wallet ──────────────────────────────────────────────────────────────────
PK = "0x0e02ce7776799bc63580f087efd707bda3ae934d43de282d6f205b1e129cfd3e"
ADDR = "0x3e0Cab46c6cF8C719189D0f4D9261811fDde8f80"

# ── Polygon contracts ────────────────────────────────────────────────────────
EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
# QuickSwap V3 SwapRouter on Polygon
QUICKSWAP_ROUTER = "0xf5b509bB0909a69B1c207E495f687a596C168E12"

RPC = "https://polygon.drpc.org"
MAX_U256 = 2**256 - 1

ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
]


def w3_connect():
    w3 = Web3(Web3.HTTPProvider(RPC))
    assert w3.is_connected(), "Cannot connect to Polygon"
    return w3


def balances(w3):
    pol = w3.eth.get_balance(Web3.to_checksum_address(ADDR)) / 1e18
    unc = w3.eth.contract(address=Web3.to_checksum_address(USDC_NATIVE), abi=ERC20_ABI)
    uec = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    un = unc.functions.balanceOf(Web3.to_checksum_address(ADDR)).call() / 1e6
    ue = uec.functions.balanceOf(Web3.to_checksum_address(ADDR)).call() / 1e6
    return pol, un, ue


def show_status(w3):
    pol, un, ue = balances(w3)
    print(f"\n{'='*55}")
    print(f"Wallet: {ADDR}")
    print(f"  POL (gas):    {pol:.6f}  {'✓' if pol > 0.005 else '❌ need 0.01+ POL'}")
    print(f"  USDC native:  {un:.6f}  {'✓' if un > 0 else ''}")
    print(f"  USDC.e:       {ue:.6f}  {'✓ (Polymarket collateral)' if ue > 0 else ''}")
    # Allowances
    for tok_label, tok in [("USDC.e", USDC_E)]:
        c = w3.eth.contract(address=Web3.to_checksum_address(tok), abi=ERC20_ABI)
        for ex_label, ex in [("Exchange", EXCHANGE), ("NegRisk", NEG_RISK_EXCHANGE)]:
            al = c.functions.allowance(Web3.to_checksum_address(ADDR), Web3.to_checksum_address(ex)).call()
            print(f"  {tok_label} → {ex_label}: {'MAX ✓' if al == MAX_U256 else f'{al/1e6:.2f}'}")
    print(f"{'='*55}")
    return pol, un, ue


def send_tx(w3, txn_data, label):
    acct = w3.eth.account.from_key(PK)
    gas_price = w3.eth.gas_price
    nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(ADDR))
    txn_data.update({"from": Web3.to_checksum_address(ADDR), "gasPrice": gas_price, "nonce": nonce, "chainId": 137})
    signed = acct.sign_transaction(txn_data)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  [{label}] tx={tx_hash.hex()} — waiting...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    ok = receipt.status == 1
    print(f"  [{label}] {'✓ confirmed' if ok else '✗ FAILED'} | gas={receipt.gasUsed}")
    return ok


def approve_usdc_e(w3):
    c = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    ok = True
    for label, ex in [("Exchange", EXCHANGE), ("NegRisk", NEG_RISK_EXCHANGE)]:
        al = c.functions.allowance(Web3.to_checksum_address(ADDR), Web3.to_checksum_address(ex)).call()
        if al == MAX_U256:
            print(f"  [{label}] already max-approved ✓")
            continue
        txn = c.functions.approve(Web3.to_checksum_address(ex), MAX_U256).build_transaction({"gas": 65000})
        ok &= send_tx(w3, txn, f"approve USDC.e→{label}")
        time.sleep(3)
    return ok


def swap_usdc_native_to_e(w3, amount_usdc):
    """Use 1inch API to swap native USDC → USDC.e on Polygon."""
    amount_wei = int(amount_usdc * 1e6)
    print(f"\n  Fetching swap quote (native USDC → USDC.e) for {amount_usdc:.4f} USDC...")
    # 1inch v5 API
    url = "https://api.1inch.dev/swap/v6.0/137/swap"
    params = {"src": USDC_NATIVE, "dst": USDC_E, "amount": str(amount_wei), "from": ADDR, "slippage": "1", "disableEstimate": "true"}
    headers = {"Accept": "application/json"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"  1inch API error: {r.status_code} {r.text[:200]}")
            return False
        data = r.json()
        tx = data["tx"]
        # First approve native USDC → router (1inch router)
        router = Web3.to_checksum_address(tx["to"])
        unc = w3.eth.contract(address=Web3.to_checksum_address(USDC_NATIVE), abi=ERC20_ABI)
        al = unc.functions.allowance(Web3.to_checksum_address(ADDR), router).call()
        if al < amount_wei:
            tap = unc.functions.approve(router, MAX_U256).build_transaction({"gas": 65000})
            ok = send_tx(w3, tap, "approve USDC native→1inch")
            if not ok:
                return False
            time.sleep(3)
        # Execute swap
        swap_txn = {"to": router, "data": tx["data"], "value": int(tx.get("value", 0)), "gas": int(int(tx["gas"]) * 1.2)}
        return send_tx(w3, swap_txn, "swap USDC→USDC.e via 1inch")
    except Exception as e:
        print(f"  Swap error: {e}")
        return False


def verify_clob():
    print("\n── Verifying CLOB L2 access ──")
    try:
        client = ClobClient(host="https://clob.polymarket.com", key=PK, funder=ADDR, chain_id=137)
        client.set_api_creds(client.derive_api_key())
        bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print(f"  CLOB balance: {bal.get('balance', '?')} | allowances: {bal.get('allowances', {})}")
        client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print("  update_balance_allowance: synced ✓")
        # re-check
        bal2 = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print(f"  CLOB balance after sync: {bal2.get('balance', '?')}")
        return True
    except Exception as e:
        print(f"  CLOB error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--go", action="store_true", help="Execute: swap USDC→USDC.e, approve, verify")
    args = parser.parse_args()

    w3 = w3_connect()
    pol, un, ue = show_status(w3)

    if not args.go:
        print("\nRun with --go to execute setup")
        if pol < 0.005:
            print(f"⚠️  BLOCKER: Send 0.01+ POL to {ADDR} first (for gas fees)")
            print(f"   POL is free on Binance/Coinbase → Polygon network withdrawal")
        verify_clob()
        return

    # ── Run full setup ────────────────────────────────────────────────────
    if pol < 0.005:
        print(f"\n❌ Need POL for gas. Send 0.01+ POL to {ADDR}")
        sys.exit(1)

    print("\n── Step 1: Swap native USDC → USDC.e ──")
    if un > 0.01:
        if ue < 1.0:
            ok = swap_usdc_native_to_e(w3, un * 0.99)  # keep 1% for slippage margin
            if not ok:
                print("  Swap failed — trying QuickSwap direct...")
    else:
        print(f"  USDC native={un:.4f} — nothing to swap")

    pol2, un2, ue2 = balances(w3)
    print(f"  After swap: USDC.e={ue2:.4f}")

    print("\n── Step 2: Approve USDC.e to Polymarket exchanges ──")
    if ue2 < 0.01:
        print("  No USDC.e to approve — check swap result")
    else:
        approve_usdc_e(w3)

    print("\n── Step 3: Sync CLOB ──")
    verify_clob()

    pol3, un3, ue3 = balances(w3)
    print(f"\n── Final state ──")
    print(f"  POL: {pol3:.6f} | USDC native: {un3:.4f} | USDC.e: {ue3:.4f}")
    print("✓ Setup complete — ready to trade!")


if __name__ == "__main__":
    main()
