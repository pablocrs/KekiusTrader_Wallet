"""
Example: Buy tokens with SOL via KekiusTrader_Wallet API
"""
import aiohttp
import asyncio

API_URL = "http://localhost:8003"

async def buy_token():
    """Example buy operation"""
    
    buy_request = {
        "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "amount_sol": 0.01,  # Buy with 0.01 SOL
        "slippage_bps": 100,  # 1% slippage
        "speed_mode": "ULTRA_FAST"
    }
    
    async with aiohttp.ClientSession() as session:
        # Execute buy
        async with session.post(f"{API_URL}/buy", json=buy_request) as response:
            if response.status == 200:
                result = await response.json()
                print("✅ Buy order submitted!")
                print(f"Signature: {result['data']['signature']}")
                print(f"Output amount: {result['data']['output_amount']}")
                print(f"Solscan: https://solscan.io/tx/{result['data']['signature']}")
            else:
                error = await response.text()
                print(f"❌ Error: {error}")

if __name__ == "__main__":
    asyncio.run(buy_token())
