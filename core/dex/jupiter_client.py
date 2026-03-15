import aiohttp
import asyncio
import random
from loguru import logger
from typing import Optional, Dict, Any, List
from config import settings

class JupiterClient:
    """
    Jupiter Aggregator client for optimal swap routing.
    Provides best price discovery across Solana DEXes.
    """
    
    def __init__(self):
        self.api_url = settings.JUPITER_API_URL
        self.fallback_api_url = settings.JUPITER_API_FALLBACK_URL
        self.session: Optional[aiohttp.ClientSession] = None
        self.request_timeout = aiohttp.ClientTimeout(total=settings.JUPITER_HTTP_TIMEOUT_SECONDS)
    
    async def initialize(self):
        """Initialize HTTP session"""
        if not self.session or self.session.closed:
            headers = {}
            if settings.JUPITER_API_KEY:
                headers["x-api-key"] = settings.JUPITER_API_KEY
            self.session = aiohttp.ClientSession(
                timeout=self.request_timeout,
                headers=headers
            )
            logger.info("🪐 Jupiter client initialized")
    
    async def close(self):
        """Close HTTP session"""
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("Jupiter client closed")

    async def _ensure_session(self):
        """Ensure HTTP client session is initialized and open."""
        if not self.session or self.session.closed:
            await self.initialize()

    def _candidate_base_urls(self) -> List[str]:
        urls = []
        for raw in (self.api_url, self.fallback_api_url):
            url = (raw or "").strip().rstrip("/")
            if url and url not in urls:
                urls.append(url)
        return urls

    @staticmethod
    def _retry_after_seconds(response: aiohttp.ClientResponse) -> Optional[float]:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            parsed = float(value)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            return None
        return None

    async def _request_json_with_retries(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, str]] = None,
        payload: Optional[Dict[str, Any]] = None,
        operation_name: str = "request",
    ) -> Optional[Dict[str, Any]]:
        await self._ensure_session()
        base_urls = self._candidate_base_urls()
        if not base_urls:
            logger.error(f"Jupiter {operation_name} failed: no API URL configured")
            return None

        max_retries = max(1, int(settings.JUPITER_MAX_RETRIES))
        base_sleep = max(0.1, float(settings.JUPITER_BACKOFF_BASE_SECONDS))
        max_sleep = max(base_sleep, float(settings.JUPITER_BACKOFF_MAX_SECONDS))
        last_error_text = ""

        for attempt in range(1, max_retries + 1):
            base_url = base_urls[(attempt - 1) % len(base_urls)]
            url = f"{base_url}/{path.lstrip('/')}"
            try:
                if method.upper() == "GET":
                    req_ctx = self.session.get(url, params=params)
                else:
                    req_ctx = self.session.post(url, json=payload)

                async with req_ctx as response:
                    if response.status == 200:
                        return await response.json()

                    error_text = await response.text()
                    last_error_text = f"{response.status} - {error_text}"

                    if response.status == 429:
                        retry_after = self._retry_after_seconds(response)
                        if attempt < max_retries:
                            sleep_for = retry_after if retry_after is not None else min(
                                max_sleep, base_sleep * (2 ** (attempt - 1))
                            )
                            sleep_for += random.uniform(0, 0.35)
                            logger.warning(
                                f"Jupiter {operation_name} rate-limited (429), "
                                f"retrying in {sleep_for:.2f}s (attempt {attempt}/{max_retries}) via {base_url}"
                            )
                            await asyncio.sleep(sleep_for)
                            continue
                        logger.error(f"Jupiter {operation_name} failed: {last_error_text}")
                        return None

                    if response.status >= 500 and attempt < max_retries:
                        sleep_for = min(max_sleep, base_sleep * (2 ** (attempt - 1))) + random.uniform(0, 0.2)
                        logger.warning(
                            f"Jupiter {operation_name} server error {response.status}, "
                            f"retrying in {sleep_for:.2f}s (attempt {attempt}/{max_retries}) via {base_url}"
                        )
                        await asyncio.sleep(sleep_for)
                        continue

                    logger.error(f"Jupiter {operation_name} failed: {last_error_text}")
                    return None
            except Exception as e:
                last_error_text = str(e)
                if attempt < max_retries:
                    sleep_for = min(max_sleep, base_sleep * (2 ** (attempt - 1))) + random.uniform(0, 0.2)
                    logger.warning(
                        f"Jupiter {operation_name} request error, retrying in {sleep_for:.2f}s "
                        f"(attempt {attempt}/{max_retries}): {e}"
                    )
                    await asyncio.sleep(sleep_for)
                    continue
                logger.error(f"Error in Jupiter {operation_name}: {e}")
                return None

        logger.error(f"Jupiter {operation_name} exhausted retries: {last_error_text}")
        return None
    
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 100
    ) -> Optional[Dict[str, Any]]:
        """
        Get quote from Jupiter for a swap.
        
        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount: Amount in base units (lamports/token decimals)
            slippage_bps: Slippage tolerance in basis points (100 = 1%)
        
        Returns:
            Quote data with route information
        """
        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": str(slippage_bps),
                # aiohttp query params only accept str/int/float, so booleans
                # are serialized as lowercase strings for Jupiter.
                "onlyDirectRoutes": "false",  # Allow split routes for best price
                "asLegacyTransaction": "false"  # Use v0 transactions
            }

            quote = await self._request_json_with_retries(
                method="GET",
                path="quote",
                params=params,
                operation_name="quote",
            )
            if quote:
                logger.debug(f"Jupiter quote: {amount} → {quote.get('outAmount', 0)}")
            return quote
        except Exception as e:
            logger.error(f"Error getting Jupiter quote: {e}")
            return None
    
    async def get_swap_transaction(
        self,
        quote: Dict[str, Any],
        user_public_key: str,
        priority_fee: Optional[int] = None,
        dynamic_compute_units: bool = True
    ) -> Optional[str]:
        """
        Get swap transaction from Jupiter quote.
        
        Args:
            quote: Quote data from get_quote()
            user_public_key: User's wallet public key
            priority_fee: Priority fee in microlamports (optional)
            dynamic_compute_units: Enable dynamic compute unit calculation
        
        Returns:
            Base64 encoded transaction
        """
        try:
            payload = {
                "quoteResponse": quote,
                "userPublicKey": user_public_key,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": dynamic_compute_units,
                "asLegacyTransaction": False
            }
            
            # Add priority fee if specified for ULTRA_FAST mode
            if priority_fee is not None:
                payload["prioritizationFeeLamports"] = priority_fee

            swap_data = await self._request_json_with_retries(
                method="POST",
                path="swap",
                payload=payload,
                operation_name="swap",
            )
            if not swap_data:
                return None
            transaction = swap_data.get("swapTransaction")
            if transaction:
                logger.debug("✅ Jupiter swap transaction created")
            return transaction
        except Exception as e:
            logger.error(f"Error getting swap transaction: {e}")
            return None
    
    async def execute_swap(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        user_public_key: str,
        slippage_bps: int = 100,
        priority_fee: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Complete swap flow: Get quote + build transaction.
        Returns transaction ready to sign.
        
        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address  
            amount: Amount in base units
            user_public_key: Wallet public key
            slippage_bps: Slippage in basis points
            priority_fee: Priority fee in microlamports
        
        Returns:
            Dict with quote and transaction data
        """
        # Get quote
        quote = await self.get_quote(input_mint, output_mint, amount, slippage_bps)
        if not quote:
            logger.error("Failed to get Jupiter quote")
            return None
        
        # Get swap transaction
        transaction = await self.get_swap_transaction(
            quote, 
            user_public_key,
            priority_fee
        )
        
        if not transaction:
            logger.error("Failed to get swap transaction")
            return None
        
        return {
            "quote": quote,
            "transaction": transaction,
            "input_amount": amount,
            "output_amount": int(quote.get("outAmount", 0)),
            "price_impact": quote.get("priceImpactPct", 0)
        }


# Utility function for quick swaps
async def create_jupiter_swap(
    input_mint: str,
    output_mint: str,
    amount: int,
    wallet_pubkey: str,
    slippage_bps: int = 100,
    speed_mode: str = "ULTRA_FAST"
) -> Optional[Dict[str, Any]]:
    """
    Convenience function to create a Jupiter swap.
    """
    client = JupiterClient()
    await client.initialize()
    
    try:
        # Get priority fee based on speed mode
        priority_fee = None
        if speed_mode == "ULTRA_FAST":
            priority_fee = settings.PRIORITY_FEE_ULTRA_FAST
        elif speed_mode == "BALANCED":
            priority_fee = settings.PRIORITY_FEE_BALANCED
        elif speed_mode == "SAFE":
            priority_fee = settings.PRIORITY_FEE_SAFE
        
        result = await client.execute_swap(
            input_mint,
            output_mint,
            amount,
            wallet_pubkey,
            slippage_bps,
            priority_fee
        )
        return result
    finally:
        await client.close()
