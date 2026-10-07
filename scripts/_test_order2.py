#!/usr/bin/env python3
"""Test order with nonce=1 (after first order used nonce=0)."""
import asyncio, secrets, os, sys, json, aiohttp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eth_account import Account
from limitless_sdk.orders.signer import OrderSigner
from limitless_sdk.types.orders import OrderSigningConfig, UnsignedOrder

PK = "7366d7afacf924d320a83e4413aa5386dd5e593a954eb01a3572c1d6de5b1d8b"
API_KEY = "lmts_Mb9iArvZpzhKYS_z_6hnnnUkA0fC-kUNGqdYM2683aHYu2FHXVVtOyhEVqCU"
EXCHANGE = "0x05c748E2f4DcDe0ec9Fa8DDc40DE6b867f923fa5"

async def main():
    acct = Account.from_key(PK)
    addr = acct.address
    signer = OrderSigner(acct)
    async with aiohttp.ClientSession() as s:
        # Get profile
        async with s.get(f'https://api.limitless.exchange/profiles/{addr}', headers={'X-API-Key': API_KEY}) as r:
            profile = await r.json()
            owner_id = profile.get("id")
        # Get market
        async with s.get('https://api.limitless.exchange/markets/active', params={'limit': 1, 'page': 1}) as r:
            m = (await r.json())['data'][0]
            slug = m['slug']
            yes_tok = m['tokens']['yes']
            print(f"Market: {m['title'][:50]}")
        # Try nonce 0 and 1
        for nonce in [0, 1]:
            salt = secrets.randbelow(10**18)
            config = OrderSigningConfig(chain_id=8453, contract_address=EXCHANGE)
            unsigned = UnsignedOrder(salt=salt, maker=addr, signer=addr, taker="0x0000000000000000000000000000000000000000", token_id=yes_tok, maker_amount=1000000, taker_amount=100000000, expiration=0, nonce=nonce, fee_rate_bps=300, side=0, signature_type=0)
            sig = await signer.sign_order(unsigned, config)
            payload = {"order": {"salt": salt, "maker": addr, "signer": addr, "taker": "0x0000000000000000000000000000000000000000", "tokenId": yes_tok, "makerAmount": 1000000, "takerAmount": 100000000, "expiration": "0", "nonce": nonce, "feeRateBps": 300, "side": 0, "signatureType": 0, "signature": sig, "price": 0.01}, "marketSlug": slug, "ownerId": owner_id, "orderType": "GTC"}
            async with s.post("https://api.limitless.exchange/orders", json=payload, headers={"X-API-Key": API_KEY, "Content-Type": "application/json"}) as r:
                status = r.status
                data = await r.json()
                msg = data.get("message", "") if isinstance(data, dict) else ""
                if status == 201:
                    print(f"  nonce={nonce}: SUCCESS! order_id={data.get('order',{}).get('id','?')[:20]}")
                else:
                    print(f"  nonce={nonce}: {status} {str(msg)[:80]}")

asyncio.run(main())
