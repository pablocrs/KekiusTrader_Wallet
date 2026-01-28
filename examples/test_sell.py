"""
Example: Sell tokens for SOL via KekiusTrader_Wallet API
"""
import aiohttp
import asyncio

API_URL = "http://localhost:8003"

async def sell_token():
    """Example sell operation"""
    
    sell_request = {
        "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "amount": 10000,  # Sell 0.01 USDC (6 decimals)
        "slippage_bps": 100,
        "speed_mode": "ULTRA_FAST"
    }
    
    async with aiohttp.ClientSession() as session:
        # Execute sell
        async with session.post(f"{API_URL}/sell", json=sell_request) as response:
            if response.status == 200:
                result = await response.json()
                print("✅ Sell order submitted!")
                print(f"Signature: {result['data']['signature']}")
                print(f"Output SOL: {result['data']['output_amount_sol']:.6f}")
                print(f"Solscan: https://solscan.io/tx/{result['data']['signature']}")
            else:
                error = await response.text()
                print(f"❌ Error: {error}")

async def sell_all_token():
    """Example sell-all operation"""
    
    sell_all_request = {
        "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "slippage_bps": 150,  # Higher slippage for full liquidation
        "speed_mode": "ULTRA_FAST"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_URL}/sell-all", json=sell_all_request) as response:
            if response.status == 200:
                result = await response.json()
                print("✅ Sell-all order submitted!")
                print(f"Signature: {result['data']['signature']}")
                print(f"Solscan: https://solscan.io/tx/{result['data']['signature']}")
            else:
                error = await response.text()
                print(f"❌ Error: {error}")

if __name__ == "__main__":
    print("Choose operation:")
    print("1. Sell specific amount")
    print("2. Sell all")
    choice = input("Enter choice (1 or 2): ")
    
    if choice == "1":
        asyncio.run(sell_token())
    elif choice == "2":
        asyncio.run(sell_all_token())
