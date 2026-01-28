import asyncio
import uvloop
from loguru import logger
import sys
from config import settings
from core.wallet_client import WalletClient
from core.trading_engine import TradingEngine
from core.wallet_info import WalletInfo
from core.api_server import ApiServer
from core.websocket_server import WebSocketServer
import uvicorn

# Use uvloop for higher performance
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

class WalletEngine:
    """
    Main orchestration engine for KekiusTrader_Wallet.
    Coordinates wallet, trading, and API services.
    """
    
    def __init__(self):
        self.wallet: Optional[WalletClient] = None
        self.trading_engine: Optional[TradingEngine] = None
        self.wallet_info: Optional[WalletInfo] = None
        self.api_server: Optional[ApiServer] = None
        self.websocket_server: Optional[WebSocketServer] = None
        self.running = False
    
    async def start(self):
        """Start all components"""
        logger.info(f"💼 Starting {settings.SERVICE_NAME}...")
        
        # Validate configuration
        if not settings.WALLET_PRIVATE_KEY:
            logger.error("❌ WALLET_PRIVATE_KEY not set in environment!")
            logger.error("Please set your private key in .env file")
            sys.exit(1)
        
        # Initialize wallet
        try:
            self.wallet = WalletClient()
            await self.wallet.connect()
        except Exception as e:
            logger.error(f"❌ Failed to initialize wallet: {e}")
            sys.exit(1)
        
        # Initialize trading engine
        self.trading_engine = TradingEngine(self.wallet)
        await self.trading_engine.initialize()
        
        # Initialize wallet info
        self.wallet_info = WalletInfo(self.wallet)
        
        # Initialize API server
        self.api_server = ApiServer(self.trading_engine, self.wallet_info)
        
        # Initialize WebSocket server
        self.websocket_server = WebSocketServer(self.api_server.app)
        
        self.running = True
        
        logger.info("✅ Wallet Engine initialized")
        logger.info(f"🔗 Wallet: {self.wallet.get_public_key_str()}")
        logger.info(f"⚡ Speed Mode: {settings.DEFAULT_SPEED_MODE}")
        logger.info(f"💎 Auto Profit Conversion: {settings.ENABLE_AUTO_PROFIT_CONVERSION}")
        
        if settings.COLD_WALLET_ADDRESS:
            logger.info(f"🏦 Cold Wallet: {settings.COLD_WALLET_ADDRESS}")
        
        # Start FastAPI server
        config = uvicorn.Config(
            self.api_server.app,
            host="0.0.0.0",
            port=settings.API_PORT,
            log_level="info"
        )
        server = uvicorn.Server(config)
        await server.serve()
    
    async def stop(self):
        """Graceful shutdown"""
        logger.info("Stopping Wallet Engine...")
        self.running = False
        
        if self.trading_engine:
            await self.trading_engine.close()
        
        if self.wallet:
            await self.wallet.close()
        
        logger.info("✅ Wallet Engine stopped")


# Make Optional available
from typing import Optional
