from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed, Finalized
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.pubkey import Pubkey
from solana.rpc.types import TxOpts
from loguru import logger
from typing import Optional, Dict, Any, List
import base58
import base64
from config import settings
import asyncio
from datetime import datetime
import aiohttp
import json

try:
    import httpx
except ImportError:
    httpx = None


def _is_rate_limit_error(exc: BaseException) -> bool:
    """True if the exception is HTTP 429 Too Many Requests (RPC rate limit)."""
    if exc is None:
        return False
    if httpx and isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429
    if "429" in str(exc) or "Too Many Requests" in str(exc):
        return True
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        return _is_rate_limit_error(cause)
    return False

# Solana native mint (SOL)
SOL_MINT = "So11111111111111111111111111111111111111112"

class WalletClient:
    """
    Solana wallet client for managing balance, tokens, and transactions.
    Optimized for ultra-low latency operations.
    """
    
    def __init__(self, private_key: Optional[str] = None):
        """
        Initialize wallet client.
        
        Args:
            private_key: Base58 encoded private key (from settings if not provided)
        """
        self.rpc_url = settings.SOLANA_RPC_URL
        self.client: Optional[AsyncClient] = None
        
        # Load wallet keypair
        pk = private_key or settings.WALLET_PRIVATE_KEY
        if not pk:
            raise ValueError("No private key provided! Set WALLET_PRIVATE_KEY in .env")
        
        try:
            # Decode base58 private key
            private_key_bytes = base58.b58decode(pk)
            self.keypair = Keypair.from_bytes(private_key_bytes)
            self.public_key = self.keypair.pubkey()
            logger.info(f"💼 Wallet loaded: {str(self.public_key)}")
        except Exception as e:
            raise ValueError(f"Invalid private key format: {e}")
        
        # Cache
        self._balance_cache: Dict[str, Any] = {}
        self._balance_cache_timestamp: float = 0
        self._token_accounts_cache: Optional[List[Dict]] = None
        self._token_accounts_cache_timestamp: float = 0
        self._token_metadata_cache: Dict[str, Dict[str, Any]] = {}  # mint -> {symbol, name}
        self._token_accounts_rate_limited_until: float = 0
        self._sol_balance_rate_limited_until: float = 0
    
    async def connect(self):
        """Connect to Solana RPC"""
        if not self.client:
            self.client = AsyncClient(self.rpc_url)
            logger.info(f"🔗 Connected to Solana RPC: {self.rpc_url}")
    
    async def close(self):
        """Close RPC connection"""
        if self.client:
            await self.client.close()
            logger.info("Wallet client closed")
    
    async def get_sol_balance(self, use_cache: bool = True) -> float:
        """
        Get SOL balance in SOL (not lamports).
        
        Args:
            use_cache: Use cached balance if available
        
        Returns:
            Balance in SOL
        """
        try:
            now_ts = datetime.now().timestamp()
            balance_cache_ts = float(getattr(self, "_balance_cache_timestamp", 0) or 0)
            # Check cache
            if use_cache and "SOL" in self._balance_cache:
                cache_age = now_ts - balance_cache_ts
                if cache_age < settings.WALLET_BALANCE_CACHE_TTL:
                    return self._balance_cache["SOL"]

            sol_balance_rate_limited_until = float(getattr(self, "_sol_balance_rate_limited_until", 0) or 0)
            if use_cache and now_ts < sol_balance_rate_limited_until:
                if "SOL" in self._balance_cache:
                    return self._balance_cache["SOL"]
                cooldown = sol_balance_rate_limited_until - now_ts
                logger.warning(f"⚠️ SOL balance RPC cooldown active for {cooldown:.1f}s; returning 0.0")
                return 0.0
            
            response = await self.client.get_balance(self.public_key)
            balance_lamports = response.value
            balance_sol = balance_lamports / 1e9
            
            # Update cache
            self._balance_cache["SOL"] = balance_sol
            self._balance_cache_timestamp = now_ts
            self._sol_balance_rate_limited_until = 0
            
            return balance_sol
        except Exception as e:
            if _is_rate_limit_error(e):
                now_ts = datetime.now().timestamp()
                self._sol_balance_rate_limited_until = now_ts + max(1, int(settings.RPC_RATE_LIMIT_COOLDOWN_SEC))
                if "SOL" in self._balance_cache:
                    logger.warning("⚠️ SOL balance RPC rate-limited; returning cached balance")
                    return self._balance_cache["SOL"]
            logger.error(f"Error getting SOL balance: {e}")
            return 0.0
    
    async def get_token_accounts(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Get all SPL token accounts for this wallet.
        
        Args:
            force_refresh: Force refresh cache
        
        Returns:
            List of token account info dicts
        """
        try:
            now_ts = datetime.now().timestamp()
            token_rate_limited_until = float(getattr(self, "_token_accounts_rate_limited_until", 0) or 0)
            token_cache_ttl = max(1, int(getattr(settings, "TOKEN_ACCOUNTS_CACHE_TTL", settings.WALLET_BALANCE_CACHE_TTL)))
            # Check cache - but if force_refresh is True, always fetch fresh data
            if not force_refresh and self._token_accounts_cache:
                cache_age = now_ts - float(getattr(self, "_token_accounts_cache_timestamp", 0) or 0)
                if cache_age < token_cache_ttl:
                    logger.info(f"📦 Using cached token accounts ({len(self._token_accounts_cache)} tokens, age: {cache_age:.1f}s)")
                    return self._token_accounts_cache
                else:
                    logger.info(f"🔄 Cache expired (age: {cache_age:.1f}s > TTL: {token_cache_ttl}s), fetching fresh data")

            if now_ts < token_rate_limited_until:
                if self._token_accounts_cache:
                    cooldown = token_rate_limited_until - now_ts
                    logger.warning(
                        f"⚠️ Token account RPC cooldown active for {cooldown:.1f}s; "
                        f"serving cache ({len(self._token_accounts_cache)} tokens)"
                    )
                    return self._token_accounts_cache
                cooldown = token_rate_limited_until - now_ts
                logger.warning(f"⚠️ Token account RPC cooldown active for {cooldown:.1f}s; returning empty list")
                return []
            
            # from solana.rpc.api import TokenAccountOpts
            from solana.rpc.types import TokenAccountOpts
            
            wallet_addr = self.get_public_key_str()
            logger.info(f"🔍 Fetching token accounts for wallet: {wallet_addr}")
            
            # Check if client is connected
            if not self.client:
                logger.error("❌ RPC client not connected!")
                await self.connect()
            
            rpc_endpoint = getattr(self.client, '_provider', None)
            if rpc_endpoint:
                rpc_uri = getattr(rpc_endpoint, 'endpoint_uri', 'Unknown')
                logger.info(f"Using RPC: {rpc_uri}")
            
            response = None
            last_error = None
            max_retries = 2
            retry_delays = [1.0, 2.0]
            for attempt in range(max_retries + 1):
                try:
                    logger.info(f"📡 Calling get_token_accounts_by_owner for wallet {wallet_addr}...")
                    response = await self.client.get_token_accounts_by_owner(
                        self.public_key,
                        TokenAccountOpts(
                            program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
                            encoding="jsonParsed",
                        )
                    )
                    logger.info(f"✅ RPC call successful")
                    break
                except Exception as e:
                    last_error = e
                    if _is_rate_limit_error(e) and attempt < max_retries:
                        delay = retry_delays[attempt]
                        logger.warning(f"⚠️ RPC rate limited (429), retry in {delay}s (attempt {attempt + 1}/{max_retries + 1})")
                        await asyncio.sleep(delay)
                        continue
                    if _is_rate_limit_error(e):
                        self._token_accounts_rate_limited_until = (
                            datetime.now().timestamp() + max(1, int(settings.RPC_RATE_LIMIT_COOLDOWN_SEC))
                        )
                        logger.error("❌ RPC rate limited (429) after retries. Use a paid RPC (SOLANA_RPC_URL) to avoid this.")
                        if self._token_accounts_cache:
                            logger.warning("📦 Returning cached token accounts due to rate limit")
                            return self._token_accounts_cache
                        return []
                    if self._token_accounts_cache:
                        logger.warning("📦 Returning cached token accounts due to RPC error")
                        return self._token_accounts_cache
                    logger.error(f"❌ RPC call failed: {type(e).__name__}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    return []
            
            if response is None:
                if self._token_accounts_cache:
                    logger.warning("📦 Returning cached token accounts because RPC response was None")
                    return self._token_accounts_cache
                return []
            
            if response.value is None:
                logger.warning("⚠️ RPC returned None for token accounts")
                if self._token_accounts_cache:
                    logger.warning("📦 Returning cached token accounts because RPC response value was None")
                    return self._token_accounts_cache
                return []
            
            logger.info(f"📦 RPC returned {len(response.value)} raw token account(s)")
            
            token_accounts = []
            
            # Log response structure for debugging
            logger.info(f"📦 Response type: {type(response)}")
            logger.info(f"📦 Response.value type: {type(response.value)}")
            logger.info(f"📦 Response.value is None: {response.value is None}")
            
            if response.value is None:
                logger.warning(f"⚠️ RPC returned None for token accounts - wallet may have no tokens")
                return []
            
            if len(response.value) == 0:
                logger.warning(f"⚠️ RPC returned empty list - wallet {wallet_addr} has no token accounts")
                return []
            
            logger.info(f"✅ Processing {len(response.value)} token account(s)...")
            
            for idx, account_info in enumerate(response.value):
                try:
                    logger.info(f"🔍 Processing token account {idx + 1}/{len(response.value)}")
                    pubkey = account_info.pubkey
                    account_data = account_info.account.data
                    data_length = "N/A"
                    if account_data is not None and isinstance(account_data, (bytes, bytearray, list, tuple, str)):
                        data_length = len(account_data)
                    logger.info(f"   Account pubkey: {pubkey}")
                    logger.info(f"   Data type: {type(account_data)}")
                    logger.info(f"   Data length: {data_length}")

                    parsed_info = None
                    parsed_amount = None
                    parsed = getattr(account_data, "parsed", None)
                    if parsed is not None:
                        parsed_info = getattr(parsed, "info", None)
                        parsed_amount = getattr(parsed_info, "token_amount", None)

                    mint_str = str(getattr(parsed_info, "mint", "") or "")
                    amount = int(getattr(parsed_amount, "amount", 0) or 0)
                    decimals = int(getattr(parsed_amount, "decimals", 9) or 9)
                    ui_amount = float(getattr(parsed_amount, "ui_amount", 0) or 0)

                    if not mint_str and account_data and isinstance(account_data, (bytes, bytearray, list)):
                        raw_data = bytes(account_data) if not isinstance(account_data, (bytes, bytearray)) else account_data
                        if len(raw_data) >= 72:
                            mint_pubkey = Pubkey.from_bytes(raw_data[:32])
                            mint_str = str(mint_pubkey)
                            amount = int.from_bytes(raw_data[64:72], byteorder='little')
                            decimals = 9
                            cached_meta = self._token_metadata_cache.get(mint_str)
                            if cached_meta and "decimals" in cached_meta:
                                decimals = int(cached_meta.get("decimals", 9) or 9)
                            else:
                                try:
                                    mint_info = await self.client.get_account_info(mint_pubkey)
                                    mint_data = getattr(getattr(mint_info, "value", None), "data", None)
                                    if mint_data and isinstance(mint_data, (bytes, bytearray, list)):
                                        mint_bytes = bytes(mint_data) if not isinstance(mint_data, (bytes, bytearray)) else mint_data
                                        if len(mint_bytes) > 44:
                                            decimals = int(mint_bytes[44])
                                except Exception as e:
                                    logger.debug(f"Could not fetch decimals for {mint_str[:8]}...: {e}")
                            ui_amount = amount / (10 ** decimals) if decimals > 0 else 0

                    if not mint_str:
                        logger.warning(f"⚠️ Token account {idx + 1} missing parsed mint data")
                        token_accounts.append({
                            "pubkey": str(pubkey),
                            "mint": "Unknown",
                            "balance": 0,
                            "ui_amount": 0,
                            "decimals": 0,
                            "symbol": "UNK",
                            "name": "Unknown Token"
                        })
                        continue

                    symbol = "UNK"
                    name = "Unknown Token"
                    try:
                        if mint_str in self._token_metadata_cache:
                            cache_age = datetime.now().timestamp() - self._token_metadata_cache[mint_str].get('_cache_time', 0)
                            if cache_age < settings.TOKEN_METADATA_CACHE_TTL:
                                cached = self._token_metadata_cache[mint_str]
                                symbol = cached.get("symbol", "UNK")
                                name = cached.get("name", "Unknown Token")
                    except Exception as e:
                        logger.debug(f"Error reading cache for {mint_str[:8]}...: {e}")

                    if symbol == "UNK" or name == "Unknown Token":
                        metadata_task = asyncio.create_task(self.get_token_metadata(mint_str))
                        metadata_task.add_done_callback(
                            lambda t, m=mint_str: (
                                None if t.cancelled() else
                                logger.debug(f"Metadata fetch failed for {m[:8]}...: {t.exception()}")
                                if t.exception() else None
                            )
                        )

                    display_symbol = symbol if symbol and symbol != "UNK" else mint_str[:8]
                    display_name = name if name and name != "Unknown Token" else f"Token {mint_str[:8]}..."

                    token_info = {
                        "pubkey": str(pubkey),
                        "mint": mint_str,
                        "balance": amount,
                        "ui_amount": ui_amount,
                        "decimals": decimals,
                        "symbol": display_symbol,
                        "name": display_name,
                        "data": account_data
                    }

                    token_accounts.append(token_info)
                    logger.info(f"✅ Added token: symbol={display_symbol}, name={display_name}, mint={mint_str[:8]}..., balance={ui_amount}, decimals={decimals}")
                except Exception as e:
                    logger.error(f"❌ Error parsing token account {idx + 1}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    # Add minimal info even if parsing fails
                    try:
                        token_accounts.append({
                            "pubkey": str(account_info.pubkey),
                            "mint": "Unknown",
                            "balance": 0,
                            "ui_amount": 0,
                            "decimals": 0,
                            "symbol": "UNK"
                        })
                        logger.debug(f"Added fallback entry for account {idx + 1}")
                    except Exception as e2:
                        logger.error(f"❌ Failed to add fallback entry: {e2}")
            
            self._token_accounts_cache = token_accounts
            self._token_accounts_cache_timestamp = now_ts
            self._token_accounts_rate_limited_until = 0
            
            logger.info(f"✅ Retrieved {len(token_accounts)} token accounts for wallet {self.get_public_key_str()[:8]}...")
            if token_accounts:
                logger.info(f"Sample token account: mint={token_accounts[0].get('mint', 'Unknown')[:8]}..., balance={token_accounts[0].get('ui_amount', 0)}")
            else:
                logger.warning(f"⚠️ No token accounts found for wallet {self.get_public_key_str()}")
            
            return token_accounts
            
        except Exception as e:
            logger.error(f"❌ Error getting token accounts: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Return empty list on error - don't crash
            return []
    
    async def get_token_balance(self, mint: str) -> Dict[str, Any]:
        """
        Get balance for a specific token.
        
        Args:
            mint: Token mint address
        
        Returns:
            Dict with balance info
        """
        try:
            # Special case for SOL
            if mint == SOL_MINT:
                balance = await self.get_sol_balance()
                return {
                    "mint": SOL_MINT,
                    "balance": balance,
                    "decimals": 9,
                    "ui_amount": balance
                }
            
            # Get token accounts
            accounts = await self.get_token_accounts()
            
            # Find matching token account
            for account in accounts:
                if account.get("mint") == mint:
                    return {
                        "mint": mint,
                        "balance": account.get("balance", 0),
                        "decimals": account.get("decimals", 0),
                        "ui_amount": account.get("ui_amount", 0),
                        "symbol": account.get("symbol", "UNK")
                    }
            
            # Token not found
            return {
                "mint": mint,
                "balance": 0,
                "decimals": 0,
                "ui_amount": 0,
                "symbol": "UNK"
            }
            
        except Exception as e:
            logger.error(f"Error getting token balance for {mint}: {e}")
            return {"mint": mint, "balance": 0, "decimals": 0, "ui_amount": 0}
    
    async def send_transaction(
        self,
        transaction_b64: str,
        max_retries: int = 3,
        skip_preflight: bool = True
    ) -> Optional[str]:
        """
        Sign and send a transaction.
        
        Args:
            transaction_b64: Base64 encoded transaction
            max_retries: Number of retry attempts
            skip_preflight: Skip preflight simulation (ULTRA_FAST mode)
        
        Returns:
            Transaction signature or None
        """
        try:
            # Decode transaction
            transaction_bytes = base64.b64decode(transaction_b64)
            transaction = VersionedTransaction.from_bytes(transaction_bytes)
            
            # Sign transaction
            transaction.sign([self.keypair])
            
            logger.debug(f"Sending transaction (skip_preflight={skip_preflight})...")
            
            # Send with retries
            for attempt in range(max_retries):
                try:
                    opts = TxOpts(
                        skip_preflight=skip_preflight,
                        preflight_commitment=Confirmed
                    )
                    
                    response = await self.client.send_transaction(
                        transaction,
                        opts=opts
                    )
                    
                    signature = str(response.value)
                    logger.info(f"✅ Transaction sent: {signature}")
                    return signature
                    
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Transaction attempt {attempt + 1} failed, retrying: {e}")
                        await asyncio.sleep(0.5)
                    else:
                        raise
            
            return None
            
        except Exception as e:
            logger.error(f"Error sending transaction: {e}")
            return None
    
    async def confirm_transaction(
        self,
        signature: str,
        timeout: int = 60
    ) -> bool:
        """
        Wait for transaction confirmation.
        
        Args:
            signature: Transaction signature
            timeout: Timeout in seconds
        
        Returns:
            True if confirmed, False otherwise
        """
        try:
            logger.debug(f"Waiting for confirmation: {signature}")
            
            loop = asyncio.get_running_loop()
            start_time = loop.time()
            
            while loop.time() - start_time < timeout:
                response = await self.client.get_signature_statuses([signature])
                
                if response.value and response.value[0]:
                    status = response.value[0]
                    confirmation_status = str(status.confirmation_status).lower()
                    if confirmation_status in ["confirmed", "finalized"]:
                        logger.info(f"✅ Transaction confirmed: {signature}")
                        return True
                    elif status.err:
                        logger.error(f"❌ Transaction failed: {status.err}")
                        return False
                
                await asyncio.sleep(1)
            
            logger.warning(f"⏱️ Transaction confirmation timeout: {signature}")
            return False
            
        except Exception as e:
            logger.error(f"Error confirming transaction: {e}")
            return False
    
    def get_public_key_str(self) -> str:
        """Get wallet public key as string"""
        return str(self.public_key)
    
    async def get_token_metadata(self, mint: str) -> Dict[str, Any]:
        """
        Fetch token metadata (symbol, name) from Jupiter API.
        
        Args:
            mint: Token mint address
            
        Returns:
            Dict with symbol, name, and other metadata
        """
        # Check cache first
        if mint in self._token_metadata_cache:
            cache_age = datetime.now().timestamp() - self._token_metadata_cache[mint].get('_cache_time', 0)
            if cache_age < settings.TOKEN_METADATA_CACHE_TTL:
                return self._token_metadata_cache[mint]
        
        try:
            # Fetch from Jupiter API - use search endpoint like Sniper service does
            async with aiohttp.ClientSession() as session:
                url = f"https://lite-api.jup.ag/tokens/v2/search?query={mint}"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                    'Accept': 'application/json'
                }
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Jupiter search returns a list, find exact match
                        if isinstance(data, list):
                            token_info = next((t for t in data if t.get('id') == mint), None)
                            if token_info:
                                metadata = {
                                    "symbol": token_info.get("symbol", "UNK"),
                                    "name": token_info.get("name", "Unknown Token"),
                                    "decimals": token_info.get("decimals", 9),
                                    "_cache_time": datetime.now().timestamp()
                                }
                                self._token_metadata_cache[mint] = metadata
                                logger.info(f"✅ Fetched metadata for {mint[:8]}...: {metadata.get('symbol')} ({metadata.get('name')})")
                                return metadata
                            else:
                                logger.warning(f"⚠️ No exact match found for {mint[:8]}... in Jupiter response (found {len(data)} results)")
                                if len(data) > 0:
                                    logger.debug(f"   First result: {data[0].get('id', 'N/A')[:8]}... (looking for {mint[:8]}...)")
                        else:
                            logger.warning(f"⚠️ Unexpected Jupiter API response format for {mint[:8]}...: {type(data)}")
                    else:
                        response_text = await response.text()
                        logger.warning(f"⚠️ Jupiter API returned {response.status} for {mint[:8]}...: {response_text[:200]}")
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Timeout fetching metadata for {mint[:8]}...")
        except Exception as e:
            logger.warning(f"❌ Error fetching metadata for {mint[:8]}...: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        
        # Return default if fetch failed
        default_metadata = {
            "symbol": "UNK",
            "name": "Unknown Token",
            "decimals": 9,
            "_cache_time": datetime.now().timestamp()
        }
        self._token_metadata_cache[mint] = default_metadata
        return default_metadata
    
    def clear_cache(self):
        """Clear balance and token account cache"""
        self._balance_cache.clear()
        self._token_accounts_cache = None
        self._balance_cache_timestamp = 0
        self._token_accounts_cache_timestamp = 0
        logger.debug("Wallet cache cleared")
