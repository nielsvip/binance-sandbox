#!/usr/bin/env python3
"""Quick check of Polymarket wallet balances + redeem resolved positions."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg
load_environment_from_gpg(None)

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
from eth_account import Account
from web3 import Web3

pk = os.getenv("POLY_API_WALLET_PK")
addr = Account.from_key(pk).address

# CLOB balance
client = ClobClient(host="https://clob.polymarket.com", key=pk, funder=addr, chain_id=137)
creds = client.derive_api_key()
client = ClobClient(host="https://clob.polymarket.com", key=pk, funder=addr, chain_id=137, creds=creds)

bal = client.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
print(f"CLOB USDC balance: ${int(bal.get('balance', 0)) / 1e6:.4f}")

# On-chain USDC.e balance
rpc = "https://polygon.drpc.org"
w3 = Web3(Web3.HTTPProvider(rpc))
usdc_e = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
erc20_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
contract = w3.eth.contract(address=Web3.to_checksum_address(usdc_e), abi=erc20_abi)
onchain_bal = contract.functions.balanceOf(Web3.to_checksum_address(addr)).call()
print(f"On-chain USDC.e: ${onchain_bal / 1e6:.4f}")

# Check CTF token balances for resolved positions
ctf_addr = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # CTF contract
ctf_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}, {"name": "_id", "type": "uint256"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
ctf = w3.eth.contract(address=Web3.to_checksum_address(ctf_addr), abi=ctf_abi)

# Load live positions to check resolved token balances
pos_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data/poly/highconf/live_positions.json")
if os.path.exists(pos_file):
    with open(pos_file) as f:
        data = json.load(f)
    # Check closed positions (resolved but maybe not redeemed)
    for p in data.get("closed", []):
        token = p.get("yes_token", "")
        if not token:
            continue
        try:
            bal = ctf.functions.balanceOf(Web3.to_checksum_address(addr), int(token)).call()
            if bal > 0:
                print(f"  UNREDEEMED: {bal/1e6:.4f} tokens | {p.get('question', '')[:50]} | reason={p.get('reason', '')}")
        except Exception as e:
            print(f"  check failed: {e}")
    # Check open positions
    for mid, p in data.get("positions", {}).items():
        token = p.get("yes_token", "")
        if not token:
            continue
        try:
            bal = ctf.functions.balanceOf(Web3.to_checksum_address(addr), int(token)).call()
            if bal > 0:
                print(f"  HOLDING: {bal/1e6:.4f} tokens | {p.get('question', '')[:50]}")
        except Exception as e:
            pass

print("\nNote: Resolved YES tokens need to be redeemed via CTF Exchange to get USDC back.")
print("Each winning YES token = $1.00 USDC after redemption.")
