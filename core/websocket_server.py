from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger
from typing import List, Dict, Any
import json
from decimal import Decimal

class CustomJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for Decimal types"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)

class WebSocketServer:
    """
    WebSocket server for real-time transaction updates.
    Broadcasts transaction status, balance changes, and alerts.
    """
    
    def __init__(self, app):
        """
        Args:
            app: FastAPI app instance
        """
        self.app = app
        self.active_connections: List[WebSocket] = []
        self.setup_routes()
    
    def setup_routes(self):
        """Setup WebSocket routes"""
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await self.connect(websocket)
            try:
                while True:
                    # Keep connection alive, receive heartbeat
                    await websocket.receive_text()
            except WebSocketDisconnect:
                await self.disconnect(websocket)
    
    async def connect(self, websocket: WebSocket):
        """Accept and register WebSocket connection"""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"✅ WebSocket connected. Total: {len(self.active_connections)}")
        
        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to KekiusTrader_Wallet",
            "features": ["transaction_updates", "balance_changes", "profit_alerts"]
        })
    
    async def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"❌ WebSocket disconnected. Total: {len(self.active_connections)}")
    
    async def broadcast(self, message: Dict[str, Any]):
        """
        Broadcast message to all connected clients.
        
        Args:
            message: Message dict to broadcast
        """
        if not self.active_connections:
            return
        
        json_message = json.dumps(message, cls=CustomJSONEncoder)
        
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(json_message)
            except Exception as e:
                logger.error(f"Error broadcasting to client: {e}")
                await self.disconnect(connection)
    
    async def broadcast_transaction(
        self,
        tx_type: str,
        signature: str,
        status: str,
        details: Dict[str, Any]
    ):
        """
        Broadcast transaction update.
        
        Args:
            tx_type: BUY, SELL, SELL_ALL, PROFIT_CONVERT
            signature: Transaction signature
            status: pending, confirmed, failed
            details: Additional transaction details
        """
        message = {
            "type": "transaction",
            "tx_type": tx_type,
            "signature": signature,
            "status": status,
            "details": details,
            "explorer": f"https://solscan.io/tx/{signature}"
        }
        await self.broadcast(message)
        logger.debug(f"Broadcasted {tx_type} {status}: {signature[:8]}...")
    
    async def broadcast_balance_update(self, balances: Dict[str, Any]):
        """
        Broadcast balance update.
        
        Args:
            balances: Balance information
        """
        message = {
            "type": "balance_update",
            "data": balances
        }
        await self.broadcast(message)
    
    async def broadcast_alert(self, alert_type: str, message_text: str, data: Dict = None):
        """
        Broadcast alert/notification.
        
        Args:
            alert_type: info, success, warning, error
            message_text: Alert message
            data: Optional additional data
        """
        message = {
            "type": "alert",
            "alert_type": alert_type,
            "message": message_text,
            "data": data or {}
        }
        await self.broadcast(message)
