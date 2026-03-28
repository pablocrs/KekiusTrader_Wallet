from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from loguru import logger
from typing import Optional, Dict, Any
from config import settings

class BuyRequest(BaseModel):
    """Buy token request"""
    mint: str = Field(..., description="Token mint address to buy")
    amount: Optional[float] = Field(None, gt=0, description="Amount of spend asset to use")
    amount_sol: Optional[float] = Field(None, gt=0, description="Deprecated SOL amount field (backward-compatible)")
    spend_denom: str = Field("SOL", description="Spend denomination: SOL or USDC")
    slippage_bps: Optional[int] = Field(None, gt=0, le=500, description="Slippage in basis points (max 500 = 5%)")
    speed_mode: Optional[str] = Field(None, description="ULTRA_FAST, BALANCED, or SAFE")

class SellRequest(BaseModel):
    """Sell token request"""
    mint: str = Field(..., description="Token mint address to sell")
    amount: int = Field(..., gt=0, description="Amount of tokens to sell (base units)")
    slippage_bps: Optional[int] = Field(None, gt=0, le=500, description="Slippage in basis points (max 500 = 5%)")
    speed_mode: Optional[str] = Field(None, description="Speed mode")

class SellAllRequest(BaseModel):
    """Sell all tokens request"""
    mint: str = Field(..., description="Token mint address to sell completely")
    slippage_bps: Optional[int] = Field(None, gt=0, le=500, description="Slippage in basis points (max 500 = 5%)")
    speed_mode: Optional[str] = Field(None, description="Speed mode")


class ConvertSolRequest(BaseModel):
    """SOL to USDC conversion request"""
    amount_sol: Optional[float] = Field(
        None,
        gt=0,
        description="Exact SOL amount to convert. If omitted, converts all SOL above min_reserve_sol.",
    )
    min_reserve_sol: Optional[float] = Field(
        None,
        ge=0,
        description="Minimum SOL to keep in wallet when amount_sol is omitted.",
    )

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
        allow_origins = ["*"]
        allow_credentials = False

        # CORS spec forbids wildcard origins when credentials are enabled.
        if settings.CORS_ALLOWED_ORIGINS:
            allow_origins = settings.CORS_ALLOWED_ORIGINS
            allow_credentials = "*" not in allow_origins

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_credentials=allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    def setup_routes(self):
        """Setup API routes"""
        def _http_status_for_trade_error(error_message: str) -> int:
            message = (error_message or "").lower()
            for marker in ("invalid", "insufficient", "must be", "cannot", "exceeds max", "no balance"):
                if marker in message:
                    return 400
            return 500
        
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
            Buy tokens using SOL or USDC via Jupiter Aggregator.
            
            Ultra-low latency execution with configurable speed mode.
            """
            try:
                logger.info(f"API: BUY request for {request.mint}")
                if request.amount is not None and request.amount_sol is not None:
                    if abs(float(request.amount) - float(request.amount_sol)) > 1e-12:
                        raise HTTPException(status_code=400, detail="amount and amount_sol mismatch")
                buy_amount = request.amount if request.amount is not None else request.amount_sol
                if buy_amount is None:
                    raise HTTPException(status_code=400, detail="amount is required (or legacy amount_sol)")
                spend_denom = str(request.spend_denom or "SOL").strip().upper()
                if spend_denom not in {"SOL", "USDC"}:
                    raise HTTPException(status_code=400, detail="spend_denom must be SOL or USDC")
                
                result = await self.trading_engine.buy(
                    mint=request.mint,
                    amount=float(buy_amount),
                    spend_denom=spend_denom,
                    slippage_bps=request.slippage_bps,
                    speed_mode=request.speed_mode
                )
                
                if not result or not result.get("success", False):
                    error_message = (result or {}).get("error", "Buy operation failed")
                    raise HTTPException(status_code=_http_status_for_trade_error(error_message), detail=error_message)
                
                return {
                    "success": True,
                    "data": result,
                    "message": f"Buy order submitted. Signature: {result['signature']}"
                }
                
            except HTTPException:
                raise
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
                
                if not result or not result.get("success", False):
                    error_message = (result or {}).get("error", "Sell operation failed")
                    raise HTTPException(status_code=_http_status_for_trade_error(error_message), detail=error_message)
                
                return {
                    "success": True,
                    "data": result,
                    "message": f"Sell order submitted. Signature: {result['signature']}"
                }
                
            except HTTPException:
                raise
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
                if not result.get("success", False):
                    error_message = result.get("error", "Sell-all operation failed")
                    raise HTTPException(status_code=_http_status_for_trade_error(error_message), detail=error_message)
                
                return {
                    "success": True,
                    "data": result,
                    "message": f"Sell-all order submitted. Signature: {result['signature']}"
                }
                
            except HTTPException:
                raise
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

        @self.app.post("/convert/sol-to-usdc")
        async def convert_sol_balance(request: ConvertSolRequest):
            """
            Convert SOL balance to USDC.

            - With amount_sol: converts exact SOL amount.
            - Without amount_sol: converts all SOL above min_reserve_sol.
            """
            try:
                result = await self.trading_engine.convert_sol_to_usdc(
                    amount_sol=request.amount_sol,
                    min_reserve_sol=request.min_reserve_sol,
                )

                if not result or not result.get("success", False):
                    detail = (result or {}).get("error", "Conversion failed")
                    raise HTTPException(status_code=400, detail=detail)

                return {
                    "success": True,
                    "data": result,
                    "message": "SOL to USDC conversion initiated",
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"API error in /convert/sol-to-usdc: {e}")
                raise HTTPException(status_code=500, detail=str(e))
