import os
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Service Configuration
    SERVICE_NAME: str = "KekiusTrader_Wallet"
    LOG_LEVEL: str = "INFO"
    API_PORT: int = 8003
    
    # Solana Configuration
    SOLANA_RPC_URL: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    SOLANA_WS_URL: str = os.getenv("SOLANA_WS_URL", "wss://api.mainnet-beta.solana.com")
    
    # Wallet Configuration
    # CRITICAL: Store private key securely! Use environment variables in production
    WALLET_PRIVATE_KEY: str = os.getenv("WALLET_PRIVATE_KEY", "")
    
    # Cold Wallet for Profit Storage
    COLD_WALLET_ADDRESS: str = os.getenv("COLD_WALLET_ADDRESS", "")
    
    # Trading Configuration
    DEFAULT_SLIPPAGE_BPS: int = 100  # 1% slippage
    DEFAULT_SPEED_MODE: str = "ULTRA_FAST"  # ULTRA_FAST, BALANCED, SAFE
    
    # Priority Fees (in microlamports)
    PRIORITY_FEE_ULTRA_FAST: int = 100000  # 0.0001 SOL
    PRIORITY_FEE_BALANCED: int = 50000     # 0.00005 SOL
    PRIORITY_FEE_SAFE: int = 10000         # 0.00001 SOL
    
    # Compute Units
    COMPUTE_UNITS_LIMIT: int = 400000
    
    # DEX Configuration
    DEX_ROUTER: str = "JUPITER"  # JUPITER, RAYDIUM, PUMPFUN
    JUPITER_API_URL: str = "https://quote-api.jup.ag/v6"
    
    # Auto USDC Profit Conversion
    ENABLE_AUTO_PROFIT_CONVERSION: bool = True
    PROFIT_THRESHOLD_SOL: float = 0.1  # Min SOL profit to trigger conversion
    USDC_MINT: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC mint address
    
    # Cold Wallet Transfer
    AUTO_TRANSFER_TO_COLD_WALLET: bool = True
    TRANSFER_THRESHOLD_USDC: float = 10.0  # Min USDC to transfer to cold wallet
    
    # Cache Configuration
    TOKEN_METADATA_CACHE_TTL: int = 3600  # 1 hour
    WALLET_BALANCE_CACHE_TTL: int = 5     # 5 seconds
    
    # Monitoring
    ENABLE_WEBSOCKET_BROADCAST: bool = True
    PING_INTERVAL: int = 30  # WebSocket ping interval
    
    class Config:
        env_file = ".env"

settings = Settings()
