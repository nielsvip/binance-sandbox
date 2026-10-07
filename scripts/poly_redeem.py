#!/usr/bin/env python3
"""poly_redeem.py — Redeem resolved Polymarket CTF tokens for USDC.e.

When a market resolves YES, your YES tokens are worth $1.00 each.
But they don't auto-convert — you must call redeemPositions() on-chain.

For regular markets: call CTF contract directly.
For negRisk markets: call NegRisk Adapter instead.

Usage:
  python poly_redeem.py              # dry-run: show redeemable positions
  python poly_redeem.py --execute    # actually redeem on-chain
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg

load_environment_from_gpg(None)

from eth_account import Account
from web3 import Web3

RPC = "https://polygon.drpc.org"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ZERO_BYTES32 = b"\x00" * 32

REDEEM_ABI = [
    {"constant": False, "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "parentCollectionId", "type": "bytes32"}, {"name": "conditionId", "type": "bytes32"}, {"name": "indexSets", "type": "uint256[]"}], "name": "redeemPositions", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}, {"name": "_id", "type": "uint256"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
]

# Known positions with their token IDs and condition IDs
# Resolved via Gamma API lookup on 2026-03-16
KNOWN_POSITIONS = [
    {"name": "MBJ Oscars", "yes_token": "50234231681845123897762455841846523448929127730863555921965983515798716361286", "condition_id": "0xea678b55f9ff24e392546a9dd569871558939b918824a0acacadda8a6bf3268e", "closed": True, "neg_risk": False},
    {"name": "DiCaprio Oscars", "yes_token": "100455809934660508281774387126940584837670688324333246108530244181172024578316", "condition_id": "0x9d973b449ff50053259b945cebbd0fa6323879754ac2aba0a9897baa3528a719", "closed": True, "neg_risk": False},
    {"name": "Zootopia", "yes_token": "6683949870969736504673330682903980023983161470414879350044424535961811133692", "condition_id": "0xf562feabe9922f78e99b488df4fcef6be8cbc831605388751538d178585007aa", "closed": False, "neg_risk": True},
    {"name": "La Equidad", "yes_token": "48528144750401773861975493621463699784011704652855829804001292108743502751834", "condition_id": "0x61da3df42d6f6fa4523351fb5f2885aae996acdf6200ae4d2fce0430ecfb1d31", "closed": True, "neg_risk": True},
    {"name": "Texas turnout", "yes_token": "29362441355608873420741430772518548539455867046132871973807074335688662489281", "condition_id": "0xf7fa7d8259fe15a692e33eb14e52859dd96c4b7902d4d19a0571e8c83e14b517", "closed": False, "neg_risk": True},
    {"name": "Gemini exam", "yes_token": "106240383251892908356948280904420032586349972607505214050148587955663418544685", "condition_id": "0x257d531f3a6b96008d0a58a0169eed8af4faaddae8f7a38961739fc8ee7a7729", "closed": False, "neg_risk": False},
    {"name": "Bangladesh", "yes_token": "83059823129472098827493678045304606487260791807230197558769229846885915295810", "condition_id": "0x15c45c09c05465d96346e97d9f5e471fe14328c866359ede425dc4bf8d54dabf", "closed": False, "neg_risk": True},
    {"name": "Nepal PM", "yes_token": "67636248562641989253322881833295634597348537935436116678308038493491931802075", "condition_id": "0x73de4d9d13eb94079d2e707b34ad3a6c6923a5d5a27775127f9139946a08152f", "closed": False, "neg_risk": True},
]


def main():
    parser = argparse.ArgumentParser(description="Redeem resolved Polymarket CTF tokens")
    parser.add_argument("--execute", action="store_true", help="Actually send redeem transactions")
    args = parser.parse_args()

    pk = os.getenv("POLY_API_WALLET_PK")
    if not pk:
        print("ERROR: POLY_API_WALLET_PK not set")
        return
    addr = Account.from_key(pk).address
    w3 = Web3(Web3.HTTPProvider(RPC))
    if not w3.is_connected():
        print("ERROR: Cannot connect to Polygon RPC")
        return

    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=REDEEM_ABI)
    neg_adapter = w3.eth.contract(address=Web3.to_checksum_address(NEG_RISK_ADAPTER), abi=REDEEM_ABI)
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)

    usdc_before = usdc.functions.balanceOf(Web3.to_checksum_address(addr)).call()
    pol_bal = w3.eth.get_balance(Web3.to_checksum_address(addr))
    print(f"Wallet: {addr}")
    print(f"USDC.e: ${usdc_before / 1e6:.4f}")
    print(f"POL (gas): {pol_bal / 1e18:.6f}")
    print()

    # Check token balances
    redeemable = []
    for pos in KNOWN_POSITIONS:
        bal = ctf.functions.balanceOf(Web3.to_checksum_address(addr), int(pos["yes_token"])).call()
        if bal > 0:
            status = "REDEEMABLE" if pos["closed"] else "NOT YET CLOSED"
            adapter = "NegRisk" if pos["neg_risk"] else "CTF"
            print(f"  {status:16s} | {bal/1e6:>8.2f} tokens | {adapter:7s} | {pos['name']}")
            if pos["closed"]:
                redeemable.append({**pos, "balance": bal})

    if not redeemable:
        print("\nNo redeemable (closed) positions with tokens found.")
        return

    print(f"\n{len(redeemable)} positions ready to redeem")
    total = sum(r["balance"] for r in redeemable) / 1e6
    print(f"Total tokens to redeem: ${total:.2f}")

    if not args.execute:
        print("\nDry-run complete. Use --execute to redeem on-chain.")
        return

    redeemed = 0
    for r in redeemable:
        cid_bytes = bytes.fromhex(r["condition_id"].replace("0x", ""))
        contract = neg_adapter if r["neg_risk"] else ctf
        contract_name = "NegRiskAdapter" if r["neg_risk"] else "CTF"
        print(f"\n  Redeeming {r['name']} via {contract_name}...")
        print(f"    tokens: {r['balance']/1e6:.2f} | conditionId: {r['condition_id'][:20]}...")
        try:
            nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(addr))
            gas_price = w3.eth.gas_price
            tx = contract.functions.redeemPositions(Web3.to_checksum_address(USDC_E), ZERO_BYTES32, cid_bytes, [1, 2]).build_transaction({"from": Web3.to_checksum_address(addr), "nonce": nonce, "gas": 300000, "gasPrice": int(gas_price * 1.2), "chainId": 137})
            signed = w3.eth.account.sign_transaction(tx, pk)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"    tx: {tx_hash.hex()}")
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt["status"] == 1:
                print(f"    SUCCESS (gas: {receipt['gasUsed']})")
                redeemed += 1
            else:
                print(f"    FAILED (status=0, reverted)")
            time.sleep(2)
        except Exception as e:
            print(f"    ERROR: {e}")

    usdc_after = usdc.functions.balanceOf(Web3.to_checksum_address(addr)).call()
    gained = (usdc_after - usdc_before) / 1e6
    print(f"\nRedeemed {redeemed}/{len(redeemable)} positions")
    print(f"USDC.e: ${usdc_before/1e6:.4f} → ${usdc_after/1e6:.4f} (gained ${gained:.4f})")


if __name__ == "__main__":
    main()
