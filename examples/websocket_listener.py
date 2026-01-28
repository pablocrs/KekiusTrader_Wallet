"""
Example: WebSocket client to monitor wallet operations in real-time
"""
import asyncio
import websockets
import json

WS_URL = "ws://localhost:8003/ws"

async def listen_to_wallet():
    """Connect to wallet WebSocket and listen for updates"""
    print(f"Connecting to {WS_URL}...")
    
    async with websockets.connect(WS_URL) as websocket:
        print("✅ Connected to KekiusTrader_Wallet WebSocket")
        
        # Keep connection alive with heartbeat
        async def heartbeat():
            while True:
                await asyncio.sleep(30)
                await websocket.send("ping")
        
        heartbeat_task = asyncio.create_task(heartbeat())
        
        try:
            async for message in websocket:
                data = json.loads(message)
                message_type = data.get("type")
                
                if message_type == "connected":
                    print(f"📡 {data.get('message')}")
                    print(f"Features: {', '.join(data.get('features', []))}")
                
                elif message_type == "transaction":
                    tx_type = data.get("tx_type")
                    status = data.get("status")
                    signature = data.get("signature")
                    
                    status_emoji = {
                        "pending": "⏳",
                        "confirmed": "✅",
                        "failed": "❌"
                    }.get(status, "📝")
                    
                    print(f"\n{status_emoji} {tx_type} {status.upper()}")
                    print(f"Signature: {signature}")
                    print(f"Explorer: {data.get('explorer')}")
                    
                    details = data.get("details", {})
                    if details:
                        print(f"Details: {json.dumps(details, indent=2)}")
                
                elif message_type == "balance_update":
                    print(f"\n💰 Balance Update:")
                    print(json.dumps(data.get("data"), indent=2))
                
                elif message_type == "alert":
                    alert_type = data.get("alert_type")
                    message_text = data.get("message")
                    
                    alert_emoji = {
                        "info": "ℹ️",
                        "success": "✅",
                        "warning": "⚠️",
                        "error": "❌"
                    }.get(alert_type, "📢")
                    
                    print(f"\n{alert_emoji} {message_text}")
                
                else:
                    print(f"\n📨 {message_type}: {data}")
                    
        except KeyboardInterrupt:
            print("\nDisconnecting...")
        finally:
            heartbeat_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(listen_to_wallet())
    except KeyboardInterrupt:
        pass
