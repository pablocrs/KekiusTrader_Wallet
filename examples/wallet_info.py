"""
Example: Get wallet info and monitor balances
"""
import aiohttp
import asyncio

API_URL = "http://localhost:8003"

async def get_wallet_info():
    """Get complete wallet information"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_URL}/wallet/info") as response:
            if response.status == 200:
                result = await response.json()
                data = result["data"]
                
                print("💼 Wallet Information")
                print("=" * 50)
                print(f"Address: {data['address']}")
                print(f"SOL Balance: {data['sol_balance']:.6f} SOL")
                print(f"Token Accounts: {data['token_count']}")
                print(f"Cold Wallet: {data.get('cold_wallet', 'Not configured')}")
                print(f"Updated: {data['timestamp']}")
            else:
                print(f"❌ Error: {await response.text()}")

async def get_balance_summary():
    """Get quick balance summary"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_URL}/wallet/balance") as response:
            if response.status == 200:
                result = await response.json()
                data = result["data"]
                print(f"💰 SOL Balance: {data['sol_balance']:.6f} SOL")
            else:
                print(f"❌ Error: {await response.text()}")

async def monitor_balance(interval: int = 5):
    """Monitor balance changes"""
    print(f"Monitoring balance every {interval} seconds (Ctrl+C to stop)...")
    
    try:
        while True:
            await get_balance_summary()
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped monitoring")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "monitor":
        asyncio.run(monitor_balance())
    else:
        asyncio.run(get_wallet_info())
