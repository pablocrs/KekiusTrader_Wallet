from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    # Service Configuration
    SERVICE_NAME: str = "KekiusTrader_Wallet"
    LOG_LEVEL: str = "INFO"
    API_PORT: int = 8003
    
    # Solana Configuration
    # Use a paid RPC (Helius, QuickNode, Triton, etc.) in production to avoid 429 rate limits from the public RPC.
    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"
    SOLANA_WS_URL: str = "wss://api.mainnet-beta.solana.com"
    
    # Wallet Configuration
    # CRITICAL: Store private key securely! Use environment variables in production
    WALLET_PRIVATE_KEY: str = ""
    
    # Cold Wallet for Profit Storage
    COLD_WALLET_ADDRESS: str = ""
    
    # Trading Configuration
    DEFAULT_SLIPPAGE_BPS: int = 100  # 1% slippage
    MAX_SLIPPAGE_BPS: int = 500      # 5% hard cap safeguard
    DEFAULT_SPEED_MODE: str = "ULTRA_FAST"  # ULTRA_FAST, BALANCED, SAFE
    MIN_FEE_RESERVE_SOL: float = 0.005      # SOL reserve to avoid fee starvation
    
    # Priority Fees (in microlamports)
    PRIORITY_FEE_ULTRA_FAST: int = 100000  # 0.0001 SOL
    PRIORITY_FEE_BALANCED: int = 50000     # 0.00005 SOL
    PRIORITY_FEE_SAFE: int = 10000         # 0.00001 SOL
    
    # Compute Units
    COMPUTE_UNITS_LIMIT: int = 400000
    
    # DEX Configuration
    DEX_ROUTER: str = "JUPITER"  # JUPITER, RAYDIUM, PUMPFUN
    JUPITER_API_URL: str = "https://lite-api.jup.ag/swap/v1"
    JUPITER_API_FALLBACK_URL: str = "https://api.jup.ag/swap/v1"
    JUPITER_API_KEY: str = ""
    JUPITER_MAX_RETRIES: int = 5
    JUPITER_BACKOFF_BASE_SECONDS: float = 0.75
    JUPITER_BACKOFF_MAX_SECONDS: float = 8.0
    
    # Auto USDC Profit Conversion
    ENABLE_AUTO_PROFIT_CONVERSION: bool = True
    PROFIT_THRESHOLD_SOL: float = 0.1  # Min SOL profit to trigger conversion
    SOL_TO_USDC_MIN_RESERVE_SOL: float = 0.02  # Keep this SOL reserve when converting balance
    USDC_MINT: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC mint address
    
    # Cold Wallet Transfer
    AUTO_TRANSFER_TO_COLD_WALLET: bool = True
    TRANSFER_THRESHOLD_USDC: float = 10.0  # Min USDC to transfer to cold wallet
    
    # Cache Configuration
    TOKEN_METADATA_CACHE_TTL: int = 3600  # 1 hour
    TOKEN_PRICE_CACHE_TTL: int = 30       # seconds
    WALLET_BALANCE_CACHE_TTL: int = 20    # seconds
    TOKEN_ACCOUNTS_CACHE_TTL: int = 30    # seconds
    RPC_RATE_LIMIT_COOLDOWN_SEC: int = 45 # seconds

    # Monitoring
    ENABLE_WEBSOCKET_BROADCAST: bool = True
    PING_INTERVAL: int = 30  # WebSocket ping interval
    CORS_ALLOWED_ORIGINS: List[str] = Field(default_factory=lambda: ["*"])
    JUPITER_HTTP_TIMEOUT_SECONDS: int = 30
    JUPITER_PRICE_API_URL: str = "https://lite-api.jup.ag/price/v3"
    DEXSCREENER_TOKENS_URL: str = "https://api.dexscreener.com/latest/dex/tokens"
    
settings = Settings()
