#!/usr/bin/env python3
"""Redeem remaining CTF tokens with minimal gas."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg
load_environment_from_gpg(None)
from eth_account import Account
from web3 import Web3

pk = os.getenv("POLY_API_WALLET_PK")
addr = Account.from_key(pk).address
w3 = Web3(Web3.HTTPProvider("https://polygon.drpc.org"))

CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
abi = [{"constant": False, "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "parentCollectionId", "type": "bytes32"}, {"name": "conditionId", "type": "bytes32"}, {"name": "indexSets", "type": "uint256[]"}], "name": "redeemPositions", "outputs": [], "stateMutability": "nonpayable", "type": "function"}]
erc20_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]

ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=abi)
neg = w3.eth.contract(address=Web3.to_checksum_address(NEG_RISK), abi=abi)
usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=erc20_abi)

to_redeem = [
    ("DiCaprio Oscars", "9d973b449ff50053259b945cebbd0fa6323879754ac2aba0a9897baa3528a719", ctf),
    ("La Equidad", "61da3df42d6f6fa4523351fb5f2885aae996acdf6200ae4d2fce0430ecfb1d31", neg),
]

usdc_before = usdc.functions.balanceOf(Web3.to_checksum_address(addr)).call()
pol = w3.eth.get_balance(Web3.to_checksum_address(addr))
gas_price = w3.eth.gas_price
print(f"Wallet: {addr}")
print(f"USDC.e: ${usdc_before / 1e6:.4f} | POL: {pol / 1e18:.6f} | Gas: {gas_price / 1e9:.1f} gwei")
print(f"Estimated cost per tx: {gas_price * 120000 / 1e18:.6f} POL")
print()

for name, cid_hex, contract in to_redeem:
    cid = bytes.fromhex(cid_hex)
    pol_now = w3.eth.get_balance(Web3.to_checksum_address(addr))
    estimated_cost = gas_price * 120000
    if pol_now < estimated_cost:
        print(f"  SKIP {name}: not enough POL ({pol_now / 1e18:.6f} < {estimated_cost / 1e18:.6f})")
        continue
    print(f"  Redeeming {name}...")
    try:
        nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(addr))
        tx = contract.functions.redeemPositions(Web3.to_checksum_address(USDC_E), b"\x00" * 32, cid, [1, 2]).build_transaction({"from": Web3.to_checksum_address(addr), "nonce": nonce, "gas": 120000, "gasPrice": gas_price, "chainId": 137})
        signed = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"    tx: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        status = receipt.get("status", 0)
        gas_used = receipt.get("gasUsed", 0)
        print(f"    status={status} gas={gas_used}")
        time.sleep(2)
    except Exception as e:
        print(f"    ERROR: {e}")

usdc_after = usdc.functions.balanceOf(Web3.to_checksum_address(addr)).call()
print(f"\nUSDC.e: ${usdc_before/1e6:.4f} → ${usdc_after/1e6:.4f} (gained ${(usdc_after-usdc_before)/1e6:.4f})")
