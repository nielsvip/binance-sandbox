#!/usr/bin/env python3
"""Quick test: place a $0.01 order on Limitless to verify signing works."""
import asyncio, secrets, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eth_account import Account
from limitless_sdk import LimitlessClient
from limitless_sdk.orders.signer import OrderSigner
from limitless_sdk.types.orders import OrderSigningConfig, UnsignedOrder
import aiohttp

PK = os.getenv("LIMITLESS_PK", "7366d7afacf924d320a83e4413aa5386dd5e593a954eb01a3572c1d6de5b1d8b")
API_KEY = os.getenv("LIMITLESS_API_KEY", "lmts_Mb9iArvZpzhKYS_z_6hnnnUkA0fC-kUNGqdYM2683aHYu2FHXVVtOyhEVqCU")
EXCHANGE = "0x05c748E2f4DcDe0ec9Fa8DDc40DE6b867f923fa5"

async def main():
    acct = Account.from_key(PK)
    addr = acct.address
    signer = OrderSigner(acct)
    client = LimitlessClient(private_key=PK, api_key=API_KEY)
    await client.create_session()
    profile = await client.get_user_profile()
    owner_id = profile.get("id")
    print(f"Owner: {owner_id} Addr: {addr}")
    markets = await client.get_active_markets(page=1, limit=1)
    m = markets["data"][0]
    slug = m["slug"]
    title = m["title"]
    yes_tok = m["tokens"]["yes"]
    print(f"Market: {title[:50]} slug={slug[:30]}")
    salt = secrets.randbelow(10**18)
    config = OrderSigningConfig(chain_id=8453, contract_address=EXCHANGE)
    unsigned = UnsignedOrder(salt=salt, maker=addr, signer=addr, taker="0x0000000000000000000000000000000000000000", token_id=yes_tok, maker_amount=1000000, taker_amount=100000000, expiration=0, nonce=0, fee_rate_bps=300, side=0, signature_type=0)
    sig = await signer.sign_order(unsigned, config)
    print(f"Sig: {sig[:30]}...")
    payload = {"order": {"salt": salt, "maker": addr, "signer": addr, "taker": "0x0000000000000000000000000000000000000000", "tokenId": yes_tok, "makerAmount": 1000000, "takerAmount": 100000000, "expiration": "0", "nonce": 0, "feeRateBps": 300, "side": 0, "signatureType": 0, "signature": sig, "price": 0.01}, "marketSlug": slug, "ownerId": owner_id, "orderType": "GTC"}
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.limitless.exchange/orders", json=payload, headers=headers) as r:
            print(f"Status: {r.status}")
            data = await r.json()
            print(json.dumps(data, indent=2)[:400])
    await client.close_session()

asyncio.run(main())
