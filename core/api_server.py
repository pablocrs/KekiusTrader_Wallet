from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from loguru import logger
from typing import Optional, Dict, Any
from config import settings

class BuyRequest(BaseModel):
    """Buy token request"""
    mint: str = Field(..., description="Token mint address to buy")
    amount_sol: float = Field(..., gt=0, description="Amount of SOL to spend")
    slippage_bps: Optional[int] = Field(None, ge=0, le=10000, description="Slippage in basis points (100 = 1%)")
    speed_mode: Optional[str] = Field(None, description="ULTRA_FAST, BALANCED, or SAFE")

class SellRequest(BaseModel):
    """Sell token request"""
    mint: str = Field(..., description="Token mint address to sell")
    amount: int = Field(..., gt=0, description="Amount of tokens to sell (base units)")
    slippage_bps: Optional[int] = Field(None, ge=0, le=10000, description="Slippage in basis points")
    speed_mode: Optional[str] = Field(None, description="Speed mode")

class SellAllRequest(BaseModel):
    """Sell all tokens request"""
    mint: str = Field(..., description="Token mint address to sell completely")
    slippage_bps: Optional[int] = Field(None, ge=0, le=10000, description="Slippage in basis points")
    speed_mode: Optional[str] = Field(None, description="Speed mode")

class ApiServer:
    """
    REST API server for wallet operations.
    Provides high-speed trading endpoints.
    """
    
    def __init__(self, trading_engine, wallet_info):
        """
        Args:
            trading_engine: TradingEngine instance
            wallet_info: WalletInfo instance
        """
        self.app = FastAPI(
            title="KekiusTrader_Wallet",
            version="1.0.0",
            description="Ultra-low latency Solana wallet trading service"
        )
        self.trading_engine = trading_engine
        self.wallet_info = wallet_info
        
        self.setup_cors()
        self.setup_routes()
    
    def setup_cors(self):
        """Configure CORS"""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    def setup_routes(self):
        """Setup API routes"""
        
        @self.app.get("/health")
        async def health_check():
            """Health check endpoint"""
            return {
                "status": "healthy",
                "service": settings.SERVICE_NAME,
                "wallet": self.wallet_info.wallet.get_public_key_str(),
                "features": {
                    "auto_profit_conversion": settings.ENABLE_AUTO_PROFIT_CONVERSION,
                    "cold_wallet_transfer": settings.AUTO_TRANSFER_TO_COLD_WALLET
                }
            }
        
        @self.app.post("/buy")
        async def buy_token(request: BuyRequest):
            """
            Buy tokens with SOL via Jupiter Aggregator.
            
            Ultra-low latency execution with configurable speed mode.
            """
            try:
                logger.info(f"API: BUY request for {request.mint}")
                
                result = await self.trading_engine.buy(
                    mint=request.mint,
                    amount_sol=request.amount_sol,
                    slippage_bps=request.slippage_bps,
                    speed_mode=request.speed_mode
                )
                
                if not result:
                    raise HTTPException(status_code=500, detail="Buy operation failed")
                
                return {
                    "success": True,
                    "data": result,
                    "message": f"Buy order submitted. Signature: {result['signature']}"
                }
                
            except Exception as e:
                logger.error(f"API error in /buy: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/sell")
        async def sell_token(request: SellRequest):
            """
            Sell tokens for SOL via Jupiter Aggregator.
            
            Automatically triggers profit conversion to USDC if enabled.
            """
            try:
                logger.info(f"API: SELL request for {request.mint}")
                
                result = await self.trading_engine.sell(
                    mint=request.mint,
                    amount=request.amount,
                    slippage_bps=request.slippage_bps,
                    speed_mode=request.speed_mode
                )
                
                if not result:
                    raise HTTPException(status_code=500, detail="Sell operation failed")
                
                return {
                    "success": True,
                    "data": result,
                    "message": f"Sell order submitted. Signature: {result['signature']}"
                }
                
            except Exception as e:
                logger.error(f"API error in /sell: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/sell-all")
        async def sell_all_tokens(request: SellAllRequest):
            """
            Sell entire token balance for SOL.
            
            Liquidates complete position and triggers profit conversion.
            """
            try:
                logger.info(f"API: SELL-ALL request for {request.mint}")
                
                result = await self.trading_engine.sell_all(
                    mint=request.mint,
                    slippage_bps=request.slippage_bps,
                    speed_mode=request.speed_mode
                )
                
                if not result:
                    raise HTTPException(
                        status_code=400,
                        detail="Sell-all operation failed (possibly no balance)"
                    )
                
                return {
                    "success": True,
                    "data": result,
                    "message": f"Sell-all order submitted. Signature: {result['signature']}"
                }
                
            except Exception as e:
                logger.error(f"API error in /sell-all: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/wallet/info")
        async def get_wallet_info():
            """
            Get complete wallet information.
            
            Returns SOL balance and all token holdings.
            """
            try:
                info = await self.wallet_info.get_full_wallet_info()
                return {
                    "success": True,
                    "data": info
                }
            except Exception as e:
                logger.error(f"API error in /wallet/info: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/wallet/balance")
        async def get_balance_summary():
            """
            Get quick balance summary (SOL only).
            
            Ultra-fast endpoint for balance checks.
            """
            try:
                summary = await self.wallet_info.get_balance_summary()
                return {
                    "success": True,
                    "data": summary
                }
            except Exception as e:
                logger.error(f"API error in /wallet/balance: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/wallet/balance/{mint}")
        async def get_token_balance(mint: str):
            """
            Get balance for a specific token.
            
            Args:
                mint: Token mint address
            """
            try:
                balance = await self.wallet_info.get_token_balance_info(mint)
                return {
                    "success": True,
                    "data": balance
                }
            except Exception as e:
                logger.error(f"API error in /wallet/balance/{mint}: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/profit/convert")
        async def convert_profit():
            """
            Manually trigger profit conversion to USDC.
            
            Converts SOL profits above threshold to USDC.
            """
            try:
                result = await self.trading_engine.convert_profit_to_usdc()
                
                if not result:
                    return {
                        "success": False,
                        "message": "No profit to convert or below threshold"
                    }
                
                return {
                    "success": True,
                    "data": result,
                    "message": "Profit conversion initiated"
                }
            except Exception as e:
                logger.error(f"API error in /profit/convert: {e}")
                raise HTTPException(status_code=500, detail=str(e))
